"""Exit & Trade Recovery Engine: thesis validation, time stagnation, hard invalidation, and momentum exits."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config.settings import ManagementSettings
from bot.core.enums import (
    ExitReason,
    MarketRegime,
    MomentumState,
    MomentumTransition,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import MarketStateSnapshot, Position


@dataclass(frozen=True)
class ExitAssessment:
    should_exit: bool
    emergency: bool
    tighten_stop: bool
    reduce_risk: bool
    exit_reason: ExitReason | None
    reason_code: str
    thesis_status: str  # INTACT | UNCERTAIN | INVALIDATED


class ExitEngine:
    """Evaluates hard invalidation, abnormal regimes, thesis recovery, stagnation, and momentum decay."""

    def __init__(self, settings: ManagementSettings) -> None:
        self.settings = settings

    def evaluate(self, position: Position, market_state: MarketStateSnapshot) -> ExitAssessment:
        cur_r = position.unrealized_r()
        is_buy = position.direction == TradeDirection.BUY

        # 1. Hard catastrophic invalidation
        if cur_r <= self.settings.hard_invalidation_r:
            return ExitAssessment(
                should_exit=True,
                emergency=True,
                tighten_stop=False,
                reduce_risk=False,
                exit_reason=ExitReason.HARD_INVALIDATION,
                reason_code="hard_r_invalidation_breached",
                thesis_status="INVALIDATED",
            )

        # 2. Abnormal volatility regime protection
        if market_state.regime == MarketRegime.ABNORMAL_VOLATILITY:
            if cur_r < 0.35:
                return ExitAssessment(
                    should_exit=True,
                    emergency=True,
                    tighten_stop=False,
                    reduce_risk=False,
                    exit_reason=ExitReason.ABNORMAL_REGIME,
                    reason_code="abnormal_volatility_exit",
                    thesis_status="INVALIDATED",
                )
            return ExitAssessment(
                should_exit=False,
                emergency=False,
                tighten_stop=True,
                reduce_risk=False,
                exit_reason=None,
                reason_code="abnormal_volatility_tighten_protection",
                thesis_status="UNCERTAIN",
            )

        opposing_strong = (is_buy and market_state.momentum_state == MomentumState.STRONG_BEARISH) or (
            not is_buy and market_state.momentum_state == MomentumState.STRONG_BULLISH
        )
        opposing_weak = (is_buy and market_state.momentum_state == MomentumState.WEAK_BEARISH) or (
            not is_buy and market_state.momentum_state == MomentumState.WEAK_BULLISH
        )
        opposing_cross = (is_buy and market_state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)) or (
            not is_buy and market_state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        )
        aligned_momentum = (
            is_buy and market_state.momentum_state in (MomentumState.STRONG_BULLISH, MomentumState.WEAK_BULLISH)
        ) or (
            not is_buy and market_state.momentum_state in (MomentumState.STRONG_BEARISH, MomentumState.WEAK_BEARISH)
        )

        # 3. Trade Recovery Logic (NO averaging down / NO martingale — thesis validation only)
        if cur_r < 0.0:
            if opposing_strong and (opposing_cross or position.decay_bars >= self.settings.momentum_exit_min_decay_bars):
                return ExitAssessment(
                    should_exit=True,
                    emergency=False,
                    tighten_stop=False,
                    reduce_risk=False,
                    exit_reason=ExitReason.THESIS_INVALIDATION,
                    reason_code="thesis_invalidated_opposing_momentum",
                    thesis_status="INVALIDATED",
                )
            if opposing_weak or market_state.squeeze_state == SqueezeState.SQUEEZE_ON:
                if position.bars_held >= self.settings.stagnation_bars and cur_r < -0.35:
                    return ExitAssessment(
                        should_exit=True,
                        emergency=False,
                        tighten_stop=False,
                        reduce_risk=False,
                        exit_reason=ExitReason.TIME_STAGNATION,
                        reason_code="underwater_stagnant_recompression",
                        thesis_status="INVALIDATED",
                    )
                return ExitAssessment(
                    should_exit=False,
                    emergency=False,
                    tighten_stop=True,
                    reduce_risk=(position.remaining_fraction > 0.55 and cur_r <= -0.50),
                    exit_reason=None,
                    reason_code="thesis_uncertain_tighten_protection",
                    thesis_status="UNCERTAIN",
                )
            if aligned_momentum:
                return ExitAssessment(
                    should_exit=False,
                    emergency=False,
                    tighten_stop=False,
                    reduce_risk=False,
                    exit_reason=None,
                    reason_code="temporary_drawdown_thesis_intact",
                    thesis_status="INTACT",
                )

        # 4. Time-based stagnation management
        if position.bars_held >= self.settings.stagnation_bars and cur_r < self.settings.stagnation_min_r:
            if not aligned_momentum or market_state.regime in (MarketRegime.CHOP, MarketRegime.COMPRESSION):
                return ExitAssessment(
                    should_exit=True,
                    emergency=False,
                    tighten_stop=False,
                    reduce_risk=False,
                    exit_reason=ExitReason.TIME_STAGNATION,
                    reason_code="time_stagnation_low_momentum",
                    thesis_status="INVALIDATED",
                )

        # 5. Momentum deterioration exit (after sustained decay)
        if opposing_cross and opposing_strong and position.decay_bars >= self.settings.momentum_exit_min_decay_bars:
            return ExitAssessment(
                should_exit=True,
                emergency=False,
                tighten_stop=False,
                reduce_risk=False,
                exit_reason=ExitReason.MOMENTUM_DETERIORATION,
                reason_code="momentum_deterioration_reversal",
                thesis_status="INVALIDATED",
            )

        return ExitAssessment(
            should_exit=False,
            emergency=False,
            tighten_stop=False,
            reduce_risk=False,
            exit_reason=None,
            reason_code="thesis_intact",
            thesis_status="INTACT",
        )
