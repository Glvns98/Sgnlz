"""Market State Engine: Squeeze states, 5-tier momentum, transitions, regimes, and MTF context."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import replace

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
        self._high_history: deque[float] = deque(maxlen=24)
        self._low_history: deque[float] = deque(maxlen=24)
        self._sqz_highs: deque[float] = deque(maxlen=24)
        self._sqz_lows: deque[float] = deque(maxlen=24)
        self._last_comp_high: float = 0.0
        self._last_comp_low: float = 0.0
        self._was_sqz_on: bool = False
        self._prev_bcolor: str | None = None
        self._accel_run_bars: int = 0
        self._last_state: MarketStateSnapshot | None = None

    @property
    def last_state(self) -> MarketStateSnapshot | None:
        return self._last_state

    def reset(self) -> None:
        self.indicator.reset()
        self._norm_history.clear()
        self._val_history.clear()
        self._zero_cross_history.clear()
        self._high_history.clear()
        self._low_history.clear()
        self._sqz_highs.clear()
        self._sqz_lows.clear()
        self._last_comp_high = 0.0
        self._last_comp_low = 0.0
        self._was_sqz_on = False
        self._prev_bcolor = None
        self._accel_run_bars = 0
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

        prev_highs = list(self._high_history)
        prev_lows = list(self._low_history)

        if snap.sqz_on:
            if not self._was_sqz_on:
                self._sqz_highs.clear()
                self._sqz_lows.clear()
            self._sqz_highs.append(candle.high)
            self._sqz_lows.append(candle.low)
            self._last_comp_high = max(self._sqz_highs)
            self._last_comp_low = min(self._sqz_lows)
        self._was_sqz_on = snap.sqz_on

        swing_high = max(prev_highs[-20:]) if prev_highs else candle.high
        swing_low = min(prev_lows[-20:]) if prev_lows else candle.low
        recent_structure_low = min(prev_lows[-3:] + [candle.low])
        recent_structure_high = max(prev_highs[-3:] + [candle.high])
        comp_high = self._last_comp_high or swing_high
        comp_low = self._last_comp_low or swing_low

        engine_stage, systematic_state = self._classify_stage_and_system(snap, squeeze_state)

        atr = (
            snap.range_ma
            if (not math.isnan(snap.range_ma) and snap.range_ma > 0)
            else max(bar_range, candle.close * 0.001, 1e-6)
        )
        safe_bar_range = max(bar_range, 1e-9)
        ma_valid = not math.isnan(snap.ma) and snap.ma > 0

        structure_bullish_confirmed = (
            candle.close >= candle.open or (candle.close - candle.low) >= 0.45 * safe_bar_range
        ) and (not ma_valid or candle.close >= snap.ma - 0.10 * atr)
        structure_bearish_confirmed = (
            candle.close <= candle.open or (candle.high - candle.close) >= 0.45 * safe_bar_range
        ) and (not ma_valid or candle.close <= snap.ma + 0.10 * atr)

        overhead_room_atr = (
            5.0 if (not prev_highs or candle.close >= swing_high) else max(0.0, (swing_high - candle.close) / atr)
        )
        underneath_room_atr = (
            5.0 if (not prev_lows or candle.close <= swing_low) else max(0.0, (candle.close - swing_low) / atr)
        )

        price_progressing_bull = (
            True
            if len(prev_highs) < 2
            else (candle.high >= max(prev_highs[-2:]) or candle.low >= min(prev_lows[-2:]))
        )
        price_progressing_bear = (
            True
            if len(prev_lows) < 2
            else (candle.low <= min(prev_lows[-2:]) or candle.high <= max(prev_highs[-2:]))
        )
        structure_broken_bull = (
            False
            if len(prev_lows) < 3
            else (candle.close < min(prev_lows[-3:]) or (ma_valid and candle.close < snap.ma - 0.15 * atr))
        )
        structure_broken_bear = (
            False
            if len(prev_highs) < 3
            else (candle.close > max(prev_highs[-3:]) or (ma_valid and candle.close > snap.ma + 0.15 * atr))
        )

        self._high_history.append(candle.high)
        self._low_history.append(candle.low)

        prev_bcolor = self._prev_bcolor
        if snap.bcolor in ("lime", "red"):
            self._accel_run_bars = (self._accel_run_bars + 1) if (snap.bcolor == prev_bcolor) else 1
        else:
            self._accel_run_bars = 0
        self._prev_bcolor = snap.bcolor

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
            open=candle.open,
            engine_stage=engine_stage,
            systematic_state=systematic_state,
            swing_high=swing_high,
            swing_low=swing_low,
            compression_high=comp_high,
            compression_low=comp_low,
            recent_structure_low=recent_structure_low,
            recent_structure_high=recent_structure_high,
            structure_bullish_confirmed=structure_bullish_confirmed,
            structure_bearish_confirmed=structure_bearish_confirmed,
            overhead_room_atr=overhead_room_atr,
            underneath_room_atr=underneath_room_atr,
            price_progressing_bull=price_progressing_bull,
            price_progressing_bear=price_progressing_bear,
            structure_broken_bull=structure_broken_bull,
            structure_broken_bear=structure_broken_bear,
            prev_bcolor=prev_bcolor,
            accel_run_bars=self._accel_run_bars,
        )
        self._last_state = state
        return state

    @staticmethod
    def _classify_stage_and_system(snap: SqueezeSnapshot, squeeze_state: SqueezeState) -> tuple[str, str]:
        if squeeze_state == SqueezeState.SQUEEZE_ON:
            return "STAGE_1_ENERGY_STORAGE", "SQUEEZE"
        if snap.sqz_fired:
            return "STAGE_2_IGNITION", "RELEASE"
        if snap.bcolor == "lime":
            return "STAGE_3_DIRECTIONAL_EXPANSION", "BULLISH_EXPANSION"
        if snap.bcolor == "red":
            return "STAGE_3_DIRECTIONAL_EXPANSION", "BEARISH_EXPANSION"
        return "STAGE_4_EXHAUSTION", "EXHAUSTION"

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
        enriched = replace(
            state,
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
