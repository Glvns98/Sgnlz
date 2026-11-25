"""Tests for Trade Management Engine: lifecycle stages, stops, profit protection, recovery & exits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.config.settings import ManagementSettings, SymbolProfile
from bot.core.enums import (
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    MarketRegime,
    MomentumState,
    MomentumTransition,
    TradeDirection,
    TradeStage,
)
from bot.core.models import Position
from bot.management.trade_manager import TradeManager
from bot.tests.conftest import make_market_state


def _make_buy_position(entry: float = 100.0, stop: float = 98.0) -> Position:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Position(
        trade_id="TRD-TEST-1",
        idempotency_key="idem-1",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.ENTERED,
        entry_price=entry,
        current_price=entry,
        initial_stop=stop,
        current_stop=stop,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=20.0,
        initial_risk_amount=100.0,
        initial_risk_distance=entry - stop,
        opened_at=now,
        last_updated_at=now,
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
    )


def test_lifecycle_breakeven_profit_lock_trailing_and_monotonic_stop() -> None:
    mgr = TradeManager(ManagementSettings())
    profile = SymbolProfile(symbol="R_100")
    pos = _make_buy_position(entry=100.0, stop=98.0)  # 1R = 2.0

    # +0.60R -> Move to breakeven
    pos.update_mark(101.2, 101.2, 100.2, pos.opened_at + timedelta(minutes=15))
    ms1 = make_market_state(close=101.2, val=1.8, prev_val=1.4)
    d1 = mgr.evaluate(pos, ms1, symbol_cfg=profile)
    assert d1.action == ManagementAction.MOVE_TO_BREAKEVEN
    assert d1.new_stop_price is not None and d1.new_stop_price >= pos.entry_price
    mgr.apply_decision(pos, d1)
    stop_after_be = pos.current_stop
    assert pos.stage == TradeStage.PROFIT_PROTECTION

    # +1.25R -> Lock profit
    pos.update_mark(102.5, 102.5, 101.0, pos.opened_at + timedelta(minutes=30))
    ms2 = make_market_state(close=102.5, val=2.2, prev_val=1.8)
    d2 = mgr.evaluate(pos, ms2, symbol_cfg=profile)
    assert d2.action == ManagementAction.LOCK_PROFIT
    mgr.apply_decision(pos, d2)
    assert pos.current_stop > stop_after_be

    # +2.20R -> Dynamic Trailing
    pos.update_mark(104.4, 104.5, 102.4, pos.opened_at + timedelta(minutes=45))
    ms3 = make_market_state(close=104.4, val=2.8, prev_val=2.2)
    d3 = mgr.evaluate(pos, ms3, symbol_cfg=profile)
    assert d3.action == ManagementAction.TRAIL
    mgr.apply_decision(pos, d3)
    assert pos.stage == TradeStage.TRAILING
    stop_at_peak = pos.current_stop

    # Pullback to +1.80R -> Stop MUST NEVER widen downward
    pos.update_mark(103.6, 104.0, 103.5, pos.opened_at + timedelta(minutes=60))
    ms4 = make_market_state(
        close=103.6,
        val=2.0,
        prev_val=2.8,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_DECELERATION,),
    )
    d4 = mgr.evaluate(pos, ms4, symbol_cfg=profile)
    mgr.apply_decision(pos, d4)
    assert pos.current_stop >= stop_at_peak
    assert "action" in d4.to_audit_dict() and "reason" in d4.to_audit_dict()


def test_recovery_logic_thesis_validation_no_averaging_and_stagnation_exit() -> None:
    mgr = TradeManager(ManagementSettings(stagnation_bars=4, stagnation_min_r=0.25))
    pos = _make_buy_position(entry=100.0, stop=98.0)

    # Slight drawdown with thesis intact -> HOLD (remaining_fraction stays 1.0, never adds size)
    pos.update_mark(99.4, 100.0, 99.3, pos.opened_at + timedelta(minutes=15))
    ms_intact = make_market_state(close=99.4, momentum_state=MomentumState.WEAK_BULLISH)
    d_intact = mgr.evaluate(pos, ms_intact)
    assert d_intact.action == ManagementAction.HOLD
    assert pos.remaining_fraction == 1.0

    # Thesis invalidated by strong opposing cross after decay -> EXIT
    pos.decay_bars = 2
    ms_inv = make_market_state(
        close=98.9,
        val=-1.8,
        prev_val=0.1,
        momentum_state=MomentumState.STRONG_BEARISH,
        transitions=(MomentumTransition.ZERO_CROSS_DOWN, MomentumTransition.BEARISH_ACCELERATION),
    )
    pos.update_mark(98.9, 99.3, 98.8, pos.opened_at + timedelta(minutes=30))
    d_exit = mgr.evaluate(pos, ms_inv)
    assert d_exit.action == ManagementAction.EXIT
    assert d_exit.exit_reason == ExitReason.THESIS_INVALIDATION

    # Stagnation exit when flat after stagnation_bars
    pos_stag = _make_buy_position(entry=100.0, stop=98.0)
    pos_stag.bars_held = 5
    pos_stag.update_mark(100.1, 100.2, 99.8, pos_stag.opened_at + timedelta(hours=2), is_new_bar=False)
    ms_chop = make_market_state(
        close=100.1,
        val=0.05,
        prev_val=0.04,
        momentum_state=MomentumState.NEUTRAL,
        transitions=(MomentumTransition.STEADY,),
        regime=MarketRegime.CHOP,
    )
    d_stag = mgr.evaluate(pos_stag, ms_chop)
    assert d_stag.action == ManagementAction.EXIT
    assert d_stag.exit_reason == ExitReason.TIME_STAGNATION


def test_five_level_professional_exit_hierarchy() -> None:
    mgr = TradeManager(ManagementSettings(momentum_exit_min_decay_bars=2))
    pos = _make_buy_position(entry=100.0, stop=98.0)  # 1R = 2.0

    # Level 1: Single dark-green bar (BULLISH_DECELERATION) while price is still progressing -> no premature exit
    pos.bars_held = 2
    pos.decay_bars = 1
    pos.update_mark(100.8, 101.0, 100.4, pos.opened_at + timedelta(minutes=30), is_new_bar=False)
    ms_l1 = make_market_state(
        close=100.8,
        val=1.4,
        prev_val=1.8,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_DECELERATION,),
        price_progressing_bull=True,
        structure_broken_bull=False,
    )
    d_l1 = mgr.evaluate(pos, ms_l1)
    assert d_l1.action != ManagementAction.EXIT

    # Level 2: Sustained fade + price stalling -> tighten stop / lock risk
    pos.decay_bars = 2
    ms_l2 = make_market_state(
        close=100.8,
        val=1.0,
        prev_val=1.4,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_DECELERATION,),
        price_progressing_bull=False,
        structure_broken_bull=False,
    )
    d_l2 = mgr.evaluate(pos, ms_l2)
    assert d_l2.action in (ManagementAction.TIGHTEN_STOP, ManagementAction.REDUCE)

    # Level 3: Momentum fade + structural failure -> immediate MOMENTUM_DETERIORATION exit
    ms_l3 = make_market_state(
        close=100.8,
        val=0.7,
        prev_val=1.0,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_DECELERATION,),
        price_progressing_bull=False,
        structure_broken_bull=True,
    )
    d_l3 = mgr.evaluate(pos, ms_l3)
    assert d_l3.action == ManagementAction.EXIT
    assert d_l3.exit_reason == ExitReason.MOMENTUM_DETERIORATION

    # Level 4: Zero-line cross against profitable position -> immediate MOMENTUM_DETERIORATION exit
    pos_l4 = _make_buy_position(entry=100.0, stop=98.0)
    pos_l4.bars_held = 2
    pos_l4.decay_bars = 1
    pos_l4.update_mark(100.6, 100.8, 100.2, pos_l4.opened_at + timedelta(minutes=30), is_new_bar=False)
    ms_l4 = make_market_state(
        close=100.6,
        val=-0.25,
        prev_val=0.15,
        momentum_state=MomentumState.WEAK_BEARISH,
        transitions=(MomentumTransition.ZERO_CROSS_DOWN, MomentumTransition.BEARISH_ACCELERATION),
    )
    d_l4 = mgr.evaluate(pos_l4, ms_l4)
    assert d_l4.action == ManagementAction.EXIT
    assert d_l4.exit_reason == ExitReason.MOMENTUM_DETERIORATION
