"""Core immutable and stateful domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from bot.core.enums import (
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    MarketRegime,
    MomentumState,
    MomentumTransition,
    RiskLevel,
    SignalType,
    SqueezeState,
    TradeDirection,
    TradeStage,
)

BarColor = Literal["lime", "green", "red", "maroon"]
SqueezeColor = Literal["blue", "black", "gray"]


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Candle:
    """OHLC market candle."""

    symbol: str
    timeframe_sec: int
    epoch: int
    open: float
    high: float
    low: float
    close: float
    is_closed: bool = True

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.epoch, tz=UTC)


@dataclass(frozen=True)
class SqueezeSnapshot:
    """Exact Squeeze.pine output plus scale-free downstream metrics."""

    val: float
    prev_val: float | None
    delta_val: float
    accel_val: float
    sqz_on: bool
    sqz_off: bool
    no_sqz: bool
    sqz_fired: bool
    squeeze_bars: int
    bcolor: BarColor | None
    scolor: SqueezeColor | None
    basis: float
    upper_bb: float
    lower_bb: float
    ma: float
    range_ma: float
    upper_kc: float
    lower_kc: float
    bb_kc_ratio: float
    normalized_momentum: float
    momentum_strength: float
    is_ready: bool

    @property
    def is_bullish(self) -> bool:
        return self.is_ready and self.val > 0.0

    @property
    def is_bearish(self) -> bool:
        return self.is_ready and self.val < 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "val": self.val,
            "prev_val": self.prev_val,
            "delta_val": self.delta_val,
            "accel_val": self.accel_val,
            "sqz_on": self.sqz_on,
            "sqz_off": self.sqz_off,
            "no_sqz": self.no_sqz,
            "sqz_fired": self.sqz_fired,
            "squeeze_bars": self.squeeze_bars,
            "bcolor": self.bcolor,
            "scolor": self.scolor,
            "basis": self.basis,
            "upper_bb": self.upper_bb,
            "lower_bb": self.lower_bb,
            "ma": self.ma,
            "range_ma": self.range_ma,
            "upper_kc": self.upper_kc,
            "lower_kc": self.lower_kc,
            "bb_kc_ratio": self.bb_kc_ratio,
            "normalized_momentum": self.normalized_momentum,
            "momentum_strength": self.momentum_strength,
            "is_ready": self.is_ready,
        }


@dataclass(frozen=True)
class MarketStateSnapshot:
    """Classified market state derived from Squeeze.pine & MTF context."""

    symbol: str
    timeframe_sec: int
    timestamp: datetime
    close: float
    high: float
    low: float
    squeeze: SqueezeSnapshot
    squeeze_state: SqueezeState
    momentum_state: MomentumState
    transitions: tuple[MomentumTransition, ...]
    regime: MarketRegime
    volatility_ratio: float
    htf_bias: MomentumState | None = None
    htf_squeeze_state: SqueezeState | None = None
    ltf_momentum_state: MomentumState | None = None
    open: float = 0.0
    engine_stage: str = "STAGE_3_DIRECTIONAL_EXPANSION"
    systematic_state: str = "BULLISH_EXPANSION"
    swing_high: float = 0.0
    swing_low: float = 0.0
    compression_high: float = 0.0
    compression_low: float = 0.0
    recent_structure_low: float = 0.0
    recent_structure_high: float = 0.0
    structure_bullish_confirmed: bool = True
    structure_bearish_confirmed: bool = True
    overhead_room_atr: float = 5.0
    underneath_room_atr: float = 5.0
    price_progressing_bull: bool = True
    price_progressing_bear: bool = True
    structure_broken_bull: bool = False
    structure_broken_bear: bool = False
    prev_bcolor: str | None = None
    accel_run_bars: int = 0

    def has_transition(self, transition: MomentumTransition) -> bool:
        return transition in self.transitions

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe_sec": self.timeframe_sec,
            "timestamp": self.timestamp.isoformat(),
            "close": self.close,
            "high": self.high,
            "low": self.low,
            "squeeze_state": self.squeeze_state.value,
            "momentum_state": self.momentum_state.value,
            "transitions": [t.value for t in self.transitions],
            "regime": self.regime.value,
            "volatility_ratio": round(self.volatility_ratio, 4),
            "htf_bias": self.htf_bias.value if self.htf_bias else None,
            "htf_squeeze_state": self.htf_squeeze_state.value if self.htf_squeeze_state else None,
            "ltf_momentum_state": self.ltf_momentum_state.value if self.ltf_momentum_state else None,
            "val": self.squeeze.val,
            "normalized_momentum": self.squeeze.normalized_momentum,
            "momentum_strength": self.squeeze.momentum_strength,
            "range_ma": self.squeeze.range_ma,
            "engine_stage": self.engine_stage,
            "systematic_state": self.systematic_state,
            "overhead_room_atr": round(self.overhead_room_atr, 3),
            "underneath_room_atr": round(self.underneath_room_atr, 3),
            "prev_bcolor": self.prev_bcolor,
            "accel_run_bars": self.accel_run_bars,
        }


@dataclass(frozen=True)
class TradeSignal:
    """Structured signal event produced by the SignalEngine."""

    signal_id: str
    symbol: str
    direction: TradeDirection | None
    signal_type: SignalType
    squeeze_state: SqueezeState
    momentum_state: MomentumState
    momentum_strength: float
    confidence: float
    regime: MarketRegime
    timestamp: datetime
    timeframe_sec: int
    entry_price: float
    suggested_stop: float
    initial_risk_distance: float
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction.value if self.direction else None,
            "signal_type": self.signal_type.value,
            "squeeze_state": self.squeeze_state.value,
            "momentum_state": self.momentum_state.value,
            "momentum_strength": round(self.momentum_strength, 4),
            "confidence": round(self.confidence, 4),
            "regime": self.regime.value,
            "timestamp": self.timestamp.isoformat(),
            "timeframe_sec": self.timeframe_sec,
            "entry_price": self.entry_price,
            "suggested_stop": self.suggested_stop,
            "initial_risk_distance": self.initial_risk_distance,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ManagementDecision:
    """Auditable decision emitted by TradeManager.evaluate()."""

    action: ManagementAction
    reason: str
    confidence: float
    risk_state: str
    target_stage: TradeStage
    new_stop_price: float | None = None
    reduce_fraction: float = 0.0
    exit_reason: ExitReason | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "confidence": round(self.confidence, 4),
            "risk_state": self.risk_state,
            "target_stage": self.target_stage.value,
            "new_stop_price": self.new_stop_price,
            "reduce_fraction": self.reduce_fraction,
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "inputs": dict(self.inputs),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ManagementEvent:
    """Recorded lifecycle management event attached to a Position."""

    event_id: str
    trade_id: str
    timestamp: datetime
    stage_before: TradeStage
    stage_after: TradeStage
    decision: ManagementDecision
    current_price: float
    unrealized_pnl: float
    unrealized_r: float
    mfe_r: float
    mae_r: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "trade_id": self.trade_id,
            "timestamp": self.timestamp.isoformat(),
            "stage_before": self.stage_before.value,
            "stage_after": self.stage_after.value,
            "decision": self.decision.to_audit_dict(),
            "current_price": self.current_price,
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "unrealized_r": round(self.unrealized_r, 4),
            "mfe_r": round(self.mfe_r, 4),
            "mae_r": round(self.mae_r, 4),
        }


@dataclass
class Position:
    """Active managed exposure with full MFE/MAE & lifecycle telemetry."""

    trade_id: str
    idempotency_key: str
    symbol: str
    direction: TradeDirection
    stage: TradeStage
    entry_price: float
    current_price: float
    initial_stop: float
    current_stop: float
    take_profit: float | None
    stake: float
    remaining_fraction: float
    multiplier: float
    initial_risk_amount: float
    initial_risk_distance: float
    opened_at: datetime
    last_updated_at: datetime
    bars_held: int = 0
    mfe_price: float = 0.0
    mae_price: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    max_unrealized_pnl: float = 0.0
    min_unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    squeeze_state_at_entry: str = SqueezeState.SQUEEZE_OFF.value
    momentum_state_at_entry: str = MomentumState.NEUTRAL.value
    momentum_at_entry: float = 0.0
    contract_id: str | None = None
    policy: ManagementPolicy = ManagementPolicy.DYNAMIC_MANAGEMENT
    decay_bars: int = 0
    management_events: list[ManagementEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mfe_price == 0.0:
            self.mfe_price = self.entry_price
        if self.mae_price == 0.0:
            self.mae_price = self.entry_price

    def price_delta_for(self, price: float) -> float:
        if self.direction == TradeDirection.BUY:
            return price - self.entry_price
        return self.entry_price - price

    def r_for_price(self, price: float) -> float:
        if self.initial_risk_distance <= 0.0:
            return 0.0
        return self.price_delta_for(price) / self.initial_risk_distance

    def unrealized_r(self) -> float:
        return self.r_for_price(self.current_price)

    def unrealized_pnl(self) -> float:
        return self.unrealized_r() * self.initial_risk_amount * self.remaining_fraction

    def total_pnl_at(self, price: float) -> float:
        return self.realized_pnl + (
            self.r_for_price(price) * self.initial_risk_amount * self.remaining_fraction
        )

    def profit_giveback_r(self) -> float:
        return max(0.0, self.mfe_r - self.unrealized_r())

    def profit_giveback_ratio(self) -> float:
        if self.mfe_r <= 1e-9:
            return 0.0
        return self.profit_giveback_r() / self.mfe_r

    def update_mark(
        self,
        price: float,
        high: float,
        low: float,
        timestamp: datetime,
        *,
        is_new_bar: bool = True,
    ) -> None:
        """Update current mark, MFE, MAE, and bars held without look-ahead."""
        self.current_price = price
        self.last_updated_at = timestamp
        if is_new_bar:
            self.bars_held += 1

        favorable_price = high if self.direction == TradeDirection.BUY else low
        adverse_price = low if self.direction == TradeDirection.BUY else high

        fav_r = self.r_for_price(favorable_price)
        adv_r = self.r_for_price(adverse_price)

        if fav_r > self.mfe_r:
            self.mfe_r = fav_r
            self.mfe_price = favorable_price
        if adv_r < self.mae_r:
            self.mae_r = adv_r
            self.mae_price = adverse_price

        cur_pnl = self.unrealized_pnl()
        self.max_unrealized_pnl = max(self.max_unrealized_pnl, cur_pnl, self.mfe_r * self.initial_risk_amount)
        self.min_unrealized_pnl = min(self.min_unrealized_pnl, cur_pnl, self.mae_r * self.initial_risk_amount)


@dataclass(frozen=True)
class ClosedTrade:
    """Complete post-trade journal record (Section 17)."""

    trade_id: str
    timestamp: datetime
    closed_at: datetime
    symbol: str
    direction: TradeDirection
    entry: float
    initial_risk: float
    initial_stop: float
    final_stop: float
    exit: float
    exit_reason: ExitReason
    duration_sec: float
    bars_held: int
    pnl: float
    r_multiple: float
    mfe: float
    mae: float
    highest_profit: float
    lowest_drawdown: float
    squeeze_state_at_entry: str
    squeeze_state_at_exit: str
    momentum_at_entry: float
    momentum_at_exit: float
    policy: ManagementPolicy
    management_events: tuple[dict[str, Any], ...] = ()
    management_actions: tuple[str, ...] = ()
    entry_quality: str = "UNKNOWN"
    management_quality: str = "UNKNOWN"
    attribution_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry": self.entry,
            "initial_risk": round(self.initial_risk, 4),
            "initial_stop": self.initial_stop,
            "final_stop": self.final_stop,
            "exit": self.exit,
            "exit_reason": self.exit_reason.value,
            "duration_sec": round(self.duration_sec, 2),
            "bars_held": self.bars_held,
            "pnl": round(self.pnl, 4),
            "r_multiple": round(self.r_multiple, 4),
            "mfe": round(self.mfe, 4),
            "mae": round(self.mae, 4),
            "highest_profit": round(self.highest_profit, 4),
            "lowest_drawdown": round(self.lowest_drawdown, 4),
            "squeeze_state_at_entry": self.squeeze_state_at_entry,
            "squeeze_state_at_exit": self.squeeze_state_at_exit,
            "momentum_at_entry": round(self.momentum_at_entry, 6),
            "momentum_at_exit": round(self.momentum_at_exit, 6),
            "policy": self.policy.value,
            "management_actions": list(self.management_actions),
            "management_events": list(self.management_events),
            "entry_quality": self.entry_quality,
            "management_quality": self.management_quality,
            "attribution_summary": self.attribution_summary,
        }


@dataclass(frozen=True)
class RiskState:
    """Real-time portfolio risk posture."""

    level: RiskLevel
    balance: float
    peak_balance: float
    daily_pnl: float
    daily_drawdown_pct: float
    consecutive_losses: int
    open_positions_count: int
    open_risk_pct: float
    entries_allowed: bool
    reasons: tuple[str, ...] = ()
