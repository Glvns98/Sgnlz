"""Tests for Deriv API resilience, malformed messages, closed-bar anti-repainting, and restart reconciliation."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from bot.config.settings import DerivSettings
from bot.core.enums import ManagementPolicy, TradeDirection, TradeStage
from bot.core.models import Position, utc_now
from bot.deriv.account import AccountMonitor
from bot.deriv.client import DerivAPIClient, DerivAPIError
from bot.deriv.market_data import MarketDataEngine


@pytest.mark.asyncio
async def test_deriv_ws_client_auth_ping_malformed_and_api_error() -> None:
    received_reqs: list[dict] = []
    malformed_alerts: list[str] = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            received_reqs.append(msg)
            req_id = msg.get("req_id")
            if "authorize" in msg:
                await ws.send(json.dumps({"req_id": req_id, "authorize": {"balance": 5000.0}}))
            elif "ping" in msg:
                await ws.send(json.dumps({"req_id": req_id, "ping": "pong"}))
            elif "trigger_malformed" in msg:
                await ws.send("NOT_JSON{{{")
                await ws.send(json.dumps({"req_id": req_id, "ok": True}))
            elif "trigger_error" in msg:
                await ws.send(
                    json.dumps(
                        {
                            "req_id": req_id,
                            "msg_type": "buy",
                            "error": {"code": "InsufficientBalance", "message": "Not enough funds"},
                        }
                    )
                )

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    client = DerivAPIClient(
        DerivSettings(endpoint=f"ws://127.0.0.1:{port}", token="fake-tok", ping_interval_sec=5.0),
        on_malformed_message=lambda m: malformed_alerts.append(m),
    )
    try:
        await client.connect()
        auth_resp = await client.authorize()
        assert auth_resp is not None and client.is_authorized

        ping_resp = await client.request({"ping": 1})
        assert ping_resp["ping"] == "pong"

        ok_resp = await client.request({"trigger_malformed": 1})
        assert ok_resp["ok"] is True
        await asyncio.sleep(0.05)
        assert len(malformed_alerts) == 1

        with pytest.raises(DerivAPIError, match="InsufficientBalance"):
            await client.request({"trigger_error": 1})
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


def test_market_data_closed_bar_anti_repainting_and_account_reconcile() -> None:
    client = DerivAPIClient(DerivSettings())
    md = MarketDataEngine(client)

    # Tick 1 & 2 inside epoch 1000 -> forming only, must NOT emit closed candle
    assert md.ingest_stream_ohlc("R_100", 900, {"open_time": 1000, "open": 10, "high": 12, "low": 9, "close": 11}) is None
    assert md.ingest_stream_ohlc("R_100", 900, {"open_time": 1000, "open": 10, "high": 13, "low": 9, "close": 12}) is None

    # Epoch advances to 1900 -> emits finalized epoch 1000 candle with high=13, close=12
    closed = md.ingest_stream_ohlc("R_100", 900, {"open_time": 1900, "open": 12, "high": 14, "low": 11, "close": 13})
    assert closed is not None
    assert closed.epoch == 1000
    assert closed.high == 13.0
    assert closed.close == 12.0

    # Duplicate epoch <= 1000 ignored
    assert md.ingest_stream_ohlc("R_100", 900, {"open_time": 1000, "open": 10, "high": 10, "low": 10, "close": 10}) is None
    assert md.duplicate_count == 1

    # Restart reconciliation
    now = utc_now()
    pos1 = Position(
        trade_id="T1",
        idempotency_key="k1",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.PROFIT_PROTECTION,
        entry_price=100.0,
        current_price=102.0,
        initial_stop=98.0,
        current_stop=100.1,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=20.0,
        initial_risk_amount=100.0,
        initial_risk_distance=2.0,
        opened_at=now,
        last_updated_at=now,
        contract_id="C-100",
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
    )
    mon = AccountMonitor(initial_balance=10000.0)
    mon.open_contracts = {"C-100": {"contract_id": "C-100"}, "C-999": {"contract_id": "C-999"}}
    rep = mon.reconcile_positions([pos1])
    assert rep.reconciled_ids == ("T1",)
    assert rep.untracked_broker_contract_ids == ("C-999",)
