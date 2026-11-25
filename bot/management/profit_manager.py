"""Profit State Manager: staged R-progression & continuation vs reversal assessment."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config.settings import ManagementSettings
from bot.core.enums import (
    MarketRegime,
    MomentumState,
    MomentumTransition,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import MarketStateSnapshot, Position


@dataclass(frozen=True)
class ProfitAssessment:
    """Detailed evaluation of unrealized profit and continuation probability."""

    profit_tier: str
    recommendation: str  # LET_RUN | HOLD | PROTECT | TRAIL | REDUCE | EXIT
    continuation_score: float
    reversal_risk: float
    giveback_r: float
    giveback_ratio: float
    reason: str


class ProfitStateManager:
    """Evaluates profit tiers (+0.25R -> +2.00R+) without choking trends prematurely."""

    def __init__(self, settings: ManagementSettings) -> None:
        self.settings = settings

    @staticmethod
    def classify_profit_tier(unrealized_r: float) -> str:
        if unrealized_r < 0.0:
            return "UNDERWATER"
        if unrealized_r < 0.25:
            return "ENTRY_BUFFER"
        if unrealized_r < 0.50:
            return "+0.25R"
        if unrealized_r < 1.00:
            return "+0.50R"
        if unrealized_r < 1.50:
            return "+1.00R"
        if unrealized_r < 2.00:
            return "+1.50R"
        return "+2.00R+"

    def evaluate(self, position: Position, market_state: MarketStateSnapshot) -> ProfitAssessment:
        cur_r = position.unrealized_r()
        tier = self.classify_profit_tier(cur_r)
        giveback_r = position.profit_giveback_r()
        giveback_ratio = position.profit_giveback_ratio()

        is_buy = position.direction == TradeDirection.BUY
        aligned_strong = (is_buy and market_state.momentum_state == MomentumState.STRONG_BULLISH) or (
            not is_buy and market_state.momentum_state == MomentumState.STRONG_BEARISH
        )
        aligned_weak = (is_buy and market_state.momentum_state == MomentumState.WEAK_BULLISH) or (
            not is_buy and market_state.momentum_state == MomentumState.WEAK_BEARISH
        )
        accelerating = (is_buy and market_state.has_transition(MomentumTransition.BULLISH_ACCELERATION)) or (
            not is_buy and market_state.has_transition(MomentumTransition.BEARISH_ACCELERATION)
        )
        decelerating = (is_buy and market_state.has_transition(MomentumTransition.BULLISH_DECELERATION)) or (
            not is_buy and market_state.has_transition(MomentumTransition.BEARISH_DECELERATION)
        )
        opposing_cross = (is_buy and market_state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)) or (
            not is_buy and market_state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        )
        opposing_momentum = (
            is_buy and market_state.momentum_state in (MomentumState.WEAK_BEARISH, MomentumState.STRONG_BEARISH)
        ) or (
            not is_buy and market_state.momentum_state in (MomentumState.WEAK_BULLISH, MomentumState.STRONG_BULLISH)
        )

        # Continuation score [0..1]
        cont = 0.35
        if aligned_strong:
            cont += 0.30
        elif aligned_weak:
            cont += 0.12
        if accelerating:
            cont += 0.20
        if market_state.squeeze_state == SqueezeState.SQUEEZE_OFF:
            cont += 0.10
        if market_state.htf_bias is not None:
            htf_aligned = (
                is_buy and market_state.htf_bias in (MomentumState.STRONG_BULLISH, MomentumState.WEAK_BULLISH)
            ) or (
                not is_buy and market_state.htf_bias in (MomentumState.STRONG_BEARISH, MomentumState.WEAK_BEARISH)
            )
            if htf_aligned:
                cont += 0.10
        if decelerating:
            cont -= 0.18
        if opposing_momentum:
            cont -= 0.25
        cont = max(0.0, min(1.0, cont))

        # Reversal risk [0..1]
        rev = 0.15
        if decelerating:
            rev += 0.20
        if market_state.has_transition(MomentumTransition.MOMENTUM_EXHAUSTION):
            rev += 0.28
        if opposing_cross:
            rev += 0.35
        if opposing_momentum:
            rev += 0.25
        if market_state.regime == MarketRegime.MOMENTUM_EXHAUSTION:
            rev += 0.15
        if giveback_ratio >= self.settings.max_giveback_ratio and position.mfe_r >= self.settings.profit_lock_trigger_r:
            rev += 0.20
        rev = max(0.0, min(1.0, rev))

        # Decision selection respecting trend breathing room
        if cur_r >= self.settings.profit_lock_trigger_r and rev >= 0.72 and position.decay_bars >= self.settings.momentum_exit_min_decay_bars:
            rec = "EXIT"
            reason = "high_reversal_risk_after_profit"
        elif cur_r >= self.settings.profit_lock_trigger_r and giveback_ratio >= self.settings.max_giveback_ratio and rev >= 0.50:
            rec = "PROTECT" if position.remaining_fraction <= 0.55 else "REDUCE"
            reason = "profit_giveback_protection"
        elif cur_r >= self.settings.strong_profit_trigger_r and cont >= 0.65 and rev < 0.45:
            rec = "LET_RUN"
            reason = "strong_trend_continuation"
        elif cur_r >= self.settings.profit_lock_trigger_r:
            rec = "TRAIL" if cont >= 0.50 else "PROTECT"
            reason = "profit_lock_and_trail" if rec == "TRAIL" else "momentum_softening_protect_profit"
        elif cur_r >= self.settings.breakeven_trigger_r:
            rec = "PROTECT"
            reason = "breakeven_threshold_reached"
        elif cur_r >= 0.25 and aligned_strong and accelerating:
            rec = "LET_RUN"
            reason = "early_profitable_acceleration"
        else:
            rec = "HOLD"
            reason = "within_normal_fluctuation"

        return ProfitAssessment(
            profit_tier=tier,
            recommendation=rec,
            continuation_score=cont,
            reversal_risk=rev,
            giveback_r=giveback_r,
            giveback_ratio=giveback_ratio,
            reason=reason,
        )
