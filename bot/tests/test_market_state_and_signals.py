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
