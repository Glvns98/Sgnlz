"""Adaptive Stop Management: monotonic breakeven, profit-lock, and dynamic trailing stops."""

from __future__ import annotations

import math

from bot.config.settings import ManagementSettings, SymbolProfile
from bot.core.enums import (
    MomentumState,
    MomentumTransition,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import MarketStateSnapshot, Position


class AdaptiveStopManager:
    """Computes monotonic stop adjustments that never widen risk away from price."""

    def __init__(self, settings: ManagementSettings) -> None:
        self.settings = settings

    @staticmethod
    def is_better_stop(direction: TradeDirection, candidate: float, current: float) -> bool:
        if direction == TradeDirection.BUY:
            return candidate > current
        return candidate < current

    def clamp_monotonic(self, position: Position, candidate: float) -> float:
        """Return candidate stop only if it tightens protection and stays behind current price."""
        if position.direction == TradeDirection.BUY:
            max_safe = position.current_price - max(position.initial_risk_distance * 0.05, 1e-8)
            bounded = min(candidate, max_safe)
            return max(position.current_stop, bounded)
        min_safe = position.current_price + max(position.initial_risk_distance * 0.05, 1e-8)
        bounded = max(candidate, min_safe)
        return min(position.current_stop, bounded)

    def price_for_r(self, position: Position, r_level: float) -> float:
        offset = r_level * position.initial_risk_distance
        if position.direction == TradeDirection.BUY:
            return position.entry_price + offset
        return position.entry_price - offset

    def compute_breakeven_stop(self, position: Position) -> float:
        target = self.price_for_r(position, self.settings.breakeven_buffer_r)
        return self.clamp_monotonic(position, target)

    def compute_profit_lock_stop(self, position: Position, lock_r: float) -> float:
        target = self.price_for_r(position, lock_r)
        return self.clamp_monotonic(position, target)

    def compute_dynamic_trailing_stop(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile | None = None,
    ) -> float:
        """Adapt trailing distance to momentum strength, acceleration, volatility, squeeze state & age."""
        snap = market_state.squeeze
        atr = (
            snap.range_ma
            if (snap.is_ready and not math.isnan(snap.range_ma) and snap.range_ma > 0)
            else position.initial_risk_distance * 0.5
        )
        base_factor = symbol_cfg.trailing_factor if symbol_cfg else 1.25

        is_buy = position.direction == TradeDirection.BUY
        aligned_strong = (is_buy and market_state.momentum_state == MomentumState.STRONG_BULLISH) or (
            not is_buy and market_state.momentum_state == MomentumState.STRONG_BEARISH
        )
        accelerating = (is_buy and market_state.has_transition(MomentumTransition.BULLISH_ACCELERATION)) or (
            not is_buy and market_state.has_transition(MomentumTransition.BEARISH_ACCELERATION)
        )
        decelerating = (is_buy and market_state.has_transition(MomentumTransition.BULLISH_DECELERATION)) or (
            not is_buy and market_state.has_transition(MomentumTransition.BEARISH_DECELERATION)
        )

        # Give strong trends room; tighten on deceleration, exhaustion, re-compression, or high age
        mult = base_factor
        if aligned_strong and accelerating:
            mult *= 1.35
        elif aligned_strong:
            mult *= 1.15
        if decelerating:
            mult *= 0.78
        if market_state.has_transition(MomentumTransition.MOMENTUM_EXHAUSTION):
            mult *= 0.65
        if market_state.squeeze_state == SqueezeState.SQUEEZE_ON:
            mult *= 0.80

        cur_r = position.unrealized_r()
        if cur_r >= self.settings.runner_trigger_r:
            mult *= 0.90
        if position.bars_held >= self.settings.stagnation_bars * 2:
            mult *= 0.85

        trail_dist = max(atr * mult, position.initial_risk_distance * 0.25)
        anchor = position.mfe_price if cur_r >= self.settings.strong_profit_trigger_r else position.current_price
        raw_stop = (anchor - trail_dist) if is_buy else (anchor + trail_dist)

        # Also ensure a minimum profit floor once in profit lock / runner tiers
        if cur_r >= self.settings.strong_profit_trigger_r:
            floor = self.price_for_r(position, self.settings.strong_profit_floor_r)
            raw_stop = max(raw_stop, floor) if is_buy else min(raw_stop, floor)

        return self.clamp_monotonic(position, raw_stop)

    @staticmethod
    def check_stop_hit(position: Position, bar_high: float, bar_low: float) -> bool:
        if position.direction == TradeDirection.BUY:
            return bar_low <= position.current_stop
        return bar_high >= position.current_stop
