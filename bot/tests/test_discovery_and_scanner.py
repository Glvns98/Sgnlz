"""Tests for full-universe Deriv symbol discovery, group exposure cap, polling, and grouped pulse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    MomentumTransition,
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
            "underlying_symbol": "1HZ15V",
            "underlying_symbol_name": "Volatility 15 Index",
            "market": "synthetic_index",
            "submarket": "random_index",
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
    assert parsed["R_100"].description == "Volatility 100 Index"
    assert parsed["R_100"].slack_channel == "C0CJWBD99QD"
    assert parsed["R_100"].cooldown_bars == 7
    assert parsed["1HZ15V"].description == "Volatility 15 (1s) Index"
    assert parsed["BOOM1000"].slack_channel == "C0CJWBE5E2Z"
    assert parsed["frxEURUSD"].slack_channel == "C0CJWBHVAMT"
    assert parsed["cryBTCUSD"].slack_channel == "C0CK3GYBY7K"
    assert parsed["cryBTCUSD"].primary_timeframe == 300
    assert parsed["cryBTCUSD"].higher_timeframe is None


def test_normalize_symbol_description_all_asset_classes() -> None:
    from bot.deriv.discovery import normalize_symbol_description

    cases = [
        ("1HZ15V", "Volatility 15 Index", "Volatility 15 (1s) Index"),
        ("R_100", "Volatility 100 (1s) Index", "Volatility 100 Index"),
        ("stpRNG", "Step Index", "Step Index 100"),
        ("stpRNG2", "Step 200", "Step Index 200"),
        ("BOOM150N", "Boom 150N", "Boom 150 Index"),
        ("CRASH300N", "Crash 300N", "Crash 300 Index"),
        ("JD50", "Jump 50", "Jump 50 Index"),
        ("RB200", "Range Break 200", "Range Break 200 Index"),
        ("RDBEAR", "Bear Market", "Bear Market Index"),
        ("frxEURUSD", "EUR/USD Spot FX", "EUR/USD"),
        ("frxXAUUSD", "Gold / USD", "Gold/USD"),
        ("cryBTCUSD", "BTC USD", "BTC/USD"),
        ("WLDXAU", "XAU Basket", "Gold Basket"),
        ("WLDEUR", "EUR Basket", "EUR Basket"),
        ("OTC_NDX", "US Tech 100", "US Tech 100"),
    ]
    for sym, raw, expected in cases:
        assert normalize_symbol_description(sym, raw) == expected


def test_risk_manager_enforces_max_per_group_open() -> None:
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
    rm_unthrottled = RiskManager(RiskSettings(max_open_positions=2, max_concurrent_risk=0.02, max_per_group_open=2))
    app_unthrottled = rm_unthrottled.check_entry_allowed(
        sig_same_group,
        SymbolProfile(symbol="R_50", submarket="random_index"),
        open_pos,
        rs,
        ms,
    )
    assert app_unthrottled.allowed

    rm_capped = RiskManager(
        RiskSettings(
            enforce_portfolio_caps=True,
            max_open_positions=8,
            max_concurrent_risk=0.10,
            max_per_group_open=2,
        )
    )
    app_blocked = rm_capped.check_entry_allowed(
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
    app_ok = rm_capped.check_entry_allowed(
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
            metadata={
                "slack_channel": "C0CJWBD99QD",
                "slack_thread_ts": "1795543168.175089",
            },
        )
        orch.open_positions.append(pos)

        # Price moves to +1.10R intra-bar -> locks +0.45R floor (stop moves to 100.9)
        up_bar = Candle("R_100", 900, int(now.timestamp()), 100.0, 102.4, 99.8, 102.2, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, up_bar, profile)
        assert pos in orch.open_positions
        assert pos.stage == TradeStage.PROFIT_PROTECTION
        assert pos.current_stop == pytest.approx(100.9)
        assert len(orch.notifier.slack.sent_messages) == 1
        assert orch.notifier.slack.sent_messages[0]["reply_broadcast"] is True
        assert orch.notifier.slack.sent_messages[0]["thread_ts"] == "1795543168.175089"

        # Pullback touches locked stop intra-bar -> closes cleanly as PROFIT_LOCK_STOP
        hit_bar = Candle("R_100", 900, int(now.timestamp()), 102.2, 102.2, 100.8, 100.9, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, hit_bar, profile)
        assert pos not in orch.open_positions
        closed = orch.storage.load_closed_trades(limit=5)
        assert len(closed) == 1
        assert closed[0].exit_reason == ExitReason.PROFIT_LOCK_STOP
    finally:
        orch.storage.close()


@pytest.mark.asyncio
async def test_position_updates_broadcast_and_early_warnings_fire_before_stop_loss(tmp_path) -> None:
    profile = SymbolProfile(
        symbol="R_100",
        primary_timeframe=900,
        higher_timeframe=None,
        slack_channel="C0CJWBD99QD",
        spread_bps=0.0,
        slippage_bps=0.0,
    )
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
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        orch.market_states["R_100"] = make_market_state(symbol="R_100", close=100.0)
        pos = Position(
            trade_id="TRD-WARN-FLOW",
            idempotency_key="idem-warn-flow",
            symbol="R_100",
            direction=TradeDirection.BUY,
            stage=TradeStage.INITIAL_PROTECTION,
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
            metadata={
                "slack_channel": "C0CJWBD99QD",
                "slack_thread_ts": "1795500000.000100",
            },
        )
        orch.open_positions.append(pos)

        # 1. Intra-bar drawdown crosses -0.50R -> broadcasts RISK WARNING, repeat tick does not duplicate
        warn_bar = Candle("R_100", 900, int(now.timestamp()), 100.0, 100.0, 98.9, 98.95, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, warn_bar, profile)
        assert len(orch.notifier.slack.sent_messages) == 1
        msg1 = orch.notifier.slack.sent_messages[-1]
        assert "RISK WARNING" in msg1["text"]
        assert msg1["reply_broadcast"] is True
        assert msg1["thread_ts"] == "1795500000.000100"

        dup_warn_bar = Candle("R_100", 900, int(now.timestamp()) + 30, 98.95, 99.0, 98.85, 98.90, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, dup_warn_bar, profile)
        assert len(orch.notifier.slack.sent_messages) == 1

        # 2. Further drop to -0.80R -> broadcasts DANGER ZONE at CRITICAL priority
        danger_bar = Candle("R_100", 900, int(now.timestamp()) + 60, 98.90, 98.95, 98.35, 98.40, is_closed=False)
        await orch._monitor_open_position_intrabar(pos, danger_bar, profile)
        assert len(orch.notifier.slack.sent_messages) == 2
        msg2 = orch.notifier.slack.sent_messages[-1]
        assert "DANGER ZONE" in msg2["text"]
        assert msg2["reply_broadcast"] is True
        assert msg2["meta"]["priority"] == "CRITICAL"

        # 3. First defensive TIGHTEN_STOP notifies, while subsequent tiny nudge (< 0.10R) is tracked silently
        pos.current_stop = 98.15  # candidate at 99.40 (-0.30R) is 99.40 - 0.60*2.0 = 98.20 (delta = 0.025R)
        pos.metadata["warned_drawdown_levels"] = [-0.5, -0.75]
        stub_states = [
            # Bar 1: first tighten -> notifies
            make_market_state(
                symbol="R_100",
                close=99.40,
                val=-0.20,
                prev_val=0.10,
                momentum_state=MomentumState.WEAK_BEARISH,
                transitions=(MomentumTransition.BULLISH_DECELERATION,),
            ),
            # Bar 2: second tiny tighten (98.20 -> 98.25, delta = 0.025R < 0.10R) -> silent
            make_market_state(
                symbol="R_100",
                close=99.45,
                val=-0.18,
                prev_val=-0.20,
                momentum_state=MomentumState.WEAK_BEARISH,
                transitions=(MomentumTransition.BULLISH_DECELERATION,),
            ),
            # Bar 3: MOMENTUM_DECAY alone -> tracked silently (no alert)
            make_market_state(
                symbol="R_100",
                close=100.20,
                val=0.30,
                prev_val=0.50,
                momentum_state=MomentumState.WEAK_BULLISH,
                transitions=(MomentumTransition.BULLISH_DECELERATION,),
            ),
            # Bar 4: REVERSAL_WARNING fires once
            make_market_state(
                symbol="R_100",
                close=100.0,
                val=-0.10,
                prev_val=0.10,
                momentum_state=MomentumState.WEAK_BEARISH,
                transitions=(MomentumTransition.ZERO_CROSS_DOWN,),
            ),
            # Bar 5: Same severity REVERSAL_WARNING -> escalation-only suppresses repeat
            make_market_state(
                symbol="R_100",
                close=100.0,
                val=0.05,
                prev_val=0.25,
                momentum_state=MomentumState.WEAK_BULLISH,
                transitions=(MomentumTransition.MOMENTUM_EXHAUSTION,),
            ),
            # Bar 6: Escalation to EXIT_WARNING -> fires at CRITICAL priority
            make_market_state(
                symbol="R_100",
                close=100.80,
                val=0.20,
                prev_val=0.05,
                momentum_state=MomentumState.WEAK_BULLISH,
                regime=MarketRegime.ABNORMAL_VOLATILITY,
                transitions=(MomentumTransition.STEADY,),
            ),
        ]

        class _StubAnalyzer:
            def __init__(self, items):
                self._items = list(items)
                self._idx = 0

            def update(self, candle):
                item = self._items[self._idx]
                self._idx += 1
                return item

        orch.analyzers["R_100"] = _StubAnalyzer(stub_states)

        # Bar 1: first TIGHTEN_STOP
        c1 = Candle("R_100", 900, int((now + timedelta(minutes=15)).timestamp()), 99.0, 99.6, 98.8, 99.40, is_closed=True)
        await orch.on_closed_candle(c1)
        assert pos.current_stop == pytest.approx(98.20)
        assert pos.metadata.get("tighten_alerted") is True
        assert len(orch.notifier.slack.sent_messages) == 3
        msg3 = orch.notifier.slack.sent_messages[-1]
        assert "STOP TIGHTENED" in msg3["text"]
        assert msg3["reply_broadcast"] is True

        # Bar 2: tiny TIGHTEN_STOP (< 0.10R) suppressed, so Rank 1 TREND WEAKENING alert fires once
        c2 = Candle("R_100", 900, int((now + timedelta(minutes=30)).timestamp()), 99.40, 99.6, 99.0, 99.45, is_closed=True)
        await orch.on_closed_candle(c2)
        assert pos.current_stop == pytest.approx(98.25)
        assert len(orch.notifier.slack.sent_messages) == 4
        msg_weak = orch.notifier.slack.sent_messages[-1]
        assert "TREND WEAKENING" in msg_weak["text"]
        assert msg_weak["reply_broadcast"] is True

        # Bar 3: MOMENTUM_DECAY (same Rank 1 severity) -> suppressed by escalation-only mode
        pos.decay_bars = 1
        c3 = Candle("R_100", 900, int((now + timedelta(minutes=45)).timestamp()), 99.45, 100.3, 99.4, 100.20, is_closed=True)
        await orch.on_closed_candle(c3)
        assert pos.stage == TradeStage.MOMENTUM_DECAY
        assert len(orch.notifier.slack.sent_messages) == 4

        # Bar 4: Escalates Rank 1 -> Rank 2 REVERSAL_WARNING
        pos.decay_bars = 0
        c4 = Candle("R_100", 900, int((now + timedelta(minutes=60)).timestamp()), 100.0, 100.2, 99.3, 100.0, is_closed=True)
        await orch.on_closed_candle(c4)
        assert len(orch.notifier.slack.sent_messages) == 5
        msg4 = orch.notifier.slack.sent_messages[-1]
        assert "REVERSAL WARNING" in msg4["text"]
        assert msg4["reply_broadcast"] is True

        # Bar 5: Repeat REVERSAL_WARNING suppressed by escalation-only mode
        c5 = Candle("R_100", 900, int((now + timedelta(minutes=75)).timestamp()), 100.0, 100.2, 99.8, 100.0, is_closed=True)
        await orch.on_closed_candle(c5)
        assert len(orch.notifier.slack.sent_messages) == 5

        # Bar 6: Escalates to EXIT_WARNING -> notifies at CRITICAL priority
        pos.current_stop = 99.95  # already tighter than 100.80 - 0.90 = 99.90 so HOLD lets EXIT_WARNING fire
        c6 = Candle("R_100", 900, int((now + timedelta(minutes=90)).timestamp()), 100.0, 100.9, 99.96, 100.80, is_closed=True)
        await orch.on_closed_candle(c6)
        assert len(orch.notifier.slack.sent_messages) == 6
        msg5 = orch.notifier.slack.sent_messages[-1]
        assert "EXIT WARNING" in msg5["text"]
        assert msg5["reply_broadcast"] is True
        assert msg5["meta"]["priority"] == "CRITICAL"
    finally:
        orch.storage.close()
