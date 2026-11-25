"""Tests verifying mathematical parity with LazyBear's Squeeze.pine."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bot.indicators.squeeze import (
    SqueezeConfig,
    SqueezeMomentumIndicator,
    compute_squeeze_momentum,
    linreg,
    stdev,
    true_range,
)
from bot.tests.conftest import make_ohlc_df


def _ref_linreg_polyfit(values: np.ndarray, length: int, offset: int = 0) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    x = np.arange(length, dtype=np.float64)
    eval_x = float(length - 1 - offset)
    for i in range(length - 1, len(values)):
        win = values[i - length + 1 : i + 1]
        if np.isnan(win).any():
            continue
        slope, intercept = np.polyfit(x, win, 1)
        out[i] = intercept + slope * eval_x
    return out


def _ref_pine_bar_by_bar(
    df: pd.DataFrame,
    *,
    length: int = 20,
    mult: float = 2.0,
    length_kc: int = 20,
    mult_kc: float = 1.5,
    use_true_range: bool = True,
    lazybear_mult_parity: bool = True,
    tr_handle_na: bool = False,
) -> pd.DataFrame:
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    close = df["close"].to_numpy(dtype=np.float64)
    n = len(df)

    tr = np.full(n, np.nan, dtype=np.float64)
    for i in range(n):
        if not use_true_range:
            tr[i] = high[i] - low[i]
        elif i == 0 or np.isnan(close[i - 1]):
            tr[i] = (high[i] - low[i]) if tr_handle_na else np.nan
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    delta = np.full(n, np.nan, dtype=np.float64)
    upper_bb = np.full(n, np.nan, dtype=np.float64)
    lower_bb = np.full(n, np.nan, dtype=np.float64)
    upper_kc = np.full(n, np.nan, dtype=np.float64)
    lower_kc = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        if i >= length - 1:
            win = close[i - length + 1 : i + 1]
            if not np.isnan(win).any():
                basis = float(np.mean(win))
                bb_m = mult_kc if lazybear_mult_parity else mult
                dev = bb_m * float(np.std(win, ddof=0))
                upper_bb[i] = basis + dev
                lower_bb[i] = basis - dev
        if i >= length_kc - 1:
            c_win = close[i - length_kc + 1 : i + 1]
            r_win = tr[i - length_kc + 1 : i + 1]
            if not (np.isnan(c_win).any() or np.isnan(r_win).any()):
                ma = float(np.mean(c_win))
                rangema = float(np.mean(r_win))
                upper_kc[i] = ma + rangema * mult_kc
                lower_kc[i] = ma - rangema * mult_kc
            h_win = high[i - length_kc + 1 : i + 1]
            l_win = low[i - length_kc + 1 : i + 1]
            if not (np.isnan(h_win).any() or np.isnan(l_win).any() or np.isnan(c_win).any()):
                donchian_mid = 0.5 * (float(np.max(h_win)) + float(np.min(l_win)))
                baseline = 0.5 * (donchian_mid + float(np.mean(c_win)))
                delta[i] = close[i] - baseline

    val = _ref_linreg_polyfit(delta, length_kc, offset=0)
    sqz_on = np.zeros(n, dtype=bool)
    sqz_off = np.zeros(n, dtype=bool)
    no_sqz = np.zeros(n, dtype=bool)
    bcolor = np.empty(n, dtype=object)
    scolor = np.empty(n, dtype=object)
    bcolor[:] = None
    scolor[:] = None

    for i in range(n):
        if not (
            np.isnan(lower_bb[i])
            or np.isnan(upper_bb[i])
            or np.isnan(lower_kc[i])
            or np.isnan(upper_kc[i])
        ):
            sqz_on[i] = (lower_bb[i] > lower_kc[i]) and (upper_bb[i] < upper_kc[i])
            sqz_off[i] = (lower_bb[i] < lower_kc[i]) and (upper_bb[i] > upper_kc[i])
            no_sqz[i] = (not sqz_on[i]) and (not sqz_off[i])
            scolor[i] = "blue" if no_sqz[i] else ("black" if sqz_on[i] else "gray")

        if not np.isnan(val[i]):
            prev_nz = 0.0 if (i == 0 or np.isnan(val[i - 1])) else val[i - 1]
            if val[i] > 0:
                bcolor[i] = "lime" if val[i] > prev_nz else "green"
            else:
                bcolor[i] = "red" if val[i] < prev_nz else "maroon"

    return pd.DataFrame(
        {
            "val": val,
            "sqz_on": sqz_on,
            "sqz_off": sqz_off,
            "no_sqz": no_sqz,
            "bcolor": pd.Series(bcolor, index=df.index, dtype=object),
            "scolor": pd.Series(scolor, index=df.index, dtype=object),
            "upper_bb": upper_bb,
            "lower_bb": lower_bb,
            "upper_kc": upper_kc,
            "lower_kc": lower_kc,
        },
        index=df.index,
    )


def test_primitives_match_pine_semantics() -> None:
    arr = np.array([1.0, 2.5, 4.0, 3.5, 6.0, np.nan, 9.0, 10.0, 11.5, 13.0], dtype=np.float64)
    s = pd.Series(arr)
    np.testing.assert_allclose(linreg(s, 4, 0), _ref_linreg_polyfit(arr, 4, 0), rtol=1e-12, atol=1e-12, equal_nan=True)
    assert math.isclose(float(stdev(pd.Series([10.0, 12.0, 14.0, 16.0]), 4).iloc[3]), float(np.std([10, 12, 14, 16], ddof=0)))

    df = pd.DataFrame({"high": [12.0, 15.0], "low": [10.0, 11.0], "close": [11.0, 12.0]})
    assert math.isnan(true_range(df["high"], df["low"], df["close"], handle_na=False).iloc[0])
    assert true_range(df["high"], df["low"], df["close"], handle_na=True).iloc[0] == 2.0


def test_exact_pine_parity_default_and_custom_settings() -> None:
    df = make_ohlc_df(180, seed=11)
    got = compute_squeeze_momentum(df, include_bands=True)
    ref = _ref_pine_bar_by_bar(df)

    for col in ("val", "upper_bb", "lower_bb", "upper_kc", "lower_kc"):
        np.testing.assert_allclose(got[col], ref[col], rtol=1e-11, atol=1e-11, equal_nan=True)
    assert (got["sqz_on"] == ref["sqz_on"]).all()
    assert (got["sqz_off"] == ref["sqz_off"]).all()
    assert (got["no_sqz"] == ref["no_sqz"]).all()
    assert got["bcolor"].tolist() == ref["bcolor"].tolist()
    assert got["scolor"].tolist() == ref["scolor"].tolist()

    # Exact Pine warmup indices: 20 for KC bands, 38 for val
    assert got["scolor"].iloc[19] is None
    assert got["scolor"].iloc[20] is not None
    assert math.isnan(got["val"].iloc[37])
    assert not math.isnan(got["val"].iloc[38])

    # Non-TrueRange + standard bb_mult
    got2 = compute_squeeze_momentum(
        df,
        bb_length=15,
        bb_mult=2.2,
        kc_length=14,
        kc_mult=1.3,
        use_true_range=False,
        lazybear_mult_parity=False,
        include_bands=True,
    )
    ref2 = _ref_pine_bar_by_bar(
        df,
        length=15,
        mult=2.2,
        length_kc=14,
        mult_kc=1.3,
        use_true_range=False,
        lazybear_mult_parity=False,
    )
    np.testing.assert_allclose(got2["val"], ref2["val"], rtol=1e-11, atol=1e-11, equal_nan=True)
    assert got2["bcolor"].tolist() == ref2["bcolor"].tolist()


def test_streaming_matches_batch_including_interior_nans() -> None:
    df = make_ohlc_df(150, seed=123)
    df.iloc[65, df.columns.get_loc("close")] = np.nan

    for tr_na in (False, True):
        cfg = SqueezeConfig(bb_length=12, kc_length=10, tr_handle_na=tr_na)
        batch = compute_squeeze_momentum(df, config=cfg, include_bands=True)
        ind = SqueezeMomentumIndicator(config=cfg)
        snaps = [ind.update(r.high, r.low, r.close) for r in df.itertuples(index=False)]

        np.testing.assert_allclose([s.val for s in snaps], batch["val"], rtol=1e-12, atol=1e-12, equal_nan=True)
        assert [s.bcolor for s in snaps] == batch["bcolor"].tolist()
        assert [s.scolor for s in snaps] == batch["scolor"].tolist()
        assert [s.sqz_fired for s in snaps] == batch["sqz_fired"].tolist()
