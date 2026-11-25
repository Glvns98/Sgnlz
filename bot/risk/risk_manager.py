"""Risk Manager: position sizing, portfolio exposure caps, circuit breakers, and anti-martingale guards."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config.settings import RiskSettings, SymbolProfile
from bot.core.enums import MarketRegime, RiskLevel, SignalType
from bot.core.models import MarketStateSnapshot, Position, RiskState, TradeSignal


@dataclass(frozen=True)
class RiskApproval:
    allowed: bool
    stake: float
    risk_amount: float
    reasons: tuple[str, ...] = ()


class RiskManager:
    """Enforces account preservation, sizing, concurrency limits, and prohibited-behavior guards."""

    def __init__(self, settings: RiskSettings) -> None:
        self.settings = settings
        self.peak_balance: float = settings.account_balance

    def evaluate_risk_state(
        self,
        balance: float,
        open_positions: list[Position],
        daily_pnl: float,
        consecutive_losses: int,
    ) -> RiskState:
        self.peak_balance = max(self.peak_balance, balance)
        daily_dd_pct = max(0.0, -daily_pnl / max(balance - daily_pnl, 1e-9))
        open_risk_amount = sum(p.initial_risk_amount * p.remaining_fraction for p in open_positions)
        open_risk_pct = open_risk_amount / max(balance, 1e-9)

        reasons: list[str] = []
        level = RiskLevel.NORMAL
        entries_allowed = True

        if self.settings.enforce_portfolio_caps:
            if daily_dd_pct >= self.settings.max_daily_loss:
                level = RiskLevel.HALTED
                entries_allowed = False
                reasons.append("max_daily_loss_reached")
            elif consecutive_losses >= self.settings.max_consecutive_losses:
                level = RiskLevel.HALTED
                entries_allowed = False
                reasons.append("max_consecutive_losses_reached")
            elif len(open_positions) >= self.settings.max_open_positions:
                level = RiskLevel.RESTRICTED
                entries_allowed = False
                reasons.append("max_open_positions_reached")
            elif open_risk_pct >= self.settings.max_concurrent_risk:
                level = RiskLevel.RESTRICTED
                entries_allowed = False
                reasons.append("max_concurrent_risk_reached")
            elif (
                daily_dd_pct >= self.settings.max_daily_loss * 0.65
                or consecutive_losses >= max(2, self.settings.max_consecutive_losses - 2)
            ):
                level = RiskLevel.ELEVATED
                reasons.append("elevated_drawdown_or_loss_streak_throttle")

        return RiskState(
            level=level,
            balance=balance,
            peak_balance=self.peak_balance,
            daily_pnl=daily_pnl,
            daily_drawdown_pct=daily_dd_pct,
            consecutive_losses=consecutive_losses,
            open_positions_count=len(open_positions),
            open_risk_pct=open_risk_pct,
            entries_allowed=entries_allowed,
            reasons=tuple(reasons),
        )

    def size_position(
        self,
        signal: TradeSignal,
        symbol_cfg: SymbolProfile,
        balance: float,
        risk_state: RiskState,
    ) -> tuple[float, float]:
        """Compute risk-bounded stake and dollar risk (strictly anti-martingale)."""
        base_risk_pct = self.settings.risk_per_trade * symbol_cfg.risk_multiplier
        if self.settings.enforce_portfolio_caps and risk_state.level == RiskLevel.ELEVATED:
            base_risk_pct *= 0.50

        target_risk = balance * base_risk_pct
        if self.settings.enforce_portfolio_caps:
            remaining_budget = max(
                0.0,
                (self.settings.max_concurrent_risk * balance) - (risk_state.open_risk_pct * balance),
            )
            risk_amount = min(target_risk, remaining_budget) if remaining_budget > 0 else 0.0
        else:
            risk_amount = target_risk

        stake = max(self.settings.min_stake, min(self.settings.max_stake, risk_amount))
        if risk_amount < self.settings.min_stake:
            return 0.0, 0.0
        return round(stake, 2), round(stake, 2)

    def check_entry_allowed(
        self,
        signal: TradeSignal,
        symbol_cfg: SymbolProfile,
        open_positions: list[Position],
        risk_state: RiskState,
        market_state: MarketStateSnapshot,
        *,
        is_recovery_averaging: bool = False,
    ) -> RiskApproval:
        """Validate new entry against signal/position nature and optional portfolio caps."""
        if is_recovery_averaging:
            return RiskApproval(False, 0.0, 0.0, ("averaging_down_prohibited",))

        if self.settings.allow_martingale or self.settings.allow_averaging_down:
            return RiskApproval(False, 0.0, 0.0, ("prohibited_risk_policy",))

        if signal.signal_type not in (SignalType.NEW_OPPORTUNITY, SignalType.CONTINUATION) or signal.direction is None:
            return RiskApproval(False, 0.0, 0.0, ("signal_not_actionable",))

        if self.settings.enforce_portfolio_caps and not risk_state.entries_allowed:
            return RiskApproval(False, 0.0, 0.0, risk_state.reasons or ("entries_blocked_by_risk_state",))

        if self.settings.abnormal_volatility_block and market_state.regime == MarketRegime.ABNORMAL_VOLATILITY:
            return RiskApproval(False, 0.0, 0.0, ("abnormal_volatility_block",))

        sym_positions = [p for p in open_positions if p.symbol == signal.symbol]
        max_sym_exp = min(self.settings.max_symbol_exposure, symbol_cfg.max_exposure)
        if len(sym_positions) >= max_sym_exp:
            return RiskApproval(False, 0.0, 0.0, ("max_symbol_exposure_reached",))

        if self.settings.enforce_portfolio_caps:
            group_positions = [
                p for p in open_positions if p.metadata.get("submarket") == symbol_cfg.submarket
            ]
            if len(group_positions) >= self.settings.max_per_group_open:
                return RiskApproval(False, 0.0, 0.0, ("max_group_exposure_reached",))

        stake, risk_amount = self.size_position(signal, symbol_cfg, risk_state.balance, risk_state)
        if stake <= 0.0:
            return RiskApproval(False, 0.0, 0.0, ("insufficient_risk_budget_for_min_stake",))

        return RiskApproval(True, stake=stake, risk_amount=risk_amount, reasons=("risk_approved",))
