"""Market State Engine: Squeeze states, 5-tier momentum, transitions, regimes, and MTF context."""

from __future__ import annotations

import math
from collections import deque

from bot.core.enums import (
    MarketRegime,
    MomentumState,
    MomentumTransition,
    SqueezeState,
)
from bot.core.models import Candle, MarketStateSnapshot, SqueezeSnapshot
from bot.indicators.squeeze import SqueezeConfig, SqueezeMomentumIndicator


class MarketStateEngine:
    """Classifies market state & momentum trajectory from Squeeze.pine snapshots."""

    def __init__(self, symbol: str, timeframe_sec: int, squeeze_config: SqueezeConfig | None = None) -> None:
        self.symbol = symbol
        self.timeframe_sec = timeframe_sec
        self.indicator = SqueezeMomentumIndicator(config=squeeze_config)
        self._norm_history: deque[float] = deque(maxlen=12)
        self._val_history: deque[float] = deque(maxlen=12)
        self._zero_cross_history: deque[int] = deque(maxlen=10)
        self._last_state: MarketStateSnapshot | None = None

    @property
    def last_state(self) -> MarketStateSnapshot | None:
        return self._last_state

    def reset(self) -> None:
        self.indicator.reset()
        self._norm_history.clear()
        self._val_history.clear()
        self._zero_cross_history.clear()
        self._last_state = None

    def update(self, candle: Candle) -> MarketStateSnapshot:
        """Process one closed candle and emit a full MarketStateSnapshot."""
        snap = self.indicator.update(candle.high, candle.low, candle.close)
        squeeze_state = self._classify_squeeze(snap)
        momentum_state = self._classify_momentum(snap)
        transitions = self._detect_transitions(snap)
        bar_range = max(0.0, candle.high - candle.low)
        volatility_ratio = (
            bar_range / snap.range_ma
            if (snap.is_ready and not math.isnan(snap.range_ma) and snap.range_ma > 1e-12)
            else 1.0
        )
        regime = self._classify_regime(snap, squeeze_state, momentum_state, transitions, volatility_ratio)

        state = MarketStateSnapshot(
            symbol=candle.symbol,
            timeframe_sec=candle.timeframe_sec,
            timestamp=candle.timestamp,
            close=candle.close,
            high=candle.high,
            low=candle.low,
            squeeze=snap,
            squeeze_state=squeeze_state,
            momentum_state=momentum_state,
            transitions=transitions,
            regime=regime,
            volatility_ratio=volatility_ratio,
        )
        self._last_state = state
        return state

    @staticmethod
    def _classify_squeeze(snap: SqueezeSnapshot) -> SqueezeState:
        if math.isnan(snap.upper_bb) or math.isnan(snap.upper_kc):
            return SqueezeState.WARMUP
        if snap.sqz_on:
            return SqueezeState.SQUEEZE_ON
        if snap.sqz_off:
            return SqueezeState.SQUEEZE_OFF
        return SqueezeState.NO_SQUEEZE

    @staticmethod
    def _classify_momentum(snap: SqueezeSnapshot) -> MomentumState:
        if not snap.is_ready:
            return MomentumState.NEUTRAL
        nm = snap.normalized_momentum
        if nm >= 0.65:
            return MomentumState.STRONG_BULLISH
        if nm >= 0.15:
            return MomentumState.WEAK_BULLISH
        if nm <= -0.65:
            return MomentumState.STRONG_BEARISH
        if nm <= -0.15:
            return MomentumState.WEAK_BEARISH
        return MomentumState.NEUTRAL

    def _detect_transitions(self, snap: SqueezeSnapshot) -> tuple[MomentumTransition, ...]:
        if not snap.is_ready:
            return (MomentumTransition.STEADY,)

        transitions: list[MomentumTransition] = []
        val = snap.val
        prev = snap.prev_val
        norm = snap.normalized_momentum
        self._val_history.append(val)
        self._norm_history.append(norm)

        crossed = 0
        if prev is not None and not math.isnan(prev):
            if val > prev:
                transitions.append(MomentumTransition.INCREASING)
            elif val < prev:
                transitions.append(MomentumTransition.DECREASING)

            if val > 0.0 and prev <= 0.0:
                transitions.append(MomentumTransition.ZERO_CROSS_UP)
                transitions.append(MomentumTransition.MOMENTUM_REVERSAL)
                crossed = 1
            elif val < 0.0 and prev >= 0.0:
                transitions.append(MomentumTransition.ZERO_CROSS_DOWN)
                transitions.append(MomentumTransition.MOMENTUM_REVERSAL)
                crossed = 1

            # Acceleration / Deceleration relative to zero
            if val > 0.0:
                if val > prev:
                    transitions.append(MomentumTransition.BULLISH_ACCELERATION)
                elif val < prev:
                    transitions.append(MomentumTransition.BULLISH_DECELERATION)
            elif val < 0.0:
                if val < prev:
                    transitions.append(MomentumTransition.BEARISH_ACCELERATION)
                elif val > prev:
                    transitions.append(MomentumTransition.BEARISH_DECELERATION)

        self._zero_cross_history.append(crossed)

        # Exhaustion: 3+ bars of shrinking magnitude after a strong peak
        if len(self._norm_history) >= 4:
            recent = list(self._norm_history)[-4:]
            mags = [abs(x) for x in recent]
            peak_mag = max(abs(x) for x in self._norm_history)
            if peak_mag >= 0.65 and mags[0] > mags[1] > mags[2] > mags[3]:
                transitions.append(MomentumTransition.MOMENTUM_EXHAUSTION)

        if not transitions:
            transitions.append(MomentumTransition.STEADY)
        return tuple(dict.fromkeys(transitions))

    def _classify_regime(
        self,
        snap: SqueezeSnapshot,
        sqz_state: SqueezeState,
        mom_state: MomentumState,
        transitions: tuple[MomentumTransition, ...],
        volatility_ratio: float,
    ) -> MarketRegime:
        if not snap.is_ready or sqz_state == SqueezeState.WARMUP:
            return MarketRegime.WARMUP
        if volatility_ratio >= 3.2 or (not math.isnan(snap.bb_kc_ratio) and snap.bb_kc_ratio >= 2.8):
            return MarketRegime.ABNORMAL_VOLATILITY
        if sqz_state == SqueezeState.SQUEEZE_ON:
            return MarketRegime.COMPRESSION
        if MomentumTransition.MOMENTUM_EXHAUSTION in transitions:
            return MarketRegime.MOMENTUM_EXHAUSTION
        if sqz_state == SqueezeState.SQUEEZE_OFF and mom_state in (
            MomentumState.STRONG_BULLISH,
            MomentumState.STRONG_BEARISH,
        ):
            return MarketRegime.EXPANSION_TREND
        if sqz_state == SqueezeState.SQUEEZE_OFF and (
            MomentumTransition.BULLISH_ACCELERATION in transitions
            or MomentumTransition.BEARISH_ACCELERATION in transitions
        ):
            return MarketRegime.EXPANSION_TREND
        if sum(self._zero_cross_history) >= 3 or mom_state == MomentumState.NEUTRAL:
            return MarketRegime.CHOP
        return MarketRegime.CHOP if sqz_state == SqueezeState.NO_SQUEEZE else MarketRegime.EXPANSION_TREND


class MultiTimeframeAnalyzer:
    """Tracks HTF context, Primary TF signal sensor, and LTF management sensor per symbol."""

    def __init__(
        self,
        symbol: str,
        primary_tf: int,
        higher_tf: int | None = 3600,
        lower_tf: int | None = 300,
        squeeze_config: SqueezeConfig | None = None,
    ) -> None:
        self.symbol = symbol
        self.primary_tf = primary_tf
        self.higher_tf = higher_tf
        self.lower_tf = lower_tf
        self.engines: dict[int, MarketStateEngine] = {
            primary_tf: MarketStateEngine(symbol, primary_tf, squeeze_config=squeeze_config)
        }
        if higher_tf is not None and higher_tf != primary_tf:
            self.engines[higher_tf] = MarketStateEngine(symbol, higher_tf, squeeze_config=squeeze_config)
        if lower_tf is not None and lower_tf not in self.engines:
            self.engines[lower_tf] = MarketStateEngine(symbol, lower_tf, squeeze_config=squeeze_config)

    def reset(self) -> None:
        for eng in self.engines.values():
            eng.reset()

    def update(self, candle: Candle) -> MarketStateSnapshot | None:
        eng = self.engines.get(candle.timeframe_sec)
        if eng is None:
            return None
        state = eng.update(candle)
        if candle.timeframe_sec != self.primary_tf:
            return state

        htf_state = self.engines[self.higher_tf].last_state if self.higher_tf in self.engines else None
        ltf_state = self.engines[self.lower_tf].last_state if self.lower_tf in self.engines else None
        enriched = MarketStateSnapshot(
            symbol=state.symbol,
            timeframe_sec=state.timeframe_sec,
            timestamp=state.timestamp,
            close=state.close,
            high=state.high,
            low=state.low,
            squeeze=state.squeeze,
            squeeze_state=state.squeeze_state,
            momentum_state=state.momentum_state,
            transitions=state.transitions,
            regime=state.regime,
            volatility_ratio=state.volatility_ratio,
            htf_bias=htf_state.momentum_state if htf_state else None,
            htf_squeeze_state=htf_state.squeeze_state if htf_state else None,
            ltf_momentum_state=ltf_state.momentum_state if ltf_state else None,
        )
        eng._last_state = enriched
        return enriched

    def primary_state(self) -> MarketStateSnapshot | None:
        return self.engines[self.primary_tf].last_state

    def lower_state(self) -> MarketStateSnapshot | None:
        if self.lower_tf and self.lower_tf in self.engines:
            return self.engines[self.lower_tf].last_state
        return None
