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
            self._recent_fired_bars[sym] = 2
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
        recently_fired = snap.sqz_fired or self._recent_fired_bars.get(sym, 0) > 0
        if direction == TradeDirection.BUY:
            candidates = [p for p in (state.recent_structure_low, state.compression_low if recently_fired else 0.0) if 0.0 < p < state.close]
            if candidates:
                anchor = max(candidates) if len(candidates) > 1 else candidates[0]
                struct_dist = (state.close - anchor) + 0.25 * range_ma
                min_d = range_ma if recently_fired else 0.85 * risk_dist
                stop_dist = min(max(struct_dist, min_d), 1.50 * risk_dist)
        else:
            candidates = [p for p in (state.recent_structure_high, state.compression_high if recently_fired else 0.0) if p > state.close]
            if candidates:
                anchor = min(candidates) if len(candidates) > 1 else candidates[0]
                struct_dist = (anchor - state.close) + 0.25 * range_ma
                min_d = range_ma if recently_fired else 0.85 * risk_dist
                stop_dist = min(max(struct_dist, min_d), 1.50 * risk_dist)

        suggested_stop = state.close - stop_dist if direction == TradeDirection.BUY else state.close + stop_dist
        if "squeeze_fire_early_entry" in reasons:
            entry_style = "SQUEEZE_FIRE"
            sig_type = SignalType.NEW_OPPORTUNITY
        elif "zero_line_flip_early_entry" in reasons:
            entry_style = "ZERO_LINE_FLIP"
            sig_type = SignalType.NEW_OPPORTUNITY
        else:
            entry_style = "ZERO_LINE_BOUNCE"
            sig_type = SignalType.CONTINUATION

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
                "accel_run_bars": state.accel_run_bars,
            },
        )

    def _score_opportunity(
        self,
        state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile,
    ) -> tuple[TradeDirection | None, float, list[str]]:
        snap = state.squeeze
        recently_fired = snap.sqz_fired or self._recent_fired_bars.get(state.symbol, 0) > 0

        if state.squeeze_state != SqueezeState.SQUEEZE_OFF and not snap.sqz_fired:
            return None, 0.0, ["squeeze_not_off"]

        bull_accel = (snap.bcolor == "lime" and snap.val > 0.0) or state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        bear_accel = (snap.bcolor == "red" and snap.val < 0.0) or state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)

        if bull_accel and not bear_accel:
            direction = TradeDirection.BUY
        elif bear_accel and not bull_accel:
            direction = TradeDirection.SELL
        else:
            return None, 0.0, ["momentum_not_accelerating"]

        # Simple candle-close confirmation (Playbook Step 2 & Section 5)
        if direction == TradeDirection.BUY:
            candle_ok = (state.close >= state.open) if state.open > 0 else state.structure_bullish_confirmed
        else:
            candle_ok = (state.close <= state.open) if state.open > 0 else state.structure_bearish_confirmed
        if not candle_ok:
            return None, 0.0, ["trigger_candle_not_confirmed"]

        nm_abs = abs(snap.normalized_momentum)
        is_zero_flip = (
            direction == TradeDirection.BUY and state.has_transition(MomentumTransition.ZERO_CROSS_UP)
        ) or (
            direction == TradeDirection.SELL and state.has_transition(MomentumTransition.ZERO_CROSS_DOWN)
        )
        is_sqz_fire = snap.sqz_fired or (recently_fired and state.accel_run_bars <= 2)
        is_bounce = (
            (direction == TradeDirection.BUY and state.prev_bcolor == "green" and snap.bcolor == "lime")
            or (direction == TradeDirection.SELL and state.prev_bcolor == "maroon" and snap.bcolor == "red")
            or (state.accel_run_bars == 1 and state.prev_bcolor is not None)
        )

        reasons: list[str] = []
        if is_sqz_fire and nm_abs <= 1.85:
            score = 0.78
            reasons.append("squeeze_fire_early_entry")
            if snap.squeeze_bars >= 5:
                score += 0.08
                reasons.append("coiled_squeeze_cluster")
            if is_zero_flip:
                score += 0.08
                reasons.append("fresh_zero_cross")
        elif is_zero_flip and nm_abs <= 1.40:
            score = 0.74
            reasons.extend(["zero_line_flip_early_entry", "fresh_zero_cross"])
            if recently_fired:
                score += 0.08
                reasons.append("recent_squeeze_release")
            if nm_abs <= 0.65:
                score += 0.06
                reasons.append("early_trend_ignition")
        elif is_bounce and nm_abs <= 1.15:
            score = 0.68
            reasons.append("zero_line_bounce_resumption")
            if nm_abs <= 0.55:
                score += 0.08
                reasons.append("fresh_bounce_near_zero")
            if state.regime == MarketRegime.EXPANSION_TREND:
                score += 0.04
        else:
            return None, 0.0, ["late_mid_trend_already_running"]

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
