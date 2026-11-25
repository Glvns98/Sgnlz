"""Faithful Python implementation of LazyBear's Squeeze.pine indicator.

Preserves exact Pine Script parity:
  - ``stdev`` uses population standard deviation (``ddof=0``).
  - ``tr`` uses Pine's ``tr(false)`` semantics (``NaN`` on bar 0 unless ``tr_handle_na=True``).
  - ``dev = multKC * stdev(source, length)`` preserves LazyBear's ``multKC`` quirk by default.
  - ``linreg(series, length, 0)`` uses exact closed-form OLS weights.
  - ``nz(val[1])`` defaults to ``0.0`` on the first valid momentum bar.
"""

from __future__ import annotations

import math
import warnings
from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.core.models import BarColor, SqueezeColor, SqueezeSnapshot


@dataclass(frozen=True)
class SqueezeConfig:
    """Configuration parameters matching ``Squeeze.pine`` inputs."""

    bb_length: int = 20
    bb_mult: float = 2.0
    kc_length: int = 20
    kc_mult: float = 1.5
    use_true_range: bool = True
    lazybear_mult_parity: bool = True
    tr_handle_na: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.bb_length, bool) or not isinstance(self.bb_length, int):
            raise TypeError("bb_length must be an int")
        if isinstance(self.kc_length, bool) or not isinstance(self.kc_length, int):
            raise TypeError("kc_length must be an int")
        if self.bb_length < 1:
            raise ValueError(f"bb_length must be >= 1, got {self.bb_length}")
        if self.kc_length < 2:
            raise ValueError(f"kc_length must be >= 2 for linreg, got {self.kc_length}")
        if not math.isfinite(self.bb_mult) or self.bb_mult <= 0:
            raise ValueError(f"bb_mult must be finite and > 0, got {self.bb_mult}")
        if not math.isfinite(self.kc_mult) or self.kc_mult <= 0:
            raise ValueError(f"kc_mult must be finite and > 0, got {self.kc_mult}")
        if self.lazybear_mult_parity and self.bb_mult != 2.0:
            warnings.warn(
                "bb_mult is ignored when lazybear_mult_parity=True (matches "
                "LazyBear's Squeeze.pine where dev uses kc_mult).",
                UserWarning,
                stacklevel=2,
            )


def _linreg_weights(length: int, offset: int = 0) -> np.ndarray:
    """Return exact OLS weights matching Pine Script ``ta.linreg(src, length, offset)``."""
    if length < 2:
        raise ValueError(f"linreg length must be >= 2, got {length}")
    x = np.arange(length, dtype=np.float64)
    x_mean = 0.5 * (length - 1)
    denom = length * (length * length - 1) / 12.0
    eval_delta = x_mean - float(offset)
    return (1.0 / length) + (eval_delta / denom) * (x - x_mean)


def sma(series: pd.Series, length: int) -> pd.Series:
    if length < 1:
        raise ValueError(f"sma length must be >= 1, got {length}")
    return series.rolling(window=length, min_periods=length).mean()


def stdev(series: pd.Series, length: int, ddof: int = 0) -> pd.Series:
    if length < 1:
        raise ValueError(f"stdev length must be >= 1, got {length}")
    return series.rolling(window=length, min_periods=length).std(ddof=ddof)


def true_range(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    handle_na: bool = False,
) -> pd.Series:
    """True Range matching Pine Script ``tr`` (``handle_na=False``) or ``ta.tr(true)``."""
    prev_close = close.shift(1)
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr_arr = np.maximum(
        hl.to_numpy(dtype=np.float64, na_value=np.nan),
        np.maximum(
            hc.to_numpy(dtype=np.float64, na_value=np.nan),
            lc.to_numpy(dtype=np.float64, na_value=np.nan),
        ),
    )
    if handle_na and len(tr_arr) > 0:
        hl_arr = hl.to_numpy(dtype=np.float64, na_value=np.nan)
        prev_na = prev_close.isna().to_numpy()
        tr_arr = np.where(prev_na, hl_arr, tr_arr)
    return pd.Series(tr_arr, index=high.index, dtype=np.float64)


def linreg(series: pd.Series, length: int, offset: int = 0) -> pd.Series:
    """Rolling OLS linear regression matching Pine Script ``ta.linreg(src, length, offset)``."""
    weights = _linreg_weights(length, offset=offset)
    arr = series.to_numpy(dtype=np.float64, na_value=np.nan)
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if len(arr) >= length:
        windows = np.lib.stride_tricks.sliding_window_view(arr, window_shape=length)
        out[length - 1 :] = windows @ weights
    return pd.Series(out, index=series.index, dtype=np.float64)


def _extract_ohlc(df: pd.DataFrame, source_col: str | pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    col_lookup = {str(col).lower(): col for col in df.columns}
    missing = [name for name in ("high", "low", "close") if name not in col_lookup]
    if missing:
        raise ValueError(f"DataFrame is missing required OHLC columns: {missing}")

    def _to_f64(col_key: object) -> pd.Series:
        num = pd.to_numeric(df[col_key], errors="raise")
        return pd.Series(num.to_numpy(dtype=np.float64, na_value=np.nan), index=df.index, dtype=np.float64)

    high = _to_f64(col_lookup["high"])
    low = _to_f64(col_lookup["low"])
    close = _to_f64(col_lookup["close"])

    if isinstance(source_col, pd.Series):
        if not source_col.index.equals(df.index):
            raise ValueError("Custom source Series index must match df.index")
        src_num = pd.to_numeric(source_col, errors="raise")
        source = pd.Series(src_num.to_numpy(dtype=np.float64, na_value=np.nan), index=df.index, dtype=np.float64)
    else:
        key = str(source_col).lower()
        if key not in col_lookup:
            raise ValueError(f"DataFrame is missing source column '{source_col}'")
        source = _to_f64(col_lookup[key])
    return high, low, close, source


def compute_squeeze_momentum(
    df: pd.DataFrame,
    config: SqueezeConfig | None = None,
    *,
    bb_length: int | None = None,
    bb_mult: float | None = None,
    kc_length: int | None = None,
    kc_mult: float | None = None,
    use_true_range: bool | None = None,
    lazybear_mult_parity: bool | None = None,
    tr_handle_na: bool | None = None,
    source: str | pd.Series = "close",
    include_bands: bool = False,
) -> pd.DataFrame:
    """Vectorized Squeeze.pine calculation over an OHLC DataFrame."""
    base = config if config is not None else SqueezeConfig()
    cfg = SqueezeConfig(
        bb_length=base.bb_length if bb_length is None else bb_length,
        bb_mult=base.bb_mult if bb_mult is None else bb_mult,
        kc_length=base.kc_length if kc_length is None else kc_length,
        kc_mult=base.kc_mult if kc_mult is None else kc_mult,
        use_true_range=base.use_true_range if use_true_range is None else use_true_range,
        lazybear_mult_parity=base.lazybear_mult_parity if lazybear_mult_parity is None else lazybear_mult_parity,
        tr_handle_na=base.tr_handle_na if tr_handle_na is None else tr_handle_na,
    )

    high, low, close, src = _extract_ohlc(df, source)
    if len(df) == 0:
        cols: dict[str, pd.Series] = {
            "val": pd.Series(index=df.index, dtype=np.float64),
            "sqz_on": pd.Series(index=df.index, dtype=bool),
            "sqz_off": pd.Series(index=df.index, dtype=bool),
            "no_sqz": pd.Series(index=df.index, dtype=bool),
            "sqz_fired": pd.Series(index=df.index, dtype=bool),
            "bcolor": pd.Series(index=df.index, dtype=object),
            "scolor": pd.Series(index=df.index, dtype=object),
        }
        if include_bands:
            for c in ("basis", "upper_bb", "lower_bb", "ma", "range_ma", "upper_kc", "lower_kc", "baseline"):
                cols[c] = pd.Series(index=df.index, dtype=np.float64)
        return pd.DataFrame(cols, index=df.index)

    basis = sma(src, cfg.bb_length)
    bb_mult_eff = cfg.kc_mult if cfg.lazybear_mult_parity else cfg.bb_mult
    dev = bb_mult_eff * stdev(src, cfg.bb_length, ddof=0)
    upper_bb = basis + dev
    lower_bb = basis - dev

    ma = sma(src, cfg.kc_length)
    bar_range = true_range(high, low, close, handle_na=cfg.tr_handle_na) if cfg.use_true_range else (high - low)
    range_ma = sma(bar_range, cfg.kc_length)
    upper_kc = ma + range_ma * cfg.kc_mult
    lower_kc = ma - range_ma * cfg.kc_mult

    bands_valid = upper_bb.notna() & lower_bb.notna() & upper_kc.notna() & lower_kc.notna()
    sqz_on = ((lower_bb > lower_kc) & (upper_bb < upper_kc)) & bands_valid
    sqz_off = ((lower_bb < lower_kc) & (upper_bb > upper_kc)) & bands_valid
    no_sqz = (~sqz_on) & (~sqz_off) & bands_valid
    sqz_fired = sqz_on.shift(1, fill_value=False) & sqz_off

    highest_high = high.rolling(window=cfg.kc_length, min_periods=cfg.kc_length).max()
    lowest_low = low.rolling(window=cfg.kc_length, min_periods=cfg.kc_length).min()
    donchian_mid = 0.5 * (highest_high + lowest_low)
    sma_close = sma(close, cfg.kc_length)
    baseline = 0.5 * (donchian_mid + sma_close)
    val = linreg(src - baseline, cfg.kc_length, offset=0)

    val_arr = val.to_numpy(dtype=np.float64)
    prev_nz_arr = val.shift(1).fillna(0.0).to_numpy(dtype=np.float64)
    valid_val = ~np.isnan(val_arr)
    pos = valid_val & (val_arr > 0.0)
    non_pos = valid_val & (val_arr <= 0.0)

    bcolor_arr = np.empty(len(df), dtype=object)
    bcolor_arr[:] = None
    bcolor_arr[pos & (val_arr > prev_nz_arr)] = "lime"
    bcolor_arr[pos & (val_arr <= prev_nz_arr)] = "green"
    bcolor_arr[non_pos & (val_arr < prev_nz_arr)] = "red"
    bcolor_arr[non_pos & (val_arr >= prev_nz_arr)] = "maroon"

    scolor_arr = np.empty(len(df), dtype=object)
    scolor_arr[:] = None
    scolor_arr[no_sqz.to_numpy()] = "blue"
    scolor_arr[sqz_on.to_numpy()] = "black"
    scolor_arr[sqz_off.to_numpy()] = "gray"

    data: dict[str, object] = {
        "val": val,
        "sqz_on": sqz_on.astype(bool),
        "sqz_off": sqz_off.astype(bool),
        "no_sqz": no_sqz.astype(bool),
        "sqz_fired": sqz_fired.astype(bool),
        "bcolor": pd.Series(bcolor_arr, index=df.index, dtype=object),
        "scolor": pd.Series(scolor_arr, index=df.index, dtype=object),
    }
    if include_bands:
        data.update(
            {
                "basis": basis,
                "upper_bb": upper_bb,
                "lower_bb": lower_bb,
                "ma": ma,
                "range_ma": range_ma,
                "upper_kc": upper_kc,
                "lower_kc": lower_kc,
                "baseline": baseline,
            }
        )
    return pd.DataFrame(data, index=df.index)


class SqueezeMomentumIndicator:
    """Streaming bar-by-bar Squeeze.pine sensor with Pine parity."""

    def __init__(self, config: SqueezeConfig | None = None, **kwargs: object) -> None:
        if config is not None and kwargs:
            raise ValueError("Provide either config or keyword overrides, not both")
        self.config: SqueezeConfig = (
            config if config is not None else SqueezeConfig(**kwargs)  # type: ignore[arg-type]
        )
        self._linreg_weights = _linreg_weights(self.config.kc_length, offset=0)
        self._highs: deque[float] = deque(maxlen=self.config.kc_length)
        self._lows: deque[float] = deque(maxlen=self.config.kc_length)
        self._closes: deque[float] = deque(maxlen=self.config.kc_length)
        self._bb_sources: deque[float] = deque(maxlen=self.config.bb_length)
        self._kc_sources: deque[float] = deque(maxlen=self.config.kc_length)
        self._ranges: deque[float] = deque(maxlen=self.config.kc_length)
        self._deltas: deque[float] = deque(maxlen=self.config.kc_length)
        self._prev_close: float | None = None
        self._prev_val: float | None = None
        self._prev_delta_val: float | None = None
        self._prev_sqz_on: bool = False
        self._squeeze_bars: int = 0
        self._last: SqueezeSnapshot | None = None

    @property
    def last(self) -> SqueezeSnapshot | None:
        return self._last

    def reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        self._closes.clear()
        self._bb_sources.clear()
        self._kc_sources.clear()
        self._ranges.clear()
        self._deltas.clear()
        self._prev_close = None
        self._prev_val = None
        self._prev_delta_val = None
        self._prev_sqz_on = False
        self._squeeze_bars = 0
        self._last = None

    def update(self, high: float, low: float, close: float, source: float | None = None) -> SqueezeSnapshot:
        """Ingest one completed OHLC bar and return a ``SqueezeSnapshot``."""
        cfg = self.config
        h = float(high)
        lo = float(low)
        c = float(close)
        s = c if source is None else float(source)

        if cfg.use_true_range:
            if math.isnan(h) or math.isnan(lo):
                bar_range = math.nan
            elif self._prev_close is None or math.isnan(self._prev_close):
                bar_range = (h - lo) if cfg.tr_handle_na else math.nan
            else:
                bar_range = max(h - lo, abs(h - self._prev_close), abs(lo - self._prev_close))
        else:
            bar_range = h - lo

        self._prev_close = c
        self._highs.append(h)
        self._lows.append(lo)
        self._closes.append(c)
        self._bb_sources.append(s)
        self._kc_sources.append(s)
        self._ranges.append(bar_range)

        basis = upper_bb = lower_bb = math.nan
        if len(self._bb_sources) == cfg.bb_length:
            bb_arr = np.asarray(self._bb_sources, dtype=np.float64)
            if not np.isnan(bb_arr).any():
                basis = float(bb_arr.mean())
                bb_mult_eff = cfg.kc_mult if cfg.lazybear_mult_parity else cfg.bb_mult
                dev = bb_mult_eff * float(bb_arr.std(ddof=0))
                upper_bb = basis + dev
                lower_bb = basis - dev

        ma = range_ma = upper_kc = lower_kc = math.nan
        if len(self._kc_sources) == cfg.kc_length:
            kc_arr = np.asarray(self._kc_sources, dtype=np.float64)
            if not np.isnan(kc_arr).any():
                ma = float(kc_arr.mean())
        if len(self._ranges) == cfg.kc_length:
            rng_arr = np.asarray(self._ranges, dtype=np.float64)
            if not np.isnan(rng_arr).any():
                range_ma = float(rng_arr.mean())
        if not (math.isnan(ma) or math.isnan(range_ma)):
            upper_kc = ma + range_ma * cfg.kc_mult
            lower_kc = ma - range_ma * cfg.kc_mult

        bands_valid = not (
            math.isnan(upper_bb)
            or math.isnan(lower_bb)
            or math.isnan(upper_kc)
            or math.isnan(lower_kc)
        )
        if bands_valid:
            sqz_on = (lower_bb > lower_kc) and (upper_bb < upper_kc)
            sqz_off = (lower_bb < lower_kc) and (upper_bb > upper_kc)
            no_sqz = (not sqz_on) and (not sqz_off)
            scolor: SqueezeColor | None = "blue" if no_sqz else ("black" if sqz_on else "gray")
            bb_kc_ratio = (upper_bb - lower_bb) / max(upper_kc - lower_kc, 1e-12)
        else:
            sqz_on = sqz_off = no_sqz = False
            scolor = None
            bb_kc_ratio = math.nan

        if sqz_on:
            self._squeeze_bars += 1
        elif bands_valid and not sqz_off:
            self._squeeze_bars = 0

        sqz_fired = bool(self._prev_sqz_on and sqz_off)
        squeeze_bars_out = self._squeeze_bars
        if sqz_off:
            self._squeeze_bars = 0

        delta = math.nan
        if len(self._highs) == cfg.kc_length and not math.isnan(s):
            h_arr = np.asarray(self._highs, dtype=np.float64)
            l_arr = np.asarray(self._lows, dtype=np.float64)
            c_arr = np.asarray(self._closes, dtype=np.float64)
            if not (np.isnan(h_arr).any() or np.isnan(l_arr).any() or np.isnan(c_arr).any()):
                donchian_mid = 0.5 * (float(h_arr.max()) + float(l_arr.min()))
                baseline = 0.5 * (donchian_mid + float(c_arr.mean()))
                delta = s - baseline

        self._deltas.append(delta)

        val = math.nan
        if len(self._deltas) == cfg.kc_length:
            d_arr = np.asarray(self._deltas, dtype=np.float64)
            if not np.isnan(d_arr).any():
                val = float(self._linreg_weights @ d_arr)

        prev_val = self._prev_val
        if math.isnan(val):
            bcolor: BarColor | None = None
            delta_val = 0.0
            accel_val = 0.0
            normalized_momentum = 0.0
            momentum_strength = 0.0
            is_ready = False
        else:
            is_ready = True
            prev_nz = 0.0 if (prev_val is None or math.isnan(prev_val)) else prev_val
            if val > 0.0:
                bcolor = "lime" if val > prev_nz else "green"
            else:
                bcolor = "red" if val < prev_nz else "maroon"
            delta_val = 0.0 if (prev_val is None or math.isnan(prev_val)) else (val - prev_val)
            prev_d = 0.0 if self._prev_delta_val is None else self._prev_delta_val
            accel_val = delta_val - prev_d
            scale = range_ma if (not math.isnan(range_ma) and range_ma > 1e-12) else max(abs(val), 1e-12)
            normalized_momentum = val / scale
            momentum_strength = min(1.0, abs(math.tanh(normalized_momentum / 1.5)))

        self._prev_val = val
        if not math.isnan(val):
            self._prev_delta_val = delta_val
        self._prev_sqz_on = sqz_on

        snap = SqueezeSnapshot(
            val=val,
            prev_val=prev_val,
            delta_val=delta_val,
            accel_val=accel_val,
            sqz_on=sqz_on,
            sqz_off=sqz_off,
            no_sqz=no_sqz,
            sqz_fired=sqz_fired,
            squeeze_bars=squeeze_bars_out,
            bcolor=bcolor,
            scolor=scolor,
            basis=basis,
            upper_bb=upper_bb,
            lower_bb=lower_bb,
            ma=ma,
            range_ma=range_ma,
            upper_kc=upper_kc,
            lower_kc=lower_kc,
            bb_kc_ratio=bb_kc_ratio,
            normalized_momentum=normalized_momentum,
            momentum_strength=momentum_strength,
            is_ready=is_ready,
        )
        self._last = snap
        return snap

    def compute(
        self,
        df: pd.DataFrame,
        *,
        source: str | pd.Series = "close",
        include_bands: bool = False,
    ) -> pd.DataFrame:
        return compute_squeeze_momentum(df, config=self.config, source=source, include_bands=include_bands)
