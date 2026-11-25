"""Tests for full-universe Deriv symbol discovery, group exposure cap, polling, and grouped pulse."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.app.lifecycle import EngineOrchestrator
from bot.config.settings import (
    DerivSettings,
    EngineSettings,
    LoggingSettings,
    NotificationSettings,
    RiskSettings,
    StorageSettings,
    SymbolProfile,
)
from bot.core.enums import (
    ExitReason,
    MarketRegime,
    MomentumState,
    RiskLevel,
    SignalType,
    SqueezeState,
    TradeDirection,
    TradeStage,
)
from bot.core.models import Candle, Position, RiskState, TradeSignal
from bot.deriv.client import DerivAPIClient
from bot.deriv.discovery import parse_active_symbols
from bot.deriv.market_data import MarketDataEngine
from bot.notifications.slack import SlackNotifier
from bot.risk.risk_manager import RiskManager
from bot.tests.conftest import make_market_state


def test_parse_active_symbols_routes_all_groups_and_skips_suspended() -> None:
    raw = [
        {
            "underlying_symbol": "R_100",
            "underlying_symbol_name": "Volatility 100 Index",
            "market": "synthetic_index",
            "submarket": "random_index",
            "exchange_is_open": 1,
            "is_trading_suspended": 0,
        },
        {
            "underlying_symbol": "BOOM1000",
            "underlying_symbol_name": "Boom 1000 Index",
            "market": "synthetic_index",
            "submarket": "crash_index",
            "exchange_is_open": 1,
            "is_trading_suspended": 0,
        },
        {
            "underlying_symbol": "frxEURUSD",
            "underlying_symbol_name": "EUR/USD",
            "market": "forex",
            "submarket": "major_pairs",
            "exchange_is_open": 1,
            "is_trading_suspended": 0,
        },
        {
            "underlying_symbol": "cryBTCUSD",
            "underlying_symbol_name": "BTC/USD",
            "market": "cryptocurrency",
            "submarket": "non_stable_coin",
            "exchange_is_open": 1,
            "is_trading_suspended": 0,
        },
        {
            "underlying_symbol": "SUSPENDED_SYM",
            "underlying_symbol_name": "Suspended",
            "market": "synthetic_index",
            "submarket": "random_index",
            "exchange_is_open": 0,
            "is_trading_suspended": 1,
        },
    ]
    overrides = {
        "R_100": SymbolProfile(symbol="R_100", cooldown_bars=7, min_confidence=0.63),
    }
    parsed = parse_active_symbols(raw, overrides=overrides)
    assert "SUSPENDED_SYM" not in parsed
    assert parsed["R_100"].slack_channel == "C0CJWBD99QD"
    assert parsed["R_100"].cooldown_bars == 7
    assert parsed["BOOM1000"].slack_channel == "C0CJWBE5E2Z"
    assert parsed["frxEURUSD"].slack_channel == "C0CJWBHVAMT"
    assert parsed["cryBTCUSD"].slack_channel == "C0CK3GYBY7K"
    assert parsed["cryBTCUSD"].primary_timeframe == 900
    assert parsed["cryBTCUSD"].higher_timeframe is None


def test_risk_manager_enforces_max_per_group_open() -> None:
    rm = RiskManager(RiskSettings(max_open_positions=8, max_concurrent_risk=0.10, max_per_group_open=2))
    now = datetime(2026, 1, 1, tzinfo=UTC)

    open_pos = [
        Position(
            trade_id="T1",
            idempotency_key="k1",
            symbol="R_10",
            direction=TradeDirection.BUY,
            stage=TradeStage.ENTERED,
            entry_price=100.0,
            current_price=100.0,
            initial_stop=98.0,
            current_stop=98.0,
            take_profit=None,
            stake=100.0,
            remaining_fraction=1.0,
            multiplier=40.0,
            initial_risk_amount=100.0,
            initial_risk_distance=2.0,
            opened_at=now,
            last_updated_at=now,
            metadata={"submarket": "random_index"},
        ),
        Position(
            trade_id="T2",
            idempotency_key="k2",
            symbol="R_25",
            direction=TradeDirection.BUY,
            stage=TradeStage.ENTERED,
            entry_price=100.0,
            current_price=100.0,
            initial_stop=98.0,
            current_stop=98.0,
            take_profit=None,
            stake=100.0,
            remaining_fraction=1.0,
            multiplier=40.0,
            initial_risk_amount=100.0,
            initial_risk_distance=2.0,
            opened_at=now,
            last_updated_at=now,
            metadata={"submarket": "random_index"},
        ),
    ]
    rs = RiskState(RiskLevel.NORMAL, 10000.0, 10000.0, 0.0, 0.0, 0, 2, 0.02, True)
    ms = make_market_state(symbol="R_50")

    sig_same_group = TradeSignal(
        signal_id="s-same",
        symbol="R_50",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.8,
        confidence=0.8,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=now,
        timeframe_sec=900,
        entry_price=100.0,
        suggested_stop=98.0,
        initial_risk_distance=2.0,
    )
    app_blocked = rm.check_entry_allowed(
        sig_same_group,
        SymbolProfile(symbol="R_50", submarket="random_index"),
        open_pos,
        rs,
        ms,
    )
    assert not app_blocked.allowed
    assert "max_group_exposure_reached" in app_blocked.reasons

    sig_other_group = TradeSignal(
        signal_id="s-other",
        symbol="BOOM1000",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.8,
        confidence=0.8,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=now,
        timeframe_sec=900,
        entry_price=100.0,
        suggested_stop=98.0,
        initial_risk_distance=2.0,
    )
    app_ok = rm.check_entry_allowed(
        sig_other_group,
        SymbolProfile(symbol="BOOM1000", submarket="crash_index"),
        open_pos,
        rs,
        make_market_state(symbol="BOOM1000"),
    )
    assert app_ok.allowed


@pytest.mark.asyncio
async def test_fetch_new_closed_candles_deduplicates_epochs() -> None:
    class _FakeClient(DerivAPIClient):
        def __init__(self) -> None:
            super().__init__(DerivSettings())
            self.calls = 0

        async def request(self, payload, timeout: float = 15.0):
            self.calls += 1
            if self.calls == 1:
                return {
                    "candles": [
                        {"epoch": 1000, "open": 10, "high": 11, "low": 9, "close": 10.5},
                        {"epoch": 1900, "open": 10.5, "high": 12, "low": 10, "close": 11.5},
                        {"epoch": 9999999999, "open": 11.5, "high": 12, "low": 11, "close": 11.8},
                    ]
                }
            return {
                "candles": [
                    {"epoch": 1900, "open": 10.5, "high": 12, "low": 10, "close": 11.5},
                    {"epoch": 2800, "open": 11.5, "high": 13, "low": 11, "close": 12.5},
                    {"epoch": 9999999999, "open": 12.5, "high": 13, "low": 12, "close": 12.7},
                ]
            }

    md = MarketDataEngine(_FakeClient())
    first = await md.fetch_new_closed_candles("R_100", 900, count=3)
    assert [c.epoch for c in first] == [1000, 1900]

    second = await md.fetch_new_closed_candles("R_100", 900, count=3)
    assert [c.epoch for c in second] == [2800]


def test_grouped_market_pulse_for_large_catalog() -> None:
    states = {f"SYM_{i:02d}": make_market_state(symbol=f"SYM_{i:02d}") for i in range(30)}
    syms = {
        f"SYM_{i:02d}": SymbolProfile(symbol=f"SYM_{i:02d}", submarket="random_index" if i < 15 else "crash_index")
        for i in range(30)
    }
    rs = RiskState(RiskLevel.NORMAL, 10000.0, 10000.0, 0.0, 0.0, 0, 0, 0.0, True)
    pulse = SlackNotifier.format_market_pulse("PAPER_TRADING", states, [], rs, symbols_cfg=syms)
    assert "Analyzed: `30`" in pulse
    assert "random_index" in pulse
    assert "crash_index" in pulse
    assert len(pulse) < 2900


@pytest.mark.asyncio
async def test_intrabar_position_monitor_locks_profit_and_closes_cleanly(tmp_path) -> None:
    profile = SymbolProfile(symbol="R_100", primary_timeframe=900, higher_timeframe=None, spread_bps=0.0, slippage_bps=0.0)
    settings = EngineSettings(
        symbols={"R_100": profile},
        storage=StorageSettings(sqlite_path=str(tmp_path / "bot.db")),
        logging=LoggingSettings(json_log_path=str(tmp_path / "audit.jsonl")),
        notifications=NotificationSettings(
            slack_enabled=False,
            reddit_enabled=False,
            reddit_outbox_dir=str(tmp_path / "reddit"),
        ),
    )
    orch = EngineOrchestrator(settings, show_dashboard=False)
    try:
        now = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
        state = make_market_state(symbol="R_100", close=100.0)
        orch.market_states["R_100"] = state
        pos = Position(
            trade_id="TRD-INTRA-1",
            idempotency_key="idem-intra-1",
            symbol="R_100",
            direction=TradeDirection.BUY,
            stage=TradeStage.ENTERED,
            entry_price=100.0,
            current_price=100.0,
            initial_stop=98.0,
            current_stop=98.0,
            take_profit=None,
            stake=100.0,
            remaining_fraction=1.0,
            multiplier=20.0,
            initial_risk_amount=100.0,
            initial_risk_distance=2.0,
            opened_at=now,
            last_updated_at=now,
        )
        orch.open_positions.append(pos)

        # Price moves to +1.10R intra-bar -> locks +0.45R floor (stop moves to 100.9)
        up_bar = Candle("R_100", 900, int(now.timestamp()), 100.0, 102.4, 99.8, 102.2, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, up_bar, profile)
        assert pos in orch.open_positions
        assert pos.stage == TradeStage.PROFIT_PROTECTION
        assert pos.current_stop == pytest.approx(100.9)

        # Pullback touches locked stop intra-bar -> closes cleanly as PROFIT_LOCK_STOP
        hit_bar = Candle("R_100", 900, int(now.timestamp()), 102.2, 102.2, 100.8, 100.9, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, hit_bar, profile)
        assert pos not in orch.open_positions
        closed = orch.storage.load_closed_trades(limit=5)
        assert len(closed) == 1
        assert closed[0].exit_reason == ExitReason.PROFIT_LOCK_STOP
    finally:
        orch.storage.close()
