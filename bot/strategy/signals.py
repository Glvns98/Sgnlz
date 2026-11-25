"""Signal Engine: emits structured TradeSignal events subordinate to Trade Management."""

from __future__ import annotations

import hashlib
import math

from bot.config.settings import SymbolProfile
from bot.core.enums import (
    MarketRegime,
    MomentumState,
    MomentumTransition,
    SignalType,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import MarketStateSnapshot, Position, TradeSignal


class SignalEngine:
    """Produces structured opportunity and management-awareness events from MarketStateSnapshot."""

    def __init__(self) -> None:
        self._last_opportunity_bar: dict[str, int] = {}
        self._bar_counter: dict[str, int] = {}
        self._recent_fired_bars: dict[str, int] = {}

    def reset(self) -> None:
        self._last_opportunity_bar.clear()
        self._bar_counter.clear()
        self._recent_fired_bars.clear()

    def evaluate(
        self,
        state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile,
        active_position: Position | None = None,
    ) -> TradeSignal:
        """Evaluate current market state and return a structured TradeSignal."""
        sym = state.symbol
        bar_idx = self._bar_counter.get(sym, 0) + 1
        self._bar_counter[sym] = bar_idx

        snap = state.squeeze
        if snap.sqz_fired:
            self._recent_fired_bars[sym] = 3
        elif sym in self._recent_fired_bars and self._recent_fired_bars[sym] > 0:
            self._recent_fired_bars[sym] -= 1

        if not snap.is_ready or state.regime == MarketRegime.WARMUP:
            return self._no_action(state, symbol_cfg, ("indicator_warmup",))

        range_ma = snap.range_ma if (not math.isnan(snap.range_ma) and snap.range_ma > 0) else max(state.high - state.low, state.close * 0.002)
        risk_dist = max(range_ma * symbol_cfg.atr_stop_multiple, state.close * 0.001)

        # If a position is already open on this symbol, emit diagnostic management-awareness signals
        if active_position is not None:
            return self._evaluate_active_position_signal(state, symbol_cfg, active_position, risk_dist)

        if state.regime == MarketRegime.ABNORMAL_VOLATILITY:
            return self._no_action(state, symbol_cfg, ("abnormal_volatility_regime",))

        if state.squeeze_state == SqueezeState.SQUEEZE_ON:
            return self._no_action(state, symbol_cfg, ("compression_active_wait_release",))

        last_opp = self._last_opportunity_bar.get(sym, -9999)
        if symbol_cfg.cooldown_bars > 0 and (bar_idx - last_opp) <= symbol_cfg.cooldown_bars:
            return self._no_action(state, symbol_cfg, ("cooldown_active",))

        direction, confidence, reasons = self._score_opportunity(state, symbol_cfg)
        if direction is None or confidence < symbol_cfg.min_confidence:
            return self._no_action(state, symbol_cfg, reasons or ("insufficient_edge_confluence",))

        self._last_opportunity_bar[sym] = bar_idx

        stop_dist = risk_dist
        if (
            direction == TradeDirection.BUY
            and state.recent_structure_low > 0
            and state.recent_structure_low < state.close
        ):
            struct_dist = (state.close - state.recent_structure_low) + 0.35 * range_ma
            stop_dist = min(max(struct_dist, 0.85 * risk_dist), 1.50 * risk_dist)
        elif direction == TradeDirection.SELL and state.recent_structure_high > state.close:
            struct_dist = (state.recent_structure_high - state.close) + 0.35 * range_ma
            stop_dist = min(max(struct_dist, 0.85 * risk_dist), 1.50 * risk_dist)

        suggested_stop = state.close - stop_dist if direction == TradeDirection.BUY else state.close + stop_dist
        sig_type = (
            SignalType.NEW_OPPORTUNITY
            if (snap.sqz_fired or self._recent_fired_bars.get(sym, 0) > 0)
            else SignalType.CONTINUATION
        )
        if snap.sqz_fired:
            entry_style = "ENTRY_A_IGNITION"
        elif (
            self._recent_fired_bars.get(sym, 0) > 0
            or (direction == TradeDirection.BUY and state.swing_high > 0 and state.close >= state.swing_high)
            or (direction == TradeDirection.SELL and state.swing_low > 0 and state.close <= state.swing_low)
        ):
            entry_style = "ENTRY_B_CONFIRMATION"
        else:
            entry_style = "ENTRY_C_RETEST"

        sig_id = self._make_id(state, sig_type, direction)
        return TradeSignal(
            signal_id=sig_id,
            symbol=sym,
            direction=direction,
            signal_type=sig_type,
            squeeze_state=state.squeeze_state,
            momentum_state=state.momentum_state,
            momentum_strength=snap.momentum_strength,
            confidence=confidence,
            regime=state.regime,
            timestamp=state.timestamp,
            timeframe_sec=state.timeframe_sec,
            entry_price=state.close,
            suggested_stop=suggested_stop,
            initial_risk_distance=stop_dist,
            reasons=tuple(reasons),
            metadata={
                "normalized_momentum": round(snap.normalized_momentum, 4),
                "range_ma": range_ma,
                "entry_style": entry_style,
                "engine_stage": state.engine_stage,
                "systematic_state": state.systematic_state,
                "overhead_room_atr": round(state.overhead_room_atr, 2),
                "underneath_room_atr": round(state.underneath_room_atr, 2),
            },
        )

    def _score_opportunity(
        self,
        state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile,
    ) -> tuple[TradeDirection | None, float, list[str]]:
        snap = state.squeeze
        reasons: list[str] = []
        recently_fired = snap.sqz_fired or self._recent_fired_bars.get(state.symbol, 0) > 0

        if state.momentum_state in (MomentumState.STRONG_BULLISH, MomentumState.WEAK_BULLISH) and (
            state.has_transition(MomentumTransition.BULLISH_ACCELERATION)
            or state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        ):
            direction = TradeDirection.BUY
        elif state.momentum_state in (MomentumState.STRONG_BEARISH, MomentumState.WEAK_BEARISH) and (
            state.has_transition(MomentumTransition.BEARISH_ACCELERATION)
            or state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)
        ):
            direction = TradeDirection.SELL
        else:
            return None, 0.0, ["momentum_not_accelerating"]

        if state.squeeze_state != SqueezeState.SQUEEZE_OFF and not recently_fired:
            return None, 0.0, ["squeeze_not_off"]

        score = 0.42
        if snap.sqz_fired:
            score += 0.22
            reasons.append("squeeze_release_bar")
        elif recently_fired:
            score += 0.16
            reasons.append("recent_squeeze_release")
        else:
            score += 0.08
            reasons.append("expansion_continuation")

        if (direction == TradeDirection.BUY and state.momentum_state == MomentumState.STRONG_BULLISH) or (
            direction == TradeDirection.SELL and state.momentum_state == MomentumState.STRONG_BEARISH
        ):
            score += 0.18
            reasons.append("strong_aligned_momentum")
        else:
            score += 0.07
            reasons.append("moderate_aligned_momentum")

        if (direction == TradeDirection.BUY and state.has_transition(MomentumTransition.ZERO_CROSS_UP)) or (
            direction == TradeDirection.SELL and state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)
        ):
            score += 0.08
            reasons.append("fresh_zero_cross")

        if state.regime == MarketRegime.CHOP:
            score -= 0.12
            reasons.append("chop_penalty")

        # Tier 4 — 15m Price Structure Confirmation
        price_confirmed = (
            state.structure_bullish_confirmed
            if direction == TradeDirection.BUY
            else state.structure_bearish_confirmed
        )
        if price_confirmed:
            score += 0.06
            reasons.append("tier4_price_structure_confirmed")
        else:
            score -= 0.14
            reasons.append("unconfirmed_price_candle")

        # Tier 5 — Location Filter (Setup A Breakout Room vs Setup B S/R Barrier Caution)
        if direction == TradeDirection.BUY:
            if state.overhead_room_atr < 0.50:
                score -= 0.18
                reasons.append("caution_overhead_resistance")
            elif state.overhead_room_atr >= 0.85:
                reasons.append("tier5_clean_location_room")
        else:
            if state.underneath_room_atr < 0.50:
                score -= 0.18
                reasons.append("caution_underlying_support")
            elif state.underneath_room_atr >= 0.85:
                reasons.append("tier5_clean_location_room")

        return direction, max(0.0, min(1.0, score)), reasons

    def _evaluate_active_position_signal(
        self,
        state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile,
        position: Position,
        risk_dist: float,
    ) -> TradeSignal:
        is_buy = position.direction == TradeDirection.BUY
        reversal = (is_buy and state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)) or (
            not is_buy and state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        )
        weakening = (is_buy and state.has_transition(MomentumTransition.BULLISH_DECELERATION)) or (
            not is_buy and state.has_transition(MomentumTransition.BEARISH_DECELERATION)
        )
        strengthening = (is_buy and state.has_transition(MomentumTransition.BULLISH_ACCELERATION)) or (
            not is_buy and state.has_transition(MomentumTransition.BEARISH_ACCELERATION)
        )

        if state.regime == MarketRegime.ABNORMAL_VOLATILITY or (
            reversal and state.momentum_state in (MomentumState.STRONG_BULLISH, MomentumState.STRONG_BEARISH)
        ):
            sig_type = SignalType.EXIT_WARNING
            reason = ("active_position_exit_warning",)
        elif reversal or state.has_transition(MomentumTransition.MOMENTUM_EXHAUSTION):
            sig_type = SignalType.REVERSAL_WARNING
            reason = ("active_position_reversal_or_exhaustion",)
        elif weakening:
            sig_type = SignalType.MOMENTUM_WEAKENING
            reason = ("active_position_momentum_weakening",)
        elif strengthening:
            sig_type = SignalType.MOMENTUM_STRENGTHENING
            reason = ("active_position_momentum_strengthening",)
        else:
            sig_type = SignalType.NO_ACTION
            reason = ("active_position_steady",)

        return TradeSignal(
            signal_id=self._make_id(state, sig_type, position.direction),
            symbol=state.symbol,
            direction=position.direction,
            signal_type=sig_type,
            squeeze_state=state.squeeze_state,
            momentum_state=state.momentum_state,
            momentum_strength=state.squeeze.momentum_strength,
            confidence=state.squeeze.momentum_strength,
            regime=state.regime,
            timestamp=state.timestamp,
            timeframe_sec=state.timeframe_sec,
            entry_price=state.close,
            suggested_stop=position.current_stop,
            initial_risk_distance=risk_dist,
            reasons=reason,
        )

    def _no_action(
        self,
        state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile,
        reasons: tuple[str, ...],
    ) -> TradeSignal:
        return TradeSignal(
            signal_id=self._make_id(state, SignalType.NO_ACTION, None),
            symbol=state.symbol,
            direction=None,
            signal_type=SignalType.NO_ACTION,
            squeeze_state=state.squeeze_state,
            momentum_state=state.momentum_state,
            momentum_strength=state.squeeze.momentum_strength,
            confidence=0.0,
            regime=state.regime,
            timestamp=state.timestamp,
            timeframe_sec=state.timeframe_sec,
            entry_price=state.close,
            suggested_stop=state.close,
            initial_risk_distance=0.0,
            reasons=reasons,
        )

    @staticmethod
    def _make_id(
        state: MarketStateSnapshot,
        sig_type: SignalType,
        direction: TradeDirection | None,
    ) -> str:
        raw = f"{state.symbol}:{state.timeframe_sec}:{state.timestamp.isoformat()}:{sig_type.value}:{direction}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
