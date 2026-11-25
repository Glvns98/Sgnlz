"""Centralized Trade Management Decision Engine & Position Lifecycle State Machine."""

from __future__ import annotations

import hashlib

from bot.config.settings import ManagementSettings, SymbolProfile
from bot.core.enums import (
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    MomentumState,
    MomentumTransition,
    TradeDirection,
    TradeStage,
)
from bot.core.models import (
    ClosedTrade,
    ManagementDecision,
    ManagementEvent,
    MarketStateSnapshot,
    Position,
)
from bot.management.exit_engine import ExitEngine
from bot.management.profit_manager import ProfitStateManager
from bot.management.stop_manager import AdaptiveStopManager


class TradeManager:
    """Evaluates active exposure and orchestrates the 10-stage position lifecycle."""

    def __init__(self, settings: ManagementSettings) -> None:
        self.settings = settings
        self.stop_manager = AdaptiveStopManager(settings)
        self.profit_manager = ProfitStateManager(settings)
        self.exit_engine = ExitEngine(settings)

    def evaluate(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile | None = None,
        risk_level: str = "NORMAL",
    ) -> ManagementDecision:
        """Return an auditable ManagementDecision for the given position and market state."""
        policy = position.policy
        cur_r = position.unrealized_r()
        is_buy = position.direction == TradeDirection.BUY

        # Update momentum decay counter heuristically on evaluation
        decelerating = (is_buy and market_state.has_transition(MomentumTransition.BULLISH_DECELERATION)) or (
            not is_buy and market_state.has_transition(MomentumTransition.BEARISH_DECELERATION)
        )
        opposing = (
            is_buy and market_state.momentum_state in (MomentumState.WEAK_BEARISH, MomentumState.STRONG_BEARISH)
        ) or (
            not is_buy and market_state.momentum_state in (MomentumState.WEAK_BULLISH, MomentumState.STRONG_BULLISH)
        )
        if decelerating or opposing or market_state.has_transition(MomentumTransition.MOMENTUM_EXHAUSTION):
            position.decay_bars += 1
        else:
            position.decay_bars = max(0, position.decay_bars - 1)

        base_inputs = {
            "policy": policy.value,
            "unrealized_r": round(cur_r, 4),
            "mfe_r": round(position.mfe_r, 4),
            "mae_r": round(position.mae_r, 4),
            "bars_held": position.bars_held,
            "decay_bars": position.decay_bars,
            "squeeze_state": market_state.squeeze_state.value,
            "momentum_state": market_state.momentum_state.value,
            "regime": market_state.regime.value,
            "current_stop": position.current_stop,
        }

        if policy == ManagementPolicy.STATIC_EXIT:
            return self._eval_static(position, market_state, risk_level, base_inputs)
        if policy == ManagementPolicy.BREAKEVEN_ONLY:
            return self._eval_breakeven_only(position, market_state, risk_level, base_inputs)
        if policy == ManagementPolicy.TRAILING_ONLY:
            return self._eval_trailing_only(position, market_state, symbol_cfg, risk_level, base_inputs)
        if policy == ManagementPolicy.MOMENTUM_EXIT:
            return self._eval_momentum_exit(position, market_state, risk_level, base_inputs)

        return self._eval_dynamic(position, market_state, symbol_cfg, risk_level, base_inputs)

    def _eval_static(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        risk_level: str,
        inputs: dict[str, object],
    ) -> ManagementDecision:
        cur_r = position.unrealized_r()
        if cur_r >= 2.0:
            return ManagementDecision(
                action=ManagementAction.EXIT,
                reason="static_2r_take_profit",
                confidence=0.95,
                risk_state=risk_level,
                target_stage=TradeStage.EXIT,
                exit_reason=ExitReason.TAKE_PROFIT,
                inputs=inputs,
                timestamp=market_state.timestamp,
            )
        stage = TradeStage.PROFITABLE if cur_r >= 0.25 else TradeStage.INITIAL_PROTECTION
        return ManagementDecision(
            action=ManagementAction.HOLD,
            reason="static_hold_until_sl_or_tp",
            confidence=0.60,
            risk_state=risk_level,
            target_stage=stage,
            inputs=inputs,
            timestamp=market_state.timestamp,
        )

    def _eval_breakeven_only(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        risk_level: str,
        inputs: dict[str, object],
    ) -> ManagementDecision:
        cur_r = position.unrealized_r()
        if cur_r >= 2.0:
            return ManagementDecision(
                action=ManagementAction.EXIT,
                reason="breakeven_policy_2r_take_profit",
                confidence=0.95,
                risk_state=risk_level,
                target_stage=TradeStage.EXIT,
                exit_reason=ExitReason.TAKE_PROFIT,
                inputs=inputs,
                timestamp=market_state.timestamp,
            )
        if cur_r >= self.settings.breakeven_trigger_r:
            be_stop = self.stop_manager.compute_breakeven_stop(position)
            if self.stop_manager.is_better_stop(position.direction, be_stop, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.MOVE_TO_BREAKEVEN,
                    reason="breakeven_threshold_reached",
                    confidence=0.82,
                    risk_state=risk_level,
                    target_stage=TradeStage.PROFIT_PROTECTION,
                    new_stop_price=be_stop,
                    inputs=inputs,
                    timestamp=market_state.timestamp,
                )
        stage = TradeStage.PROFITABLE if cur_r >= 0.25 else TradeStage.INITIAL_PROTECTION
        return ManagementDecision(
            action=ManagementAction.HOLD,
            reason="breakeven_policy_hold",
            confidence=0.65,
            risk_state=risk_level,
            target_stage=stage,
            inputs=inputs,
            timestamp=market_state.timestamp,
        )

    def _eval_trailing_only(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile | None,
        risk_level: str,
        inputs: dict[str, object],
    ) -> ManagementDecision:
        cur_r = position.unrealized_r()
        if cur_r >= self.settings.breakeven_trigger_r:
            trail_stop = self.stop_manager.compute_dynamic_trailing_stop(position, market_state, symbol_cfg)
            if self.stop_manager.is_better_stop(position.direction, trail_stop, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.TRAIL,
                    reason="trailing_only_stop_update",
                    confidence=0.80,
                    risk_state=risk_level,
                    target_stage=TradeStage.TRAILING,
                    new_stop_price=trail_stop,
                    inputs=inputs,
                    timestamp=market_state.timestamp,
                )
        stage = TradeStage.PROFITABLE if cur_r >= 0.25 else TradeStage.INITIAL_PROTECTION
        return ManagementDecision(
            action=ManagementAction.HOLD,
            reason="trailing_only_hold",
            confidence=0.65,
            risk_state=risk_level,
            target_stage=stage,
            inputs=inputs,
            timestamp=market_state.timestamp,
        )

    def _eval_momentum_exit(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        risk_level: str,
        inputs: dict[str, object],
    ) -> ManagementDecision:
        exit_check = self.exit_engine.evaluate(position, market_state)
        if exit_check.should_exit:
            return ManagementDecision(
                action=ManagementAction.EMERGENCY_EXIT if exit_check.emergency else ManagementAction.EXIT,
                reason=exit_check.reason_code,
                confidence=0.88,
                risk_state=risk_level,
                target_stage=TradeStage.EXIT,
                exit_reason=exit_check.exit_reason,
                inputs=inputs,
                timestamp=market_state.timestamp,
            )
        cur_r = position.unrealized_r()
        stage = TradeStage.MOMENTUM_DECAY if position.decay_bars >= self.settings.momentum_exit_min_decay_bars else (
            TradeStage.PROFITABLE if cur_r >= 0.25 else TradeStage.INITIAL_PROTECTION
        )
        return ManagementDecision(
            action=ManagementAction.HOLD,
            reason="momentum_exit_policy_hold",
            confidence=0.70,
            risk_state=risk_level,
            target_stage=stage,
            inputs=inputs,
            timestamp=market_state.timestamp,
        )

    def _eval_dynamic(
        self,
        position: Position,
        market_state: MarketStateSnapshot,
        symbol_cfg: SymbolProfile | None,
        risk_level: str,
        inputs: dict[str, object],
    ) -> ManagementDecision:
        cur_r = position.unrealized_r()
        exit_check = self.exit_engine.evaluate(position, market_state)

        if exit_check.should_exit:
            return ManagementDecision(
                action=ManagementAction.EMERGENCY_EXIT if exit_check.emergency else ManagementAction.EXIT,
                reason=exit_check.reason_code,
                confidence=0.92 if exit_check.emergency else 0.85,
                risk_state=risk_level,
                target_stage=TradeStage.EXIT,
                exit_reason=exit_check.exit_reason,
                inputs={**inputs, "thesis_status": exit_check.thesis_status},
                timestamp=market_state.timestamp,
            )

        if exit_check.reduce_risk and position.remaining_fraction > 0.55:
            tighter = self.stop_manager.clamp_monotonic(
                position,
                position.current_price - position.initial_risk_distance * 0.55
                if position.direction == TradeDirection.BUY
                else position.current_price + position.initial_risk_distance * 0.55,
            )
            return ManagementDecision(
                action=ManagementAction.REDUCE,
                reason=exit_check.reason_code,
                confidence=0.76,
                risk_state="elevated",
                target_stage=TradeStage.MOMENTUM_DECAY,
                new_stop_price=tighter if self.stop_manager.is_better_stop(position.direction, tighter, position.current_stop) else None,
                reduce_fraction=self.settings.partial_reduce_fraction,
                inputs={**inputs, "thesis_status": exit_check.thesis_status},
                timestamp=market_state.timestamp,
            )

        if exit_check.tighten_stop:
            ratio = 0.60 if cur_r < 0 else 0.45
            candidate = (
                position.current_price - position.initial_risk_distance * ratio
                if position.direction == TradeDirection.BUY
                else position.current_price + position.initial_risk_distance * ratio
            )
            tighter = self.stop_manager.clamp_monotonic(position, candidate)
            if self.stop_manager.is_better_stop(position.direction, tighter, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.TIGHTEN_STOP,
                    reason=exit_check.reason_code,
                    confidence=0.78,
                    risk_state="elevated",
                    target_stage=TradeStage.MOMENTUM_DECAY if position.decay_bars >= 1 else position.stage,
                    new_stop_price=tighter,
                    inputs={**inputs, "thesis_status": exit_check.thesis_status},
                    timestamp=market_state.timestamp,
                )

        profit = self.profit_manager.evaluate(position, market_state)
        p_inputs = {
            **inputs,
            "profit_tier": profit.profit_tier,
            "continuation_score": round(profit.continuation_score, 3),
            "reversal_risk": round(profit.reversal_risk, 3),
            "giveback_ratio": round(profit.giveback_ratio, 3),
        }

        if profit.recommendation == "EXIT":
            return ManagementDecision(
                action=ManagementAction.EXIT,
                reason=profit.reason,
                confidence=min(0.95, 0.55 + profit.reversal_risk * 0.4),
                risk_state=risk_level,
                target_stage=TradeStage.EXIT,
                exit_reason=ExitReason.MOMENTUM_DETERIORATION,
                inputs=p_inputs,
                timestamp=market_state.timestamp,
            )

        if profit.recommendation == "REDUCE" and position.remaining_fraction > 0.55:
            lock_stop = self.stop_manager.compute_profit_lock_stop(position, self.settings.profit_lock_floor_r)
            return ManagementDecision(
                action=ManagementAction.REDUCE,
                reason=profit.reason,
                confidence=0.80,
                risk_state=risk_level,
                target_stage=TradeStage.PROFIT_PROTECTION,
                new_stop_price=lock_stop if self.stop_manager.is_better_stop(position.direction, lock_stop, position.current_stop) else None,
                reduce_fraction=self.settings.partial_reduce_fraction,
                inputs=p_inputs,
                timestamp=market_state.timestamp,
            )

        if cur_r >= self.settings.strong_profit_trigger_r:
            trail_stop = self.stop_manager.compute_dynamic_trailing_stop(position, market_state, symbol_cfg)
            if self.stop_manager.is_better_stop(position.direction, trail_stop, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.TRAIL,
                    reason=profit.reason,
                    confidence=0.84,
                    risk_state=risk_level,
                    target_stage=TradeStage.TRAILING,
                    new_stop_price=trail_stop,
                    inputs=p_inputs,
                    timestamp=market_state.timestamp,
                )

        if cur_r >= self.settings.profit_lock_trigger_r:
            floor_r = (
                self.settings.strong_profit_floor_r
                if profit.reversal_risk >= 0.45
                else self.settings.profit_lock_floor_r
            )
            lock_stop = self.stop_manager.compute_profit_lock_stop(position, floor_r)
            if self.stop_manager.is_better_stop(position.direction, lock_stop, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.LOCK_PROFIT,
                    reason=profit.reason,
                    confidence=0.82,
                    risk_state=risk_level,
                    target_stage=TradeStage.PROFIT_PROTECTION,
                    new_stop_price=lock_stop,
                    inputs=p_inputs,
                    timestamp=market_state.timestamp,
                )

        if cur_r >= self.settings.breakeven_trigger_r:
            be_stop = self.stop_manager.compute_breakeven_stop(position)
            if self.stop_manager.is_better_stop(position.direction, be_stop, position.current_stop):
                return ManagementDecision(
                    action=ManagementAction.MOVE_TO_BREAKEVEN,
                    reason="breakeven_protection_activated",
                    confidence=0.80,
                    risk_state=risk_level,
                    target_stage=TradeStage.PROFIT_PROTECTION,
                    new_stop_price=be_stop,
                    inputs=p_inputs,
                    timestamp=market_state.timestamp,
                )

        # Determine non-mutating lifecycle stage
        if position.decay_bars >= self.settings.momentum_exit_min_decay_bars and cur_r > 0:
            next_stage = TradeStage.MOMENTUM_DECAY
        elif cur_r >= self.settings.runner_trigger_r:
            next_stage = TradeStage.TRAILING
        elif cur_r >= self.settings.profit_lock_trigger_r:
            next_stage = TradeStage.PROFIT_PROTECTION
        elif cur_r >= 0.25:
            next_stage = TradeStage.PROFITABLE
        else:
            next_stage = TradeStage.INITIAL_PROTECTION

        return ManagementDecision(
            action=ManagementAction.HOLD,
            reason=profit.reason,
            confidence=max(0.55, profit.continuation_score),
            risk_state=risk_level,
            target_stage=next_stage,
            inputs=p_inputs,
            timestamp=market_state.timestamp,
        )

    def apply_decision(
        self,
        position: Position,
        decision: ManagementDecision,
    ) -> ManagementEvent | None:
        """Apply a ManagementDecision onto a Position, preserving monotonic stop invariants."""
        stage_before = position.stage
        changed = decision.action != ManagementAction.HOLD or decision.target_stage != stage_before

        if decision.new_stop_price is not None:
            if self.stop_manager.is_better_stop(position.direction, decision.new_stop_price, position.current_stop):
                position.current_stop = decision.new_stop_price
                changed = True

        if decision.action == ManagementAction.REDUCE and decision.reduce_fraction > 0.0:
            close_frac = min(position.remaining_fraction, position.remaining_fraction * decision.reduce_fraction)
            partial_pnl = position.r_for_price(position.current_price) * position.initial_risk_amount * close_frac
            position.realized_pnl += partial_pnl
            position.remaining_fraction = max(0.0, position.remaining_fraction - close_frac)
            changed = True

        position.stage = decision.target_stage

        if not changed:
            return None

        raw_ev_id = (
            f"{position.trade_id}:{decision.timestamp.isoformat()}:"
            f"{stage_before.value}:{position.stage.value}:{decision.action.value}:"
            f"{len(position.management_events)}"
        )
        event = ManagementEvent(
            event_id=hashlib.sha256(raw_ev_id.encode("utf-8")).hexdigest()[:12],
            trade_id=position.trade_id,
            timestamp=decision.timestamp,
            stage_before=stage_before,
            stage_after=position.stage,
            decision=decision,
            current_price=position.current_price,
            unrealized_pnl=position.unrealized_pnl(),
            unrealized_r=position.unrealized_r(),
            mfe_r=position.mfe_r,
            mae_r=position.mae_r,
        )
        position.management_events.append(event)
        return event

    @staticmethod
    def build_closed_trade(
        position: Position,
        exit_price: float,
        exit_reason: ExitReason,
        market_state: MarketStateSnapshot,
    ) -> ClosedTrade:
        """Convert a closed Position into a complete Section 17 Trade Journal record."""
        final_pnl = position.total_pnl_at(exit_price)
        r_mult = final_pnl / position.initial_risk_amount if position.initial_risk_amount > 0 else 0.0
        duration = max(0.0, (market_state.timestamp - position.opened_at).total_seconds())
        actions = tuple(ev.decision.action.value for ev in position.management_events)
        events = tuple(ev.to_dict() for ev in position.management_events)

        # Entry quality vs Management quality attribution
        entry_quality = "GOOD_ENTRY" if position.mfe_r >= 0.75 else ("NEUTRAL_ENTRY" if position.mfe_r >= 0.25 else "POOR_ENTRY")
        if position.mfe_r >= 1.0 and r_mult <= 0.0:
            mgmt_quality = "OVER_MANAGED_OR_GAVE_BACK"
        elif position.mfe_r >= 0.75 and r_mult >= position.mfe_r * 0.55:
            mgmt_quality = "WELL_MANAGED"
        elif r_mult > -0.8 and position.mae_r < -0.5 and exit_reason in (
            ExitReason.BREAKEVEN_STOP,
            ExitReason.PROFIT_LOCK_STOP,
            ExitReason.TRAILING_STOP,
            ExitReason.MOMENTUM_DETERIORATION,
            ExitReason.TIME_STAGNATION,
        ):
            mgmt_quality = "RISK_PROTECTED"
        elif r_mult < 0 and position.mfe_r < 0.25:
            mgmt_quality = "MARKET_OR_ENTRY_LOSS"
        else:
            mgmt_quality = "STANDARD_EXECUTION"

        summary = (
            f"{entry_quality} | {mgmt_quality} | exit={exit_reason.value} | "
            f"MFE={position.mfe_r:+.2f}R, MAE={position.mae_r:+.2f}R, Final={r_mult:+.2f}R"
        )

        position.stage = TradeStage.CLOSED
        return ClosedTrade(
            trade_id=position.trade_id,
            timestamp=position.opened_at,
            closed_at=market_state.timestamp,
            symbol=position.symbol,
            direction=position.direction,
            entry=position.entry_price,
            initial_risk=position.initial_risk_amount,
            initial_stop=position.initial_stop,
            final_stop=position.current_stop,
            exit=exit_price,
            exit_reason=exit_reason,
            duration_sec=duration,
            bars_held=position.bars_held,
            pnl=final_pnl,
            r_multiple=r_mult,
            mfe=position.mfe_r,
            mae=position.mae_r,
            highest_profit=position.max_unrealized_pnl,
            lowest_drawdown=position.min_unrealized_pnl,
            squeeze_state_at_entry=position.squeeze_state_at_entry,
            squeeze_state_at_exit=market_state.squeeze_state.value,
            momentum_at_entry=position.momentum_at_entry,
            momentum_at_exit=market_state.squeeze.val,
            policy=position.policy,
            management_events=events,
            management_actions=actions,
            entry_quality=entry_quality,
            management_quality=mgmt_quality,
            attribution_summary=summary,
        )
