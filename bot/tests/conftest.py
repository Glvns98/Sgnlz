"""Shared test fixtures for deterministic market data, settings, and temporary SQLite stores."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bot.backtest.replay import generate_synthetic_deriv_candles
from bot.config.settings import (
    DashboardSettings,
    DerivSettings,
    EngineSettings,
    LoggingSettings,
    ManagementSettings,
    NotificationSettings,
    RiskSettings,
    SqueezeSettings,
    StorageSettings,
    SymbolProfile,
)
from bot.core.enums import (
    MarketRegime,
    MomentumState,
    MomentumTransition,
    OperatingMode,
    SqueezeState,
)
from bot.core.models import Candle, MarketStateSnapshot, SqueezeSnapshot
from bot.storage.database import SQLiteStorageEngine


def make_ohlc_df(n: int = 180, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    vols = np.concatenate(
        [
            np.full(min(n, 45), 1.2),
            np.full(max(0, min(n - 45, 35)), 0.12),
            np.full(max(0, min(n - 80, 40)), 1.4),
            np.full(max(0, n - 120), 0.9),
        ]
    )[:n]
    drifts = np.concatenate(
        [
            np.zeros(min(n, 45)),
            np.zeros(max(0, min(n - 45, 35))),
            np.full(max(0, min(n - 80, 40)), 0.45),
            np.full(max(0, n - 120), -0.35),
        ]
    )[:n]
    close = 100.0 + np.cumsum(drifts + rng.normal(0.0, vols, size=n))
    high = close + np.abs(rng.normal(0.35, 0.15, size=n)) * np.maximum(vols, 0.25)
    low = close - np.abs(rng.normal(0.35, 0.15, size=n)) * np.maximum(vols, 0.25)
    return pd.DataFrame({"high": high, "low": low, "close": close}, index=idx)


def make_market_state(
    *,
    symbol: str = "R_100",
    close: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    val: float = 1.5,
    prev_val: float = 1.0,
    range_ma: float = 1.5,
    squeeze_state: SqueezeState = SqueezeState.SQUEEZE_OFF,
    momentum_state: MomentumState = MomentumState.STRONG_BULLISH,
    transitions: tuple[MomentumTransition, ...] = (MomentumTransition.BULLISH_ACCELERATION,),
    regime: MarketRegime = MarketRegime.EXPANSION_TREND,
    sqz_fired: bool = False,
    volatility_ratio: float = 1.0,
    epoch: int = 1767225600,
) -> MarketStateSnapshot:
    snap = SqueezeSnapshot(
        val=val,
        prev_val=prev_val,
        delta_val=val - prev_val,
        accel_val=0.1,
        sqz_on=squeeze_state == SqueezeState.SQUEEZE_ON,
        sqz_off=squeeze_state == SqueezeState.SQUEEZE_OFF,
        no_sqz=squeeze_state == SqueezeState.NO_SQUEEZE,
        sqz_fired=sqz_fired,
        squeeze_bars=5 if sqz_fired else 0,
        bcolor="lime" if val > 0 else "red",
        scolor="black" if squeeze_state == SqueezeState.SQUEEZE_ON else "gray",
        basis=close,
        upper_bb=close + 2.0,
        lower_bb=close - 2.0,
        ma=close,
        range_ma=range_ma,
        upper_kc=close + 1.5,
        lower_kc=close - 1.5,
        bb_kc_ratio=1.3,
        normalized_momentum=val / max(range_ma, 1e-9),
        momentum_strength=min(1.0, abs(val / max(range_ma, 1e-9)) / 1.5),
        is_ready=True,
    )
    return MarketStateSnapshot(
        symbol=symbol,
        timeframe_sec=900,
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        close=close,
        high=high,
        low=low,
        squeeze=snap,
        squeeze_state=squeeze_state,
        momentum_state=momentum_state,
        transitions=transitions,
        regime=regime,
        volatility_ratio=volatility_ratio,
    )


@pytest.fixture
def test_settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        mode=OperatingMode.PAPER_TRADING,
        deriv=DerivSettings(app_id="1089", token=None),
        squeeze=SqueezeSettings(),
        risk=RiskSettings(),
        management=ManagementSettings(),
        storage=StorageSettings(sqlite_path=str(tmp_path / "test.db")),
        notifications=NotificationSettings(reddit_outbox_dir=str(tmp_path / "reddit")),
        dashboard=DashboardSettings(enabled=False),
        logging=LoggingSettings(
            json_log_path=str(tmp_path / "engine.jsonl"),
            text_log_path=str(tmp_path / "engine.log"),
        ),
        symbols={
            "R_100": SymbolProfile(symbol="R_100", enabled=True, multiplier=20.0),
            "R_75": SymbolProfile(symbol="R_75", enabled=True, multiplier=25.0),
        },
        config_version="testv1",
    )


@pytest.fixture
def temp_storage(tmp_path: Path):
    store = SQLiteStorageEngine(tmp_path / "unit.db")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def synthetic_candles() -> list[Candle]:
    return generate_synthetic_deriv_candles("R_100", bars=320, seed=77)
