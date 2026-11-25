"""Multi-Policy Backtest Runner & Out-of-Sample Walk-Forward Validator (Sections 28 & 29)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from bot.analytics.management_analysis import compare_policies_before_after
from bot.analytics.performance import compute_performance_metrics
from bot.analytics.trade_analysis import PostTradeLearningEngine
from bot.backtest.replay import HistoricalReplayEngine, ReplayResult
from bot.config.settings import EngineSettings
from bot.core.enums import ManagementPolicy
from bot.core.models import Candle, ClosedTrade


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_bars: int
    validate_bars: int
    oos_bars: int
    best_train_policy: str
    train_expectancy_r: float
    validate_expectancy_r: float
    oos_expectancy_r: float
    oos_profit_factor: float
    oos_trades: int
    degradation_ratio: float


class BacktestRunner:
    """Runs controlled 5-policy experiments on identical market history."""

    ALL_POLICIES = (
        ManagementPolicy.STATIC_EXIT,
        ManagementPolicy.BREAKEVEN_ONLY,
        ManagementPolicy.TRAILING_ONLY,
        ManagementPolicy.MOMENTUM_EXIT,
        ManagementPolicy.DYNAMIC_MANAGEMENT,
    )

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings
        self.replay_engine = HistoricalReplayEngine(settings)
        self.learning_engine = PostTradeLearningEngine(min_sample_size=3)

    def run_policy_comparison(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        policies: tuple[ManagementPolicy, ...] = ALL_POLICIES,
    ) -> dict[str, Any]:
        results: dict[ManagementPolicy, ReplayResult] = {}
        policy_trades: dict[ManagementPolicy, list[ClosedTrade]] = {}

        for pol in policies:
            res = self.replay_engine.replay(candles_by_symbol, policy=pol)
            results[pol] = res
            policy_trades[pol] = res.trades

        comparison = compare_policies_before_after(
            policy_trades,
            starting_balance=self.settings.risk.account_balance,
        )
        dyn_trades = policy_trades.get(ManagementPolicy.DYNAMIC_MANAGEMENT, [])
        learning = self.learning_engine.analyze(dyn_trades, config_version=self.settings.config_version)

        return {
            "comparison": comparison,
            "learning": learning,
            "ending_balances": {pol.value: res.ending_balance for pol, res in results.items()},
        }


class WalkForwardValidator:
    """Rolling Train -> Validate -> Out-of-Sample evaluation to detect overfitting."""

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings
        self.replay_engine = HistoricalReplayEngine(settings)

    def run(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        folds: int = 4,
    ) -> dict[str, Any]:
        if not candles_by_symbol:
            return {"folds": [], "warnings": ["no_market_data"], "summary": {}}

        min_len = min(len(v) for v in candles_by_symbol.values())
        if min_len < 300 or folds < 1:
            return {"folds": [], "warnings": ["insufficient_bars_for_walkforward"], "summary": {}}

        fold_size = min_len // (folds + 1)
        train_len = max(140, int(fold_size * 0.65))
        val_len = max(80, int(fold_size * 0.35))
        oos_len = max(100, fold_size)

        fold_reports: list[WalkForwardFold] = []
        warnings: list[str] = []
        candidate_policies = (
            ManagementPolicy.STATIC_EXIT,
            ManagementPolicy.TRAILING_ONLY,
            ManagementPolicy.DYNAMIC_MANAGEMENT,
        )

        for f in range(folds):
            t_start = f * (oos_len // 2)
            t_end = t_start + train_len
            v_end = t_end + val_len
            o_end = min(min_len, v_end + oos_len)
            if o_end - v_end < 80:
                break

            train_slice = {s: c[t_start:t_end] for s, c in candles_by_symbol.items()}
            val_slice = {s: c[t_end:v_end] for s, c in candles_by_symbol.items()}
            oos_slice = {s: c[v_end:o_end] for s, c in candles_by_symbol.items()}

            best_pol = ManagementPolicy.DYNAMIC_MANAGEMENT
            best_score = -999.0
            for pol in candidate_policies:
                tr_res = self.replay_engine.replay(train_slice, policy=pol)
                tr_m = compute_performance_metrics(tr_res.trades, self.settings.risk.account_balance)
                if tr_m.expectancy_r > best_score:
                    best_score = tr_m.expectancy_r
                    best_pol = pol

            val_res = self.replay_engine.replay(val_slice, policy=best_pol)
            val_m = compute_performance_metrics(val_res.trades, self.settings.risk.account_balance)

            oos_res = self.replay_engine.replay(oos_slice, policy=best_pol)
            oos_m = compute_performance_metrics(oos_res.trades, self.settings.risk.account_balance)

            deg = (
                oos_m.expectancy_r / best_score
                if abs(best_score) > 1e-6
                else (1.0 if oos_m.expectancy_r >= 0 else 0.0)
            )
            fold_reports.append(
                WalkForwardFold(
                    fold_index=f + 1,
                    train_bars=t_end - t_start,
                    validate_bars=v_end - t_end,
                    oos_bars=o_end - v_end,
                    best_train_policy=best_pol.value,
                    train_expectancy_r=round(best_score, 4),
                    validate_expectancy_r=round(val_m.expectancy_r, 4),
                    oos_expectancy_r=round(oos_m.expectancy_r, 4),
                    oos_profit_factor=round(oos_m.profit_factor, 4),
                    oos_trades=oos_m.total_trades,
                    degradation_ratio=round(deg, 4),
                )
            )

        selected_policies = {fr.best_train_policy for fr in fold_reports}
        if len(selected_policies) > 2:
            warnings.append("policy_instability_across_folds")
        avg_oos_r = (
            sum(fr.oos_expectancy_r for fr in fold_reports) / len(fold_reports)
            if fold_reports
            else 0.0
        )
        if avg_oos_r <= 0:
            warnings.append("negative_or_flat_oos_expectancy_review_before_live")

        return {
            "folds": [asdict(fr) for fr in fold_reports],
            "warnings": warnings,
            "summary": {
                "folds_evaluated": len(fold_reports),
                "avg_oos_expectancy_r": round(avg_oos_r, 4),
                "policy_stability": "STABLE" if len(selected_policies) <= 2 else "UNSTABLE",
            },
        }
