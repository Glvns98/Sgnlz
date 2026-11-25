"""Async Deriv WebSocket API client with heartbeat, rate-limiting, reconnects, and malformed-message safety."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import websockets
from websockets.asyncio.client import ClientConnection

from bot.config.settings import DerivSettings

logger = logging.getLogger(__name__)

SubscriptionCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class DerivAPIError(RuntimeError):
    """Structured error returned by Deriv WebSocket API."""

    def __init__(self, code: str, message: str, msg_type: str | None = None) -> None:
        self.code = code
        self.message = message
        self.msg_type = msg_type
        super().__init__(f"DerivAPIError({code}): {message}")


class TokenBucketRateLimiter:
    """Async token-bucket limiter to protect Deriv WebSocket rate quotas."""

    def __init__(self, rate_per_sec: float, capacity: float | None = None) -> None:
        self.rate = max(0.5, rate_per_sec)
        self.capacity = capacity if capacity is not None else max(2.0, self.rate * 2.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_sec = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait_sec)


class DerivAPIClient:
    """Resilient async WebSocket transport separated cleanly from strategy logic."""

    def __init__(
        self,
        settings: DerivSettings,
        *,
        on_disconnect: Callable[[str], Awaitable[None] | None] | None = None,
        on_reconnect: Callable[[], Awaitable[None] | None] | None = None,
        on_malformed_message: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.settings = settings
        self._ws: ClientConnection | None = None
        self._connected: bool = False
        self._authorized: bool = False
        self._closing: bool = False
        self._req_seq: int = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._subscriptions: dict[str, SubscriptionCallback] = {}
        self._sub_payloads: list[dict[str, Any]] = []
        self._rate_limiter = TokenBucketRateLimiter(settings.max_requests_per_sec)
        self._recv_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._reconnect_attempts: int = 0
        self._recent_errors: deque[str] = deque(maxlen=50)
        self._on_disconnect = on_disconnect
        self._on_reconnect = on_reconnect
        self._on_malformed_message = on_malformed_message
        self.rest_account: dict[str, Any] | None = None

    @property
    def is_v1_options_api(self) -> bool:
        return "api.derivws.com/trading/v1" in self.settings.endpoint

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def is_authorized(self) -> bool:
        return self._authorized

    async def connect(self) -> None:
        self._closing = False
        await self._open_socket()
        self._recv_task = asyncio.create_task(self._receive_loop(), name="deriv-recv")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="deriv-ping")

    async def _open_socket(self) -> None:
        headers = {"Deriv-App-ID": self.settings.app_id} if self.settings.app_id else None
        self._ws = await websockets.connect(
            self.settings.ws_url,
            additional_headers=headers,
            ping_interval=None,
            close_timeout=5.0,
        )
        self._connected = True
        self._reconnect_attempts = 0
        logger.info("Connected to Deriv WebSocket endpoint (%s)", self.settings.endpoint)

    async def authorize(self) -> dict[str, Any] | None:
        if not self.settings.token:
            logger.info("No DERIV_API_TOKEN configured; operating in public market-data mode")
            self._authorized = False
            return None
        if self.is_v1_options_api:
            if not self.settings.app_id:
                logger.warning("DERIV_APP_ID missing for v1 REST authorization; staying in public mode")
                self._authorized = False
                return None
            url = f"{self.settings.rest_base_url.rstrip('/')}/accounts"
            headers = {
                "Authorization": f"Bearer {self.settings.token}",
                "Deriv-App-ID": self.settings.app_id,
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise DerivAPIError(str(resp.status), f"REST authorize failed: {body[:200]}")
                    payload = await resp.json()
            accounts = payload.get("data", []) if isinstance(payload, dict) else []
            active_demo = next(
                (a for a in accounts if a.get("account_type") == "demo" and a.get("status") == "active"),
                None,
            )
            chosen = active_demo or next((a for a in accounts if a.get("status") == "active"), None)
            if chosen is not None:
                self.rest_account = chosen
                self._authorized = True
                logger.info(
                    "Authorized Deriv v1 account %s (%s)",
                    chosen.get("account_id"),
                    chosen.get("account_type"),
                )
                return {"authorize": chosen}
            self._authorized = False
            return None

        resp = await self.request({"authorize": self.settings.token})
        self._authorized = True
        return resp

    async def close(self) -> None:
        self._closing = True
        self._connected = False
        for task in (self._ping_task, self._recv_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._fail_pending(ConnectionError("DerivAPIClient closed"))

    async def request(self, payload: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        if not self.is_connected or self._ws is None:
            raise ConnectionError("Deriv WebSocket is not connected")

        await self._rate_limiter.acquire()
        self._req_seq += 1
        req_id = self._req_seq
        msg = dict(payload)
        msg["req_id"] = req_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(json.dumps(msg))
            resp = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

        if "error" in resp:
            err = resp["error"]
            raise DerivAPIError(
                code=str(err.get("code", "UnknownError")),
                message=str(err.get("message", "Unknown Deriv API error")),
                msg_type=resp.get("msg_type"),
            )
        return resp

    async def subscribe(
        self,
        payload: dict[str, Any],
        callback: SubscriptionCallback,
        *,
        remember: bool = True,
    ) -> dict[str, Any]:
        sub_req = dict(payload)
        sub_req["subscribe"] = 1
        resp = await self.request(sub_req)
        sub_info = resp.get("subscription", {})
        sub_id = sub_info.get("id")
        if sub_id:
            self._subscriptions[str(sub_id)] = callback
        if remember:
            self._sub_payloads.append((dict(payload), callback))  # type: ignore[arg-type]
        return resp

    async def _ping_loop(self) -> None:
        while not self._closing:
            try:
                await asyncio.sleep(self.settings.ping_interval_sec)
                if self.is_connected:
                    await self.request({"ping": 1}, timeout=10.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Heartbeat ping failed: %s", exc)

    async def _receive_loop(self) -> None:
        while not self._closing:
            try:
                if self._ws is None or not self._connected:
                    await self._reconnect()
                    continue
                raw = await self._ws.recv()
                await self._handle_raw_message(raw)
            except asyncio.CancelledError:
                break
            except websockets.ConnectionClosed as exc:
                self._connected = False
                self._authorized = False
                self._fail_pending(exc)
                logger.warning("Deriv WebSocket disconnected: %s", exc)
                if self._on_disconnect:
                    await self._maybe_await(self._on_disconnect(str(exc)))
                if not self._closing:
                    await self._reconnect()
            except Exception as exc:
                self._connected = False
                self._authorized = False
                self._fail_pending(exc)
                logger.exception("Unexpected error in Deriv receive loop")
                if not self._closing:
                    await self._reconnect()

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Payload is not a JSON object")
        except Exception as exc:
            msg = f"Malformed message from Deriv WS: {exc}"
            self._recent_errors.append(msg)
            logger.warning(msg)
            if self._on_malformed_message:
                await self._maybe_await(self._on_malformed_message(msg))
            return

        req_id = data.get("req_id")
        if req_id is not None and int(req_id) in self._pending:
            fut = self._pending[int(req_id)]
            if not fut.done():
                fut.set_result(data)

        sub_id = (data.get("subscription") or {}).get("id")
        if sub_id and str(sub_id) in self._subscriptions:
            cb = self._subscriptions[str(sub_id)]
            try:
                await self._maybe_await(cb(data))
            except Exception:
                logger.exception("Error inside subscription callback %s", sub_id)

    async def _reconnect(self) -> None:
        while not self._closing:
            self._reconnect_attempts += 1
            delay = min(
                self.settings.reconnect_max_sec,
                self.settings.reconnect_base_sec * (2 ** min(self._reconnect_attempts - 1, 6)),
            )
            logger.info("Reconnecting to Deriv WS in %.1fs (attempt %d)...", delay, self._reconnect_attempts)
            await asyncio.sleep(delay)
            try:
                await self._open_socket()
                if self.settings.token:
                    await self.authorize()
                # Re-subscribe stored streams
                old_subs = list(self._sub_payloads)
                self._subscriptions.clear()
                self._sub_payloads.clear()
                for item in old_subs:
                    payload, cb = item  # type: ignore[misc]
                    await self.subscribe(payload, cb, remember=True)
                if self._on_reconnect:
                    await self._maybe_await(self._on_reconnect())
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Reconnect attempt %d failed: %s", self._reconnect_attempts, exc)

    def _fail_pending(self, exc: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    @staticmethod
    async def _maybe_await(val: Any) -> None:
        if asyncio.iscoroutine(val):
            await val
