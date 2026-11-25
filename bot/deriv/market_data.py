"""Market Data Engine: historical candle hydration, strict closed-bar anti-repainting, and gap checks."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from bot.core.models import Candle
from bot.deriv.client import DerivAPIClient

logger = logging.getLogger(__name__)

ClosedCandleCallback = Callable[[Candle], Awaitable[None] | None]


class MarketDataEngine:
    """Maintains rolling OHLC buffers and emits strictly closed candles without repainting."""

    def __init__(
        self,
        client: DerivAPIClient,
        *,
        buffer_size: int = 500,
        max_clock_drift_sec: int = 120,
    ) -> None:
        self.client = client
        self.buffer_size = buffer_size
        self.max_clock_drift_sec = max_clock_drift_sec
        self._buffers: dict[tuple[str, int], deque[Candle]] = {}
        self._active_forming: dict[tuple[str, int], Candle] = {}
        self._last_closed_epoch: dict[tuple[str, int], int] = {}
        self.gap_count: int = 0
        self.duplicate_count: int = 0

    def get_candles(self, symbol: str, timeframe_sec: int) -> list[Candle]:
        return list(self._buffers.get((symbol, timeframe_sec), ()))

    def last_closed_epoch(self, symbol: str, timeframe_sec: int) -> int | None:
        return self._last_closed_epoch.get((symbol, timeframe_sec))

    async def fetch_new_closed_candles(
        self,
        symbol: str,
        timeframe_sec: int,
        count: int = 5,
    ) -> list[Candle]:
        """Poll ticks_history and return strictly newly closed candles without repainting."""
        resp = await self.client.request(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "granularity": timeframe_sec,
                "style": "candles",
            }
        )
        raw_candles = resp.get("candles", [])
        key = (symbol, timeframe_sec)
        buf = self._buffers.setdefault(key, deque(maxlen=self.buffer_size))
        prev_epoch = self._last_closed_epoch.get(key, -1)
        now_epoch = int(datetime.now(UTC).timestamp())
        newly_closed: list[Candle] = []

        for idx, item in enumerate(raw_candles):
            epoch = int(item["epoch"])
            if epoch <= prev_epoch:
                continue
            is_last = idx == len(raw_candles) - 1
            is_closed = (not is_last) or (now_epoch >= epoch + timeframe_sec)
            if not is_closed:
                continue
            candle = Candle(
                symbol=symbol,
                timeframe_sec=timeframe_sec,
                epoch=epoch,
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                is_closed=True,
            )
            buf.append(candle)
            self._last_closed_epoch[key] = epoch
            prev_epoch = epoch
            newly_closed.append(candle)
        return newly_closed

    async def fetch_latest_price_bar(self, symbol: str, timeframe_sec: int = 900) -> Candle | None:
        """Fetch the active forming candle for intra-bar position monitoring without mutating closed history."""
        resp = await self.client.request(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 1,
                "end": "latest",
                "granularity": timeframe_sec,
                "style": "candles",
            }
        )
        raw_candles = resp.get("candles", [])
        if not raw_candles:
            return None
        item = raw_candles[-1]
        return Candle(
            symbol=symbol,
            timeframe_sec=timeframe_sec,
            epoch=int(item["epoch"]),
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            is_closed=False,
        )

    async def bootstrap_history(
        self,
        symbol: str,
        timeframe_sec: int,
        count: int = 250,
    ) -> list[Candle]:
        """Fetch historical OHLC candles via Deriv ticks_history."""
        resp = await self.client.request(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "granularity": timeframe_sec,
                "style": "candles",
            }
        )
        raw_candles = resp.get("candles", [])
        key = (symbol, timeframe_sec)
        buf = self._buffers.setdefault(key, deque(maxlen=self.buffer_size))
        buf.clear()

        # Treat all but the potentially forming latest candle as closed
        candles: list[Candle] = []
        now_epoch = int(datetime.now(UTC).timestamp())
        for idx, item in enumerate(raw_candles):
            epoch = int(item["epoch"])
            is_last = idx == len(raw_candles) - 1
            is_closed = (not is_last) or (now_epoch >= epoch + timeframe_sec)
            candle = Candle(
                symbol=symbol,
                timeframe_sec=timeframe_sec,
                epoch=epoch,
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                is_closed=is_closed,
            )
            if is_closed:
                buf.append(candle)
                self._last_closed_epoch[key] = epoch
                candles.append(candle)
            else:
                self._active_forming[key] = candle
        return candles

    async def subscribe_candles(
        self,
        symbol: str,
        timeframe_sec: int,
        on_closed_candle: ClosedCandleCallback,
    ) -> None:
        """Subscribe to live OHLC stream and emit only finalized closed candles."""

        async def _handle_stream(msg: dict[str, Any]) -> None:
            ohlc = msg.get("ohlc")
            if not isinstance(ohlc, dict):
                return
            candle = self.ingest_stream_ohlc(symbol, timeframe_sec, ohlc)
            if candle is not None:
                res = on_closed_candle(candle)
                if res is not None and hasattr(res, "__await__"):
                    await res

        await self.client.subscribe(
            {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": 2,
                "end": "latest",
                "granularity": timeframe_sec,
                "style": "candles",
            },
            _handle_stream,
        )

    def ingest_stream_ohlc(
        self,
        symbol: str,
        timeframe_sec: int,
        ohlc: dict[str, Any],
    ) -> Candle | None:
        """Process one incoming Deriv OHLC tick; returns a Candle ONLY when a bar closes."""
        key = (symbol, timeframe_sec)
        try:
            raw_epoch = ohlc.get("open_time")
            if raw_epoch is None:
                raw_epoch = ohlc["epoch"]
            open_time = int(raw_epoch)
            open_px = float(ohlc["open"])
            high_px = float(ohlc["high"])
            low_px = float(ohlc["low"])
            close_px = float(ohlc["close"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring malformed OHLC payload for %s: %s", symbol, ohlc)
            return None

        last_closed = self._last_closed_epoch.get(key)
        if last_closed is not None and open_time <= last_closed:
            self.duplicate_count += 1
            return None

        forming = self._active_forming.get(key)
        incoming = Candle(
            symbol=symbol,
            timeframe_sec=timeframe_sec,
            epoch=open_time,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            is_closed=False,
        )

        # When open_time advances past the currently forming candle, the previous forming bar is closed
        if forming is not None and open_time > forming.epoch:
            closed = Candle(
                symbol=forming.symbol,
                timeframe_sec=forming.timeframe_sec,
                epoch=forming.epoch,
                open=forming.open,
                high=forming.high,
                low=forming.low,
                close=forming.close,
                is_closed=True,
            )
            if last_closed is not None and (closed.epoch - last_closed) > timeframe_sec:
                self.gap_count += 1
                logger.warning(
                    "Detected candle gap on %s (%ds): prev=%d new=%d",
                    symbol,
                    timeframe_sec,
                    last_closed,
                    closed.epoch,
                )
            buf = self._buffers.setdefault(key, deque(maxlen=self.buffer_size))
            buf.append(closed)
            self._last_closed_epoch[key] = closed.epoch
            self._active_forming[key] = incoming
            return closed

        self._active_forming[key] = incoming
        return None
