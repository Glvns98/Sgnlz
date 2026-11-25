"""Management-Quality Analysis (Section 19): evaluating whether management improved expectancy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from bot.analytics.performance import PerformanceMetrics, compute_performance_metrics
from bot.core.enums import ManagementPolicy
from bot.core.models import ClosedTrade


@dataclass(frozen=True)
class TradeManagementDiagnostic:
    trade_id: str
    symbol: str
    entry_potential_r: float
    realized_r: float
    profit_surrendered_r: float
    risk_taken_r: float
    risk_actually_needed_r: float
    quadrant: str
    exited_too_early: bool
    exited_too_late: bool
    management_protected_account: bool
    management_destroyed_winner: bool


@dataclass(frozen=True)
class ManagementQualityReport:
    total_trades: int
    avg_entry_potential_r: float
    avg_realized_r: float
    avg_profit_surrendered_r: float
    avg_risk_needed_r: float
    quadrant_counts: dict[str, int]
    exit_reason_stats: dict[str, dict[str, float]]
    best_exit_reason: str | None
    early_exits: int
    late_exits: int
    account_saves: int
    destroyed_winners: int
    summary_answers: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_trade_management(trade: ClosedTrade) -> TradeManagementDiagnostic:
    surrendered = max(0.0, trade.mfe - trade.r_multiple)
    risk_needed = abs(min(0.0, trade.mae))

    good_entry = trade.mfe >= 0.65
    good_mgmt = (
        (good_entry and trade.r_multiple >= trade.mfe * 0.50)
        or (not good_entry and trade.r_multiple > -0.75)
    )
    if good_entry and good_mgmt:
        quadrant = "GOOD_ENTRY_WELL_MANAGED"
    elif good_entry and not good_mgmt:
        quadrant = "GOOD_ENTRY_POORLY_MANAGED"
    elif not good_entry and good_mgmt:
        quadrant = "POOR_ENTRY_WELL_MANAGED"
    else:
        quadrant = "POOR_ENTRY_POORLY_MANAGED"

    exited_too_early = trade.mfe >= 1.25 and 0.0 <= trade.r_multiple < 0.45 * trade.mfe
    exited_too_late = trade.mfe >= 1.0 and trade.r_multiple <= 0.15
    protected = (not good_entry or trade.mae <= -0.45) and trade.r_multiple > -0.75 and trade.r_multiple < 0.25
    destroyed = trade.mfe >= 0.85 and trade.r_multiple < 0.0

    return TradeManagementDiagnostic(
        trade_id=trade.trade_id,
        symbol=trade.symbol,
        entry_potential_r=round(trade.mfe, 4),
        realized_r=round(trade.r_multiple, 4),
        profit_surrendered_r=round(surrendered, 4),
        risk_taken_r=1.0,
        risk_actually_needed_r=round(risk_needed, 4),
        quadrant=quadrant,
        exited_too_early=exited_too_early,
        exited_too_late=exited_too_late,
        management_protected_account=protected,
        management_destroyed_winner=destroyed,
    )


def analyze_management_quality(trades: list[ClosedTrade]) -> ManagementQualityReport:
    if not trades:
        return ManagementQualityReport(
            total_trades=0,
            avg_entry_potential_r=0.0,
            avg_realized_r=0.0,
            avg_profit_surrendered_r=0.0,
            avg_risk_needed_r=0.0,
            quadrant_counts={},
            exit_reason_stats={},
            best_exit_reason=None,
            early_exits=0,
            late_exits=0,
            account_saves=0,
            destroyed_winners=0,
            summary_answers={"status": "no_trades_available"},
        )

    diags = [diagnose_trade_management(t) for t in trades]
    quads: dict[str, int] = {}
    for d in diags:
        quads[d.quadrant] = quads.get(d.quadrant, 0) + 1

    by_reason: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        by_reason.setdefault(t.exit_reason.value, []).append(t)

    reason_stats: dict[str, dict[str, float]] = {}
    for reason, grp in by_reason.items():
        reason_stats[reason] = {
            "count": float(len(grp)),
            "avg_r": round(sum(x.r_multiple for x in grp) / len(grp), 4),
            "total_r": round(sum(x.r_multiple for x in grp), 4),
            "avg_mfe_r": round(sum(x.mfe for x in grp) / len(grp), 4),
        }

    best_reason = max(reason_stats.items(), key=lambda kv: kv[1]["total_r"])[0] if reason_stats else None
    n = len(diags)
    early = sum(1 for d in diags if d.exited_too_early)
    late = sum(1 for d in diags if d.exited_too_late)
    saves = sum(1 for d in diags if d.management_protected_account)
    destroyed = sum(1 for d in diags if d.management_destroyed_winner)

    answers = {
        "best_exit_reason": f"{best_reason} (highest total R contribution)" if best_reason else "N/A",
        "account_protection": f"{saves}/{n} trades had adverse excursion capped above -0.75R",
        "winner_preservation": f"{destroyed}/{n} trades turned >=0.85R potential into a loss",
        "exit_timing_balance": f"early={early}, late={late}",
    }

    return ManagementQualityReport(
        total_trades=n,
        avg_entry_potential_r=round(sum(d.entry_potential_r for d in diags) / n, 4),
        avg_realized_r=round(sum(d.realized_r for d in diags) / n, 4),
        avg_profit_surrendered_r=round(sum(d.profit_surrendered_r for d in diags) / n, 4),
        avg_risk_needed_r=round(sum(d.risk_actually_needed_r for d in diags) / n, 4),
        quadrant_counts=quads,
        exit_reason_stats=reason_stats,
        best_exit_reason=best_reason,
        early_exits=early,
        late_exits=late,
        account_saves=saves,
        destroyed_winners=destroyed,
        summary_answers=answers,
    )


def compare_policies_before_after(
    policy_trades: dict[ManagementPolicy, list[ClosedTrade]],
    starting_balance: float = 10000.0,
) -> dict[str, Any]:
    """Compare Static baseline against Breakeven, Trailing, Momentum Exit, and Dynamic Management."""
    metrics_by_policy: dict[str, PerformanceMetrics] = {}
    quality_by_policy: dict[str, ManagementQualityReport] = {}

    for pol, trades in policy_trades.items():
        metrics_by_policy[pol.value] = compute_performance_metrics(trades, starting_balance=starting_balance)
        quality_by_policy[pol.value] = analyze_management_quality(trades)

    static_m = metrics_by_policy.get(ManagementPolicy.STATIC_EXIT.value)
    dyn_m = metrics_by_policy.get(ManagementPolicy.DYNAMIC_MANAGEMENT.value)
    be_m = metrics_by_policy.get(ManagementPolicy.BREAKEVEN_ONLY.value)
    tr_m = metrics_by_policy.get(ManagementPolicy.TRAILING_ONLY.value)

    verdicts: dict[str, Any] = {}
    if static_m and dyn_m:
        delta_exp_r = round(dyn_m.expectancy_r - static_m.expectancy_r, 4)
        delta_dd = round(dyn_m.max_drawdown_pct - static_m.max_drawdown_pct, 4)
        verdicts["dynamic_vs_static_expectancy_r_delta"] = delta_exp_r
        verdicts["dynamic_vs_static_drawdown_delta"] = delta_dd
        verdicts["did_dynamic_management_improve_expectancy"] = delta_exp_r > 0
    if static_m and be_m:
        verdicts["did_breakeven_help_expectancy"] = be_m.expectancy_r > static_m.expectancy_r
    if static_m and tr_m:
        verdicts["did_trailing_help_expectancy"] = tr_m.expectancy_r > static_m.expectancy_r

    return {
        "metrics": {k: v.to_dict() for k, v in metrics_by_policy.items()},
        "management_quality": {k: v.to_dict() for k, v in quality_by_policy.items()},
        "verdicts": verdicts,
    }
