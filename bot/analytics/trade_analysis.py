"""Deterministic Post-Trade Learning Engine (Section 20): versioned insights, never auto-mutating live config."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from bot.core.models import ClosedTrade


@dataclass(frozen=True)
class LearningInsight:
    pattern_key: str
    sample_size: int
    win_rate: float
    avg_r: float
    avg_mfe_r: float
    conclusion: str


@dataclass(frozen=True)
class ProposedPolicyChange:
    version: str
    status: str  # Always PROPOSED_FOR_REVIEW — never auto-applied
    parameter: str
    rationale: str
    evidence_sample_size: int
    expected_r_impact: float


class PostTradeLearningEngine:
    """Identifies deterministic statistical patterns across Squeeze, momentum, duration, and symbols."""

    def __init__(self, min_sample_size: int = 5) -> None:
        self.min_sample_size = min_sample_size

    def analyze(self, trades: list[ClosedTrade], config_version: str = "v1") -> dict[str, Any]:
        if not trades:
            return {"insights": [], "proposed_changes": [], "config_version": config_version}

        insights: list[LearningInsight] = []
        proposals: list[ProposedPolicyChange] = []

        # 1. Analyze entry squeeze state + momentum magnitude
        strong_entry = [t for t in trades if abs(t.momentum_at_entry) > 0]
        if len(strong_entry) >= self.min_sample_size:
            med_mom = sorted(abs(t.momentum_at_entry) for t in strong_entry)[len(strong_entry) // 2]
            hi_mom = [t for t in strong_entry if abs(t.momentum_at_entry) >= med_mom]
            lo_mom = [t for t in strong_entry if abs(t.momentum_at_entry) < med_mom]
            for label, grp in (("strong_entry_momentum", hi_mom), ("weak_entry_momentum", lo_mom)):
                if len(grp) >= self.min_sample_size:
                    avg_r = sum(t.r_multiple for t in grp) / len(grp)
                    wr = sum(1 for t in grp if t.r_multiple > 0) / len(grp)
                    avg_mfe = sum(t.mfe for t in grp) / len(grp)
                    insights.append(
                        LearningInsight(
                            pattern_key=label,
                            sample_size=len(grp),
                            win_rate=round(wr, 4),
                            avg_r=round(avg_r, 4),
                            avg_mfe_r=round(avg_mfe, 4),
                            conclusion="favorable_continuation" if avg_r > 0.1 else "poor_continuation",
                        )
                    )

        # 2. Stagnant / long-duration trades
        long_trades = [t for t in trades if t.bars_held >= 12]
        if len(long_trades) >= self.min_sample_size:
            avg_r = sum(t.r_multiple for t in long_trades) / len(long_trades)
            wr = sum(1 for t in long_trades if t.r_multiple > 0) / len(long_trades)
            avg_mfe = sum(t.mfe for t in long_trades) / len(long_trades)
            insights.append(
                LearningInsight(
                    pattern_key="long_duration_trades_12plus_bars",
                    sample_size=len(long_trades),
                    win_rate=round(wr, 4),
                    avg_r=round(avg_r, 4),
                    avg_mfe_r=round(avg_mfe, 4),
                    conclusion="low_expectancy_stagnation" if avg_r < 0.0 else "trend_runners_paying",
                )
            )
            if avg_r < -0.15:
                proposals.append(
                    ProposedPolicyChange(
                        version=f"{config_version}-cand-1",
                        status="PROPOSED_FOR_REVIEW",
                        parameter="management.stagnation_bars",
                        rationale=f"Trades held >=12 bars averaged {avg_r:+.2f}R; consider tightening stagnation threshold after walk-forward verification.",
                        evidence_sample_size=len(long_trades),
                        expected_r_impact=round(abs(avg_r) * 0.35, 4),
                    )
                )

        # 3. Symbol-level edge check
        by_symbol: dict[str, list[ClosedTrade]] = {}
        for t in trades:
            by_symbol.setdefault(t.symbol, []).append(t)
        for sym, grp in sorted(by_symbol.items()):
            if len(grp) >= self.min_sample_size:
                avg_r = sum(t.r_multiple for t in grp) / len(grp)
                wr = sum(1 for t in grp if t.r_multiple > 0) / len(grp)
                avg_mfe = sum(t.mfe for t in grp) / len(grp)
                insights.append(
                    LearningInsight(
                        pattern_key=f"symbol:{sym}",
                        sample_size=len(grp),
                        win_rate=round(wr, 4),
                        avg_r=round(avg_r, 4),
                        avg_mfe_r=round(avg_mfe, 4),
                        conclusion="positive_symbol_expectancy" if avg_r > 0 else "underperforming_symbol",
                    )
                )
                if avg_r < -0.25 and not math.isnan(avg_r):
                    proposals.append(
                        ProposedPolicyChange(
                            version=f"{config_version}-cand-{sym}",
                            status="PROPOSED_FOR_REVIEW",
                            parameter=f"symbols.{sym}.min_confidence",
                            rationale=f"{sym} averaged {avg_r:+.2f}R over {len(grp)} trades; propose raising min_confidence for out-of-sample review.",
                            evidence_sample_size=len(grp),
                            expected_r_impact=0.15,
                        )
                    )

        return {
            "config_version": config_version,
            "insights": [asdict(i) for i in insights],
            "proposed_changes": [asdict(p) for p in proposals],
        }
