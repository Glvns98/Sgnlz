"""Tests for MarketStateEngine, MTF Analyzer, and SignalEngine."""

from __future__ import annotations

from dataclasses import replace

from bot.config.settings import SymbolProfile
from bot.core.enums import (
    MarketRegime,
    MomentumState,
    MomentumTransition,
    SignalType,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import Candle
from bot.strategy.market_state import MarketStateEngine, MultiTimeframeAnalyzer
from bot.strategy.signals import SignalEngine
from bot.tests.conftest import make_market_state


def test_market_state_squeeze_momentum_transitions_and_regimes(synthetic_candles: list[Candle]) -> None:
    eng = MarketStateEngine("R_100", 900)
    states = [eng.update(c) for c in synthetic_candles]

    sqz_states = {s.squeeze_state for s in states}
    mom_states = {s.momentum_state for s in states}
    regimes = {s.regime for s in states}

    assert SqueezeState.WARMUP in sqz_states
    assert SqueezeState.SQUEEZE_ON in sqz_states
    assert SqueezeState.SQUEEZE_OFF in sqz_states
    assert MomentumState.STRONG_BULLISH in mom_states or MomentumState.STRONG_BEARISH in mom_states
    assert MarketRegime.COMPRESSION in regimes

    # Verify abnormal volatility detection on extreme bar spike
    last = synthetic_candles[-1]
    spike = Candle(
        symbol="R_100",
        timeframe_sec=900,
        epoch=last.epoch + 900,
        open=last.close,
        high=last.close * 1.15,
        low=last.close * 0.85,
        close=last.close * 1.05,
    )
    spike_state = eng.update(spike)
    assert spike_state.regime == MarketRegime.ABNORMAL_VOLATILITY


def test_mtf_analyzer_attaches_htf_and_ltf_context(synthetic_candles: list[Candle]) -> None:
    mtf = MultiTimeframeAnalyzer("R_100", primary_tf=900, higher_tf=3600, lower_tf=300)
    for i, c in enumerate(synthetic_candles[:100]):
        if i % 4 == 0:
            htf = Candle("R_100", 3600, c.epoch, c.open, c.high, c.low, c.close)
            mtf.update(htf)
        ltf = Candle("R_100", 300, c.epoch, c.open, c.high, c.low, c.close)
        mtf.update(ltf)
        primary = mtf.update(c)
    assert primary is not None
    assert primary.htf_bias is not None
    assert primary.ltf_momentum_state is not None


def test_signal_engine_opportunity_cooldown_and_no_action() -> None:
    sig_eng = SignalEngine()
    profile = SymbolProfile(symbol="R_100", min_confidence=0.58, cooldown_bars=3)

    # Warmup / squeeze on -> NO_ACTION
    s_on = make_market_state(
        squeeze_state=SqueezeState.SQUEEZE_ON,
        momentum_state=MomentumState.NEUTRAL,
        regime=MarketRegime.COMPRESSION,
    )
    out_on = sig_eng.evaluate(s_on, profile)
    assert out_on.signal_type == SignalType.NO_ACTION

    # Release + strong bullish -> NEW_OPPORTUNITY
    s_rel = make_market_state(
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION, MomentumTransition.ZERO_CROSS_UP),
        sqz_fired=True,
        epoch=1767226500,
    )
    out_rel = sig_eng.evaluate(s_rel, profile)
    assert out_rel.signal_type == SignalType.NEW_OPPORTUNITY
    assert out_rel.direction == TradeDirection.BUY
    assert out_rel.confidence >= 0.58

    # Immediate next bar blocked by cooldown
    s_next = make_market_state(
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION,),
        epoch=1767227400,
    )
    out_cd = sig_eng.evaluate(s_next, profile)
    assert out_cd.signal_type == SignalType.NO_ACTION
    assert "cooldown_active" in out_cd.reasons


def test_signal_engine_pure_15m_ignores_htf_bias() -> None:
    sig_no_htf = SignalEngine()
    sig_opp_htf = SignalEngine()
    profile = SymbolProfile(symbol="R_100", primary_timeframe=900, higher_timeframe=None, min_confidence=0.58)

    base = make_market_state(
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION, MomentumTransition.ZERO_CROSS_UP),
        sqz_fired=True,
        epoch=1767226500,
    )
    opposed = replace(base, htf_bias=MomentumState.STRONG_BEARISH)
    res_base = sig_no_htf.evaluate(base, profile)
    res_opp = sig_opp_htf.evaluate(opposed, profile)
    assert res_base.signal_type == SignalType.NEW_OPPORTUNITY
    assert res_opp.signal_type == SignalType.NEW_OPPORTUNITY
    assert res_base.confidence == res_opp.confidence
    assert "htf_aligned" not in res_opp.reasons
    assert "htf_strongly_opposing" not in res_opp.reasons


def test_early_sqzmom_entries_fire_immediately_and_reject_late_exhausted_trends() -> None:
    profile = SymbolProfile(symbol="R_100", primary_timeframe=900, higher_timeframe=None, min_confidence=0.58)

    # 1. Early Squeeze Fire even with modest initial momentum (WEAK_BULLISH, val=0.25) -> fires immediately
    sig_fire = SignalEngine()
    s_fire = make_market_state(
        close=101.5,
        high=101.7,
        low=100.4,
        val=0.25,
        prev_val=-0.10,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION, MomentumTransition.ZERO_CROSS_UP),
        sqz_fired=True,
        compression_low=100.1,
        recent_structure_low=100.3,
        accel_run_bars=1,
        epoch=1767226500,
    )
    out_fire = sig_fire.evaluate(s_fire, profile)
    assert out_fire.signal_type == SignalType.NEW_OPPORTUNITY
    assert out_fire.direction == TradeDirection.BUY
    assert out_fire.metadata.get("entry_style") == "SQUEEZE_FIRE"
    assert "squeeze_fire_early_entry" in out_fire.reasons

    # 2. Early Zero-Line Flip without sqz_fired -> fires immediately
    sig_flip = SignalEngine()
    s_flip = make_market_state(
        close=101.0,
        high=101.2,
        low=100.5,
        val=0.20,
        prev_val=-0.15,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION, MomentumTransition.ZERO_CROSS_UP),
        sqz_fired=False,
        prev_bcolor="maroon",
        accel_run_bars=1,
        epoch=1767226500,
    )
    out_flip = sig_flip.evaluate(s_flip, profile)
    assert out_flip.signal_type == SignalType.NEW_OPPORTUNITY
    assert out_flip.direction == TradeDirection.BUY
    assert out_flip.metadata.get("entry_style") == "ZERO_LINE_FLIP"
    assert "zero_line_flip_early_entry" in out_flip.reasons

    # 3. Zero-Line Bounce resumption (dark green -> lime) -> fires CONTINUATION
    sig_bounce = SignalEngine()
    s_bounce = make_market_state(
        close=101.2,
        high=101.4,
        low=100.6,
        val=0.60,
        prev_val=0.35,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.WEAK_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION,),
        sqz_fired=False,
        prev_bcolor="green",
        accel_run_bars=1,
        epoch=1767226500,
    )
    out_bounce = sig_bounce.evaluate(s_bounce, profile)
    assert out_bounce.signal_type == SignalType.CONTINUATION
    assert out_bounce.direction == TradeDirection.BUY
    assert out_bounce.metadata.get("entry_style") == "ZERO_LINE_BOUNCE"
    assert "zero_line_bounce_resumption" in out_bounce.reasons

    # 4. Late exhausted mid-trend run (6 consecutive lime bars, no squeeze fire/flip/bounce) -> blocked
    sig_late = SignalEngine()
    s_late = make_market_state(
        close=105.0,
        high=105.2,
        low=104.2,
        val=2.50,
        prev_val=2.20,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        transitions=(MomentumTransition.BULLISH_ACCELERATION,),
        sqz_fired=False,
        prev_bcolor="lime",
        accel_run_bars=6,
        epoch=1767226500,
    )
    out_late = sig_late.evaluate(s_late, profile)
    assert out_late.signal_type == SignalType.NO_ACTION
    assert "late_mid_trend_already_running" in out_late.reasons
