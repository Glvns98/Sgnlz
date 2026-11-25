"""Tests for SQLite 9-table persistence, performance analytics, management quality & learning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.analytics.management_analysis import (
    analyze_management_quality,
    compare_policies_before_after,
)
from bot.analytics.performance import compute_performance_metrics
from bot.analytics.trade_analysis import PostTradeLearningEngine
from bot.core.enums import (
    AlertPriority,
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    TradeDirection,
    TradeStage,
)
from bot.core.models import ClosedTrade, ManagementDecision, ManagementEvent, Position
from bot.tests.conftest import make_market_state


def _sample_closed_trade(
    idx: int,
    r_mult: float,
    mfe: float,
    mae: float,
    exit_reason: ExitReason = ExitReason.TRAILING_STOP,
) -> ClosedTrade:
    t0 = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=idx)
    return ClosedTrade(
        trade_id=f"T-{idx}",
        timestamp=t0,
        closed_at=t0 + timedelta(minutes=30),
        symbol="R_100",
        direction=TradeDirection.BUY,
        entry=100.0,
        initial_risk=100.0,
        initial_stop=98.0,
        final_stop=101.0 if r_mult > 0 else 98.0,
        exit=100.0 + r_mult * 2.0,
        exit_reason=exit_reason,
        duration_sec=1800.0,
        bars_held=4 if r_mult > 0 else 14,
        pnl=r_mult * 100.0,
        r_multiple=r_mult,
        mfe=mfe,
        mae=mae,
        highest_profit=mfe * 100.0,
        lowest_drawdown=mae * 100.0,
        squeeze_state_at_entry="SQUEEZE_OFF",
        squeeze_state_at_exit="SQUEEZE_OFF",
        momentum_at_entry=1.8 if r_mult > 0 else 0.4,
        momentum_at_exit=0.5,
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
        management_actions=("MOVE_TO_BREAKEVEN", "TRAIL"),
        entry_quality="GOOD_ENTRY" if mfe >= 0.75 else "POOR_ENTRY",
        management_quality="WELL_MANAGED" if r_mult > 0 else "RISK_PROTECTED",
        attribution_summary="test",
    )


def test_sqlite_all_9_tables_and_restart_reload(temp_storage) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    temp_storage.save_config_version("cfg1", "{}")

    pos = Position(
        trade_id="TRD-PERSIST",
        idempotency_key="idem-persist",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.PROFIT_PROTECTION,
        entry_price=100.0,
        current_price=101.5,
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
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
    )
    temp_storage.upsert_position(pos)
    assert temp_storage.has_idempotency_key("idem-persist")

    dec = ManagementDecision(
        action=ManagementAction.MOVE_TO_BREAKEVEN,
        reason="be_hit",
        confidence=0.82,
        risk_state="NORMAL",
        target_stage=TradeStage.PROFIT_PROTECTION,
        new_stop_price=100.1,
        timestamp=now,
    )
    ev = ManagementEvent(
        event_id="ev-1",
        trade_id=pos.trade_id,
        timestamp=now,
        stage_before=TradeStage.ENTERED,
        stage_after=TradeStage.PROFIT_PROTECTION,
        decision=dec,
        current_price=101.5,
        unrealized_pnl=75.0,
        unrealized_r=0.75,
        mfe_r=0.75,
        mae_r=-0.10,
    )
    temp_storage.record_management_event(ev)

    ms = make_market_state()
    temp_storage.record_market_state(ms)
    temp_storage.record_system_event(AlertPriority.NOTICE, "test", "booted")
    temp_storage.save_performance_snapshot("snap1", {"expectancy_r": 0.45})

    reloaded = temp_storage.load_open_positions()
    assert len(reloaded) == 1
    assert reloaded[0].trade_id == "TRD-PERSIST"
    assert len(reloaded[0].management_events) == 1

    # Save closed trade and verify position removed from open positions
    ct = _sample_closed_trade(1, 1.5, 1.8, -0.2)
    temp_storage.save_closed_trade(ct, idempotency_key="idem-closed-1")
    loaded_trades = temp_storage.load_closed_trades()
    assert len(loaded_trades) == 1
    assert loaded_trades[0].r_multiple == 1.5

    counts = temp_storage.table_counts()
    assert counts["config_versions"] == 1
    assert counts["trades"] == 1
    assert counts["management_events"] == 1
    assert counts["market_states"] == 1
    assert counts["system_events"] == 1
    assert counts["performance_metrics"] == 1


def test_performance_management_quality_and_learning() -> None:
    trades = [
        _sample_closed_trade(1, 2.2, 2.5, -0.1, ExitReason.TRAILING_STOP),
        _sample_closed_trade(2, 1.4, 1.8, -0.2, ExitReason.PROFIT_LOCK_STOP),
        _sample_closed_trade(3, -0.5, 0.2, -0.6, ExitReason.TIME_STAGNATION),
        _sample_closed_trade(4, -1.0, 0.1, -1.0, ExitReason.STOP_LOSS),
        _sample_closed_trade(5, 1.8, 2.1, -0.15, ExitReason.TRAILING_STOP),
        _sample_closed_trade(6, -0.6, 0.15, -0.65, ExitReason.THESIS_INVALIDATION),
    ]
    perf = compute_performance_metrics(trades, starting_balance=10000.0)
    assert perf.total_trades == 6
    assert perf.wins == 3 and perf.losses == 3
    assert perf.net_pnl == 330.0
    assert perf.profit_factor > 1.5
    assert perf.expectancy_r > 0.0

    mq = analyze_management_quality(trades)
    assert mq.total_trades == 6
    assert mq.best_exit_reason == ExitReason.TRAILING_STOP.value

    static_trades = [_sample_closed_trade(i, -0.4, 1.2, -1.0, ExitReason.STOP_LOSS) for i in range(6)]
    comp = compare_policies_before_after(
        {ManagementPolicy.STATIC_EXIT: static_trades, ManagementPolicy.DYNAMIC_MANAGEMENT: trades}
    )
    assert comp["verdicts"]["did_dynamic_management_improve_expectancy"] is True

    learn = PostTradeLearningEngine(min_sample_size=2).analyze(trades, config_version="vtest")
    assert len(learn["insights"]) >= 1
    for p in learn["proposed_changes"]:
        assert p["status"] == "PROPOSED_FOR_REVIEW"
