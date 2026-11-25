"""Performance Analytics: comprehensive Section 18 metrics beyond win rate alone."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from bot.core.enums import ExitReason
from bot.core.models import ClosedTrade


@dataclass(frozen=True)
class PerformanceMetrics:
    total_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    expectancy_pnl: float
    expectancy_r: float
    avg_win_r: float
    avg_loss_r: float
    avg_r: float
    max_drawdown_pct: float
    max_drawdown_r: float
    recovery_factor: float
    sharpe_like: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_sec: float
    avg_mfe_r: float
    avg_mae_r: float
    exit_efficiency: float
    avg_profit_giveback_r: float
    management_induced_losses: int
    risk_of_ruin_est: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_performance_metrics(
    trades: list[ClosedTrade],
    starting_balance: float = 10000.0,
) -> PerformanceMetrics:
    """Compute Section 18 performance metrics over a sequence of ClosedTrades."""
    n = len(trades)
    if n == 0:
        return PerformanceMetrics(
            total_trades=0,
            wins=0,
            losses=0,
            breakevens=0,
            win_rate=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_pnl=0.0,
            profit_factor=0.0,
            expectancy_pnl=0.0,
            expectancy_r=0.0,
            avg_win_r=0.0,
            avg_loss_r=0.0,
            avg_r=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_r=0.0,
            recovery_factor=0.0,
            sharpe_like=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            avg_duration_sec=0.0,
            avg_mfe_r=0.0,
            avg_mae_r=0.0,
            exit_efficiency=0.0,
            avg_profit_giveback_r=0.0,
            management_induced_losses=0,
            risk_of_ruin_est=0.0,
        )

    pnls = np.array([t.pnl for t in trades], dtype=np.float64)
    rs = np.array([t.r_multiple for t in trades], dtype=np.float64)
    mfes = np.array([t.mfe for t in trades], dtype=np.float64)
    maes = np.array([t.mae for t in trades], dtype=np.float64)
    durations = np.array([t.duration_sec for t in trades], dtype=np.float64)

    wins_mask = pnls > 1e-9
    losses_mask = pnls < -1e-9
    be_mask = (~wins_mask) & (~losses_mask)

    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    breakevens = int(be_mask.sum())
    win_rate = wins / n

    gross_profit = float(pnls[wins_mask].sum()) if wins > 0 else 0.0
    gross_loss = float(-pnls[losses_mask].sum()) if losses > 0 else 0.0
    net_pnl = float(pnls.sum())
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 1e-9
        else (999.0 if gross_profit > 0 else 0.0)
    )

    expectancy_pnl = net_pnl / n
    avg_r = float(rs.mean())
    avg_win_r = float(rs[wins_mask].mean()) if wins > 0 else 0.0
    avg_loss_r = float(rs[losses_mask].mean()) if losses > 0 else 0.0
    loss_rate = losses / n
    expectancy_r = (win_rate * avg_win_r) + (loss_rate * avg_loss_r)

    # Equity curve drawdown (balance & R)
    equity = starting_balance + np.cumsum(pnls)
    peaks = np.maximum.accumulate(np.insert(equity, 0, starting_balance))[1:]
    dd_pct_series = np.where(peaks > 0, (peaks - equity) / peaks, 0.0)
    max_dd_pct = float(dd_pct_series.max()) if len(dd_pct_series) > 0 else 0.0
    max_dd_abs = float((peaks - equity).max()) if len(equity) > 0 else 0.0

    cum_r = np.cumsum(rs)
    peak_r = np.maximum.accumulate(np.insert(cum_r, 0, 0.0))[1:]
    max_dd_r = float((peak_r - cum_r).max()) if len(cum_r) > 0 else 0.0

    recovery_factor = net_pnl / max_dd_abs if max_dd_abs > 1e-9 else (net_pnl if net_pnl > 0 else 0.0)

    r_std = float(rs.std(ddof=1)) if n > 1 else 0.0
    sharpe_like = (avg_r / r_std) * math.sqrt(n) if r_std > 1e-9 else 0.0

    max_cw = cur_cw = max_cl = cur_cl = 0
    for pnl in pnls:
        if pnl > 1e-9:
            cur_cw += 1
            cur_cl = 0
            max_cw = max(max_cw, cur_cw)
        elif pnl < -1e-9:
            cur_cl += 1
            cur_cw = 0
            max_cl = max(max_cl, cur_cl)

    givebacks = np.maximum(0.0, mfes - rs)
    pos_mfe = mfes > 0.05
    efficiencies = np.where(pos_mfe, np.clip(rs / np.maximum(mfes, 1e-9), -1.0, 1.0), 0.0)
    exit_efficiency = float(efficiencies[pos_mfe].mean()) if pos_mfe.any() else 0.0

    # Management-induced loss: had meaningful favorable excursion (>= +0.75R) yet closed negative
    # via a management exit (not untouched initial SL)
    mgmt_induced_losses = sum(
        1
        for t in trades
        if t.mfe >= 0.75
        and t.r_multiple < 0.0
        and t.exit_reason
        in (
            ExitReason.MOMENTUM_DETERIORATION,
            ExitReason.TIME_STAGNATION,
            ExitReason.TRAILING_STOP,
        )
    )

    # Approximate risk-of-ruin heuristic across 20R ruin barrier
    if expectancy_r <= 0:
        ror = 1.0
    else:
        edge = max(1e-4, min(0.99, 0.5 + expectancy_r / max(2.0 * max(abs(avg_loss_r), 0.5), 1.0)))
        ratio = (1.0 - edge) / edge
        ror = min(1.0, ratio ** 20)

    return PerformanceMetrics(
        total_trades=n,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        win_rate=round(win_rate, 4),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        net_pnl=round(net_pnl, 2),
        profit_factor=round(profit_factor, 4),
        expectancy_pnl=round(expectancy_pnl, 2),
        expectancy_r=round(expectancy_r, 4),
        avg_win_r=round(avg_win_r, 4),
        avg_loss_r=round(avg_loss_r, 4),
        avg_r=round(avg_r, 4),
        max_drawdown_pct=round(max_dd_pct, 4),
        max_drawdown_r=round(max_dd_r, 4),
        recovery_factor=round(recovery_factor, 4),
        sharpe_like=round(sharpe_like, 4),
        max_consecutive_wins=max_cw,
        max_consecutive_losses=max_cl,
        avg_duration_sec=round(float(durations.mean()), 2),
        avg_mfe_r=round(float(mfes.mean()), 4),
        avg_mae_r=round(float(maes.mean()), 4),
        exit_efficiency=round(exit_efficiency, 4),
        avg_profit_giveback_r=round(float(givebacks.mean()), 4),
        management_induced_losses=mgmt_induced_losses,
        risk_of_ruin_est=round(ror, 6),
    )
