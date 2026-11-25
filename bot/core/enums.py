"""Domain enumerations for the Deriv Squeeze Trade Management Engine."""

from __future__ import annotations

from enum import StrEnum


class OperatingMode(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_TRADING = "LIVE_TRADING"


class SqueezeState(StrEnum):
    SQUEEZE_ON = "SQUEEZE_ON"
    SQUEEZE_OFF = "SQUEEZE_OFF"
    NO_SQUEEZE = "NO_SQUEEZE"
    WARMUP = "WARMUP"


class MomentumState(StrEnum):
    STRONG_BULLISH = "STRONG_BULLISH"
    WEAK_BULLISH = "WEAK_BULLISH"
    NEUTRAL = "NEUTRAL"
    WEAK_BEARISH = "WEAK_BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class MomentumTransition(StrEnum):
    INCREASING = "INCREASING"
    DECREASING = "DECREASING"
    BULLISH_ACCELERATION = "BULLISH_ACCELERATION"
    BULLISH_DECELERATION = "BULLISH_DECELERATION"
    BEARISH_ACCELERATION = "BEARISH_ACCELERATION"
    BEARISH_DECELERATION = "BEARISH_DECELERATION"
    ZERO_CROSS_UP = "ZERO_CROSS_UP"
    ZERO_CROSS_DOWN = "ZERO_CROSS_DOWN"
    MOMENTUM_REVERSAL = "MOMENTUM_REVERSAL"
    MOMENTUM_EXHAUSTION = "MOMENTUM_EXHAUSTION"
    STEADY = "STEADY"


class MarketRegime(StrEnum):
    COMPRESSION = "COMPRESSION"
    EXPANSION_TREND = "EXPANSION_TREND"
    MOMENTUM_EXHAUSTION = "MOMENTUM_EXHAUSTION"
    CHOP = "CHOP"
    ABNORMAL_VOLATILITY = "ABNORMAL_VOLATILITY"
    WARMUP = "WARMUP"


class SignalType(StrEnum):
    NEW_OPPORTUNITY = "NEW_OPPORTUNITY"
    CONTINUATION = "CONTINUATION"
    MOMENTUM_STRENGTHENING = "MOMENTUM_STRENGTHENING"
    MOMENTUM_WEAKENING = "MOMENTUM_WEAKENING"
    REVERSAL_WARNING = "REVERSAL_WARNING"
    EXIT_WARNING = "EXIT_WARNING"
    NO_ACTION = "NO_ACTION"


class TradeDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStage(StrEnum):
    PENDING = "PENDING"
    ENTERED = "ENTERED"
    INITIAL_PROTECTION = "INITIAL_PROTECTION"
    PROFITABLE = "PROFITABLE"
    PROFIT_PROTECTION = "PROFIT_PROTECTION"
    TRAILING = "TRAILING"
    MOMENTUM_DECAY = "MOMENTUM_DECAY"
    EXIT = "EXIT"
    CLOSED = "CLOSED"
    POST_TRADE_ANALYSIS = "POST_TRADE_ANALYSIS"


class ManagementAction(StrEnum):
    HOLD = "HOLD"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    MOVE_TO_BREAKEVEN = "MOVE_TO_BREAKEVEN"
    TRAIL = "TRAIL"
    LOCK_PROFIT = "LOCK_PROFIT"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class ManagementPolicy(StrEnum):
    STATIC_EXIT = "STATIC_EXIT"
    BREAKEVEN_ONLY = "BREAKEVEN_ONLY"
    TRAILING_ONLY = "TRAILING_ONLY"
    MOMENTUM_EXIT = "MOMENTUM_EXIT"
    DYNAMIC_MANAGEMENT = "DYNAMIC_MANAGEMENT"


class ExitReason(StrEnum):
    STOP_LOSS = "STOP_LOSS"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    PROFIT_LOCK_STOP = "PROFIT_LOCK_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    MOMENTUM_DETERIORATION = "MOMENTUM_DETERIORATION"
    THESIS_INVALIDATION = "THESIS_INVALIDATION"
    TIME_STAGNATION = "TIME_STAGNATION"
    ABNORMAL_REGIME = "ABNORMAL_REGIME"
    HARD_INVALIDATION = "HARD_INVALIDATION"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    RISK_CIRCUIT_BREAKER = "RISK_CIRCUIT_BREAKER"
    END_OF_REPLAY = "END_OF_REPLAY"


class AlertPriority(StrEnum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"

    @property
    def severity(self) -> int:
        order = {
            AlertPriority.INFO: 10,
            AlertPriority.NOTICE: 20,
            AlertPriority.WARNING: 30,
            AlertPriority.CRITICAL: 40,
            AlertPriority.EMERGENCY: 50,
        }
        return order[self]

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, AlertPriority):
            return NotImplemented
        return self.severity >= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, AlertPriority):
            return NotImplemented
        return self.severity > other.severity


class RiskLevel(StrEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    RESTRICTED = "RESTRICTED"
    HALTED = "HALTED"
