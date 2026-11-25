"""Tests proving ZERO look-ahead bias, 5-policy backtest comparison, and walk-forward validation."""

from __future__ import annotations

from bot.backtest.engine import BacktestRunner, WalkForwardValidator
from bot.backtest.replay import HistoricalReplayEngine, generate_synthetic_deriv_candles
from bot.core.enums import ManagementPolicy
from bot.core.models import Candle


def test_strict_zero_look_ahead_bias_invariance(test_settings) -> None:
    """Mutating future bars > T must produce IDENTICAL signals & closed trades up to bar T."""
    candles_orig = generate_synthetic_deriv_candles("R_100", bars=360, seed=55)
    cutoff_idx = 220
    cutoff_epoch = candles_orig[cutoff_idx].epoch

    candles_mutated = list(candles_orig)
    for i in range(cutoff_idx + 1, len(candles_mutated)):
        c = candles_mutated[i]
        candles_mutated[i] = Candle(
            symbol=c.symbol,
            timeframe_sec=c.timeframe_sec,
            epoch=c.epoch,
            open=c.open * 3.0,
            high=c.high * 3.5,
            low=c.low * 0.4,
            close=c.close * 2.5,
        )

    engine = HistoricalReplayEngine(test_settings)
    res_orig = engine.replay({"R_100": candles_orig}, policy=ManagementPolicy.DYNAMIC_MANAGEMENT, close_open_at_end=False)
    res_mut = engine.replay({"R_100": candles_mutated}, policy=ManagementPolicy.DYNAMIC_MANAGEMENT, close_open_at_end=False)

    sig_orig_pre = [s.to_dict() for s in res_orig.signals if int(s.timestamp.timestamp()) <= cutoff_epoch]
    sig_mut_pre = [s.to_dict() for s in res_mut.signals if int(s.timestamp.timestamp()) <= cutoff_epoch]
    assert sig_orig_pre == sig_mut_pre

    tr_orig_pre = [t.to_dict() for t in res_orig.trades if int(t.closed_at.timestamp()) <= cutoff_epoch]
    tr_mut_pre = [t.to_dict() for t in res_mut.trades if int(t.closed_at.timestamp()) <= cutoff_epoch]
    assert tr_orig_pre == tr_mut_pre


def test_five_policy_backtest_and_walkforward(test_settings) -> None:
    candles = {
        "R_100": generate_synthetic_deriv_candles("R_100", bars=650, seed=42),
        "R_75": generate_synthetic_deriv_candles("R_75", bars=650, seed=99),
    }
    runner = BacktestRunner(test_settings)
    report = runner.run_policy_comparison(candles)
    metrics = report["comparison"]["metrics"]
    assert set(metrics.keys()) == {p.value for p in BacktestRunner.ALL_POLICIES}
    assert "verdicts" in report["comparison"]

    wf = WalkForwardValidator(test_settings).run(candles, folds=3)
    assert len(wf["folds"]) >= 1
    assert "summary" in wf
