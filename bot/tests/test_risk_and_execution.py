"""Tests for RiskManager, ExecutionEngine 7-stage pipeline, idempotency, and Live lock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.config.settings import RiskSettings
from bot.core.enums import (
    MarketRegime,
    MomentumState,
    OperatingMode,
    RiskLevel,
    SignalType,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import TradeSignal
from bot.deriv.execution import ExecutionEngine
from bot.risk.risk_manager import RiskManager
from bot.tests.conftest import make_market_state


def _make_signal(epoch: int = 1767225600) -> TradeSignal:
    return TradeSignal(
        signal_id="sig-1234",
        symbol="R_100",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.85,
        confidence=0.80,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        timeframe_sec=900,
        entry_price=100.0,
        suggested_stop=98.0,
        initial_risk_distance=2.0,
    )


def test_risk_prohibits_martingale_and_enforces_circuit_breakers() -> None:
    with pytest.raises(ValueError, match="Martingale"):
        RiskSettings(allow_martingale=True)
    with pytest.raises(ValueError, match="averaging down"):
        RiskSettings(allow_averaging_down=True)

    rm = RiskManager(RiskSettings(account_balance=10000.0, max_daily_loss=0.05, max_consecutive_losses=3))
    halted_dd = rm.evaluate_risk_state(9400.0, [], daily_pnl=-600.0, consecutive_losses=1)
    assert halted_dd.level == RiskLevel.HALTED
    assert not halted_dd.entries_allowed

    halted_streak = rm.evaluate_risk_state(9800.0, [], daily_pnl=-200.0, consecutive_losses=3)
    assert halted_streak.level == RiskLevel.HALTED
    assert not halted_streak.entries_allowed


@pytest.mark.asyncio
async def test_execution_idempotency_and_live_mode_safety_lock(test_settings, temp_storage) -> None:
    rm = RiskManager(test_settings.risk)
    exec_engine = ExecutionEngine(test_settings, rm, temp_storage)
    profile = test_settings.symbols["R_100"]
    signal = _make_signal()
    ms = make_market_state()
    rs = rm.evaluate_risk_state(10000.0, [], 0.0, 0)

    # First submission succeeds in PAPER_TRADING
    r1 = await exec_engine.submit_signal(signal, profile, [], rs, ms)
    assert r1.accepted and r1.position is not None

    # Exact duplicate signal blocked by max_symbol_exposure / idempotency
    r2 = await exec_engine.submit_signal(signal, profile, [], rs, ms)
    assert not r2.accepted
    assert "idempotency" in r2.reason

    # ANALYSIS_ONLY blocks execution
    test_settings.mode = OperatingMode.ANALYSIS_ONLY
    r_ana = await exec_engine.submit_signal(_make_signal(1767226500), profile, [], rs, ms)
    assert not r_ana.accepted
    assert "analysis_only" in r_ana.reason
