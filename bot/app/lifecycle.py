"""Live Engine Orchestrator: restart recovery, Deriv streaming, trade lifecycle, and clean shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
import signal as os_signal
import time
from datetime import UTC, datetime

from bot.app.logging_setup import DecisionAuditLogger
from bot.config.settings import EngineSettings, SymbolProfile
from bot.core.enums import (
    AlertPriority,
    ExitReason,
    ManagementAction,
    MomentumState,
    OperatingMode,
    SignalType,
    TradeStage,
)
from bot.core.events import EngineEvent, EventBus
from bot.core.models import (
    Candle,
    ManagementDecision,
    ManagementEvent,
    MarketStateSnapshot,
    Position,
    RiskState,
    TradeSignal,
)
from bot.dashboard.terminal import DashboardState, TerminalDashboard
from bot.deriv.account import AccountMonitor
from bot.deriv.client import DerivAPIClient
from bot.deriv.discovery import discover_active_symbols
from bot.deriv.execution import ExecutionEngine
from bot.deriv.market_data import MarketDataEngine
from bot.indicators.squeeze import SqueezeConfig
from bot.management.trade_manager import TradeManager
from bot.notifications.formatter import (
    build_management_comment_bundle,
    build_trade_closed_comment_bundle,
    build_trade_opened_bundle,
)
from bot.notifications.reddit import RedditPublisher
from bot.notifications.router import NotificationRouter
from bot.notifications.slack import SlackNotifier
from bot.risk.risk_manager import RiskManager
from bot.storage.database import SQLiteStorageEngine
from bot.strategy.market_state import MultiTimeframeAnalyzer
from bot.strategy.signals import SignalEngine

logger = logging.getLogger(__name__)


class EngineOrchestrator:
    """Coordinates live/paper/analysis trading loops with safe restart recovery."""

    def __init__(self, settings: EngineSettings, *, show_dashboard: bool = True) -> None:
        self.settings = settings
        self.show_dashboard = show_dashboard and settings.dashboard.enabled
        self.bus = EventBus()
        self.storage = SQLiteStorageEngine(settings.storage.sqlite_path)
        self.storage.save_config_version(
            settings.config_version,
            settings.model_dump_json(
                exclude={
                    "deriv": {"token"},
                    "notifications": {
                        "slack_bot_token",
                        "slack_webhook_url",
                        "reddit_client_secret",
                        "reddit_password",
                    },
                }
            ),
        )
        self.audit_log = DecisionAuditLogger(settings.logging.json_log_path)
        self.risk_manager = RiskManager(settings.risk)
        self.trade_manager = TradeManager(settings.management)
        self.signal_engine = SignalEngine()
        self.notifier = NotificationRouter(settings.notifications)
        self.dashboard = TerminalDashboard()

        self.client = DerivAPIClient(
            settings.deriv,
            on_disconnect=self._on_ws_disconnect,
            on_reconnect=self._on_ws_reconnect,
            on_malformed_message=self._on_malformed_msg,
        )
        self.market_data = MarketDataEngine(self.client)
        self.account = AccountMonitor(self.client, initial_balance=settings.risk.account_balance)
        self.execution = ExecutionEngine(settings, self.risk_manager, self.storage, client=self.client)

        self.analyzers: dict[str, MultiTimeframeAnalyzer] = {}
        self.market_states: dict[str, MarketStateSnapshot] = {}
        self.open_positions: list[Position] = []
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.latest_action: str = "BOOTING"
        self.latest_reason: str = "initializing"
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._last_symbol_refresh: float = 0.0

    def _ensure_analyzer(self, profile: SymbolProfile) -> MultiTimeframeAnalyzer:
        if profile.symbol not in self.analyzers:
            sq = self.settings.squeeze_for(profile.symbol)
            sq_cfg = SqueezeConfig(
                bb_length=sq.bb_length,
                bb_mult=sq.bb_mult,
                kc_length=sq.kc_length,
                kc_mult=sq.kc_mult,
                use_true_range=sq.use_true_range,
                lazybear_mult_parity=sq.lazybear_mult_parity,
                tr_handle_na=sq.tr_handle_na,
            )
            self.analyzers[profile.symbol] = MultiTimeframeAnalyzer(
                symbol=profile.symbol,
                primary_tf=profile.primary_timeframe,
                higher_tf=profile.higher_timeframe,
                lower_tf=profile.lower_timeframe,
                squeeze_config=sq_cfg,
            )
        return self.analyzers[profile.symbol]

    async def start(self, *, max_seconds: float | None = None) -> None:
        """Start engine, restore persisted trades, connect to Deriv, and run until stopped."""
        if self.settings.mode == OperatingMode.LIVE_TRADING:
            if os.environ.get("DERIV_ALLOW_LIVE_EXECUTION", "").lower() != "true":
                raise PermissionError(
                    "Refusing to start LIVE_TRADING without DERIV_ALLOW_LIVE_EXECUTION=true"
                )

        # 1. Restore persisted state from SQLite BEFORE processing new candles
        self.open_positions = self.storage.load_open_positions()
        if self.open_positions:
            logger.info("Restored %d open positions from SQLite", len(self.open_positions))

        # 2. Connect to Deriv API & reconcile account state
        try:
            await self.client.connect()
            await self.client.authorize()
            await self.account.refresh_balance()
            await self.account.refresh_portfolio()
            await self.account.subscribe_balance()
            reconcile = self.account.reconcile_positions(self.open_positions)
            if reconcile.missing_on_broker_ids or reconcile.untracked_broker_contract_ids:
                logger.warning("Reconciliation discrepancy: %s", reconcile)
                self.storage.record_system_event(
                    AlertPriority.WARNING,
                    "account_reconcile",
                    "Position reconciliation notice",
                    {
                        "missing": reconcile.missing_on_broker_ids,
                        "untracked": reconcile.untracked_broker_contract_ids,
                    },
                )
        except Exception as exc:
            logger.error("Deriv connection failed cleanly during startup: %s", exc)
            self.storage.record_system_event(
                AlertPriority.EMERGENCY,
                "deriv_client",
                f"Startup connection error: {exc}",
            )
            if max_seconds is None:
                raise
            return

        # 3. Discover full Deriv active symbol catalog when enabled
        if self.settings.deriv.discover_all_symbols:
            try:
                discovered = await discover_active_symbols(self.client, overrides=self.settings.symbols)
                if discovered:
                    self.settings.symbols = discovered
                    self._last_symbol_refresh = time.monotonic()
                    open_n = sum(1 for p in discovered.values() if p.exchange_is_open)
                    logger.info(
                        "Discovered %d Deriv symbols (%d currently open)",
                        len(discovered),
                        open_n,
                    )
            except Exception as exc:
                logger.warning("Dynamic symbol discovery failed; using config/symbols.yaml (%s)", exc)

        for profile in self.settings.enabled_symbols():
            self._ensure_analyzer(profile)

        # 4. Hydrate historical candles across all discovered symbols (open markets prioritized)
        ordered_profiles = sorted(
            self.settings.enabled_symbols(),
            key=lambda p: (0 if p.exchange_is_open else 1, p.submarket, p.symbol),
        )
        for profile in ordered_profiles:
            tf = profile.primary_timeframe
            try:
                hist = await self.market_data.bootstrap_history(
                    profile.symbol, tf, count=self.settings.deriv.history_candles
                )
                for candle in hist:
                    st = self.analyzers[profile.symbol].update(candle)
                    if st is not None:
                        self.market_states[profile.symbol] = st
            except Exception as exc:
                    logger.debug("Skipping hydration for %s (%ds): %s", profile.symbol, tf, exc)

        # Backfill rich parent cards for restored positions if missing thread_ts
        for profile in ordered_profiles:
            latest_st = self.market_states.get(profile.symbol)
            if latest_st is None:
                continue
            active = next((p for p in self.open_positions if p.symbol == profile.symbol), None)
            if active is not None and not active.metadata.get("slack_thread_ts"):
                backfill_sig = TradeSignal(
                    signal_id=str(active.metadata.get("signal_id", f"restored-{active.trade_id}")),
                    symbol=active.symbol,
                    direction=active.direction,
                    signal_type=SignalType.NEW_OPPORTUNITY,
                    squeeze_state=latest_st.squeeze_state,
                    momentum_state=MomentumState(active.momentum_state_at_entry),
                    momentum_strength=latest_st.squeeze.momentum_strength,
                    confidence=float(active.metadata.get("confidence", 0.75)),
                    regime=latest_st.regime,
                    timestamp=active.opened_at,
                    timeframe_sec=profile.primary_timeframe,
                    entry_price=active.entry_price,
                    suggested_stop=active.initial_stop,
                    initial_risk_distance=active.initial_risk_distance,
                    reasons=("restored_active_exposure", "squeeze_momentum_aligned"),
                )
                await self._publish_position_opened(active, backfill_sig, latest_st, profile)

        # Evaluate hydrated signals across the whole universe, ranked by confidence
        candidates: list[tuple[TradeSignal, MarketStateSnapshot, SymbolProfile]] = []
        for profile in ordered_profiles:
            if not profile.exchange_is_open:
                continue
            latest_st = self.market_states.get(profile.symbol)
            if latest_st is None:
                continue
            if any(p.symbol == profile.symbol for p in self.open_positions):
                continue
            sig = self.signal_engine.evaluate(latest_st, profile, active_position=None)
            if sig.signal_type in (SignalType.NEW_OPPORTUNITY, SignalType.CONTINUATION):
                self.storage.record_signal(sig, executed=False)
                candidates.append((sig, latest_st, profile))

        candidates.sort(key=lambda item: item[0].confidence, reverse=True)
        for sig, latest_st, profile in candidates:
            rs = self._current_risk_state()
            receipt = await self.execution.submit_signal(
                sig, profile, self.open_positions, rs, latest_st
            )
            if receipt.accepted and receipt.position is not None:
                self.open_positions.append(receipt.position)
                self.storage.record_signal(sig, executed=True)
                await self._publish_position_opened(receipt.position, sig, latest_st, profile)
            elif (
                self.settings.notifications.send_unentered_analysis_signals
                and (
                    "max_open_positions" in receipt.reason
                    or "max_group_exposure" in receipt.reason
                    or "max_concurrent_risk" in receipt.reason
                )
            ):
                await self._publish_analysis_signal(sig, latest_st, profile, receipt.reason)

        self.latest_action = "MONITORING"
        self.latest_reason = f"hydrated_{len(self.market_states)}_symbols"
        if self.market_states and self.settings.notifications.send_market_pulses:
            pulse_text = SlackNotifier.format_market_pulse(
                self.settings.mode.value,
                self.market_states,
                self.open_positions,
                self._current_risk_state(),
                symbols_cfg=self.settings.symbols,
            )
            pulse_reddit = RedditPublisher.build_market_pulse_post(
                self.market_states, symbols_cfg=self.settings.symbols
            )
            await self.notifier.notify(
                dedupe_key="startup:market_pulse",
                priority=AlertPriority.NOTICE,
                slack_text=pulse_text,
                reddit_post=pulse_reddit,
            )

        if self.show_dashboard:
            self.dashboard.print_snapshot(self._snapshot_dashboard())

        self._poll_task = asyncio.create_task(self._poll_sweep_loop(), name="deriv-poll-sweep")

        # 5. Wait for stop signal or bounded duration
        if max_seconds is not None:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max_seconds)
            except TimeoutError:
                pass
        else:
            await self._stop_event.wait()

        await self.shutdown()

    async def on_closed_candle(self, candle: Candle) -> None:
        """Process a newly closed candle through MarketState -> Management -> Signal -> Execution."""
        analyzer = self.analyzers.get(candle.symbol)
        profile = self.settings.symbols.get(candle.symbol)
        if analyzer is None or profile is None:
            return

        state = analyzer.update(candle)
        if state is None or candle.timeframe_sec != profile.primary_timeframe:
            return

        self.market_states[candle.symbol] = state
        self.storage.record_market_state(state)

        # Manage open positions on this symbol first
        for pos in list(self.open_positions):
            if pos.symbol != candle.symbol:
                continue
            pos.update_mark(candle.close, candle.high, candle.low, candle.timestamp, is_new_bar=True)

            if self.trade_manager.stop_manager.check_stop_hit(pos, candle.high, candle.low):
                await self._close_position(
                    pos,
                    pos.current_stop,
                    self._classify_stop_exit_reason(pos),
                    state,
                    profile,
                )
                continue

            risk_state = self._current_risk_state()
            prev_stop = pos.current_stop
            decision = self.trade_manager.evaluate(
                pos, state, symbol_cfg=profile, risk_level=risk_state.level.value
            )
            event = self.trade_manager.apply_decision(pos, decision)
            self.storage.upsert_position(pos)
            self.latest_action = decision.action.value
            self.latest_reason = decision.reason
            self.audit_log.log_decision(
                trade_id=pos.trade_id,
                market_state=state.to_dict(),
                position_state={"stage": pos.stage.value, "stop": pos.current_stop, "unrealized_r": pos.unrealized_r()},
                decision=decision.to_audit_dict(),
                risk_state=risk_state.level.value,
            )

            if event is not None and decision.action not in (ManagementAction.EXIT, ManagementAction.EMERGENCY_EXIT):
                self.storage.record_management_event(event)
                await self._maybe_notify_management_event(pos, prev_stop, decision, event, state, profile)
            elif event is not None:
                self.storage.record_management_event(event)

            if decision.action in (ManagementAction.EXIT, ManagementAction.EMERGENCY_EXIT):
                await self._close_position(
                    pos,
                    candle.close,
                    decision.exit_reason or ExitReason.MOMENTUM_DETERIORATION,
                    state,
                    profile,
                )

        # Evaluate new entry signal
        active = next((p for p in self.open_positions if p.symbol == candle.symbol), None)
        signal = self.signal_engine.evaluate(state, profile, active_position=active)
        if signal.signal_type != SignalType.NO_ACTION:
            self.storage.record_signal(signal, executed=False)

        if active is None and signal.signal_type in (SignalType.NEW_OPPORTUNITY, SignalType.CONTINUATION):
            risk_state = self._current_risk_state()
            receipt = await self.execution.submit_signal(
                signal, profile, self.open_positions, risk_state, state
            )
            if receipt.accepted and receipt.position is not None:
                self.open_positions.append(receipt.position)
                self.storage.record_signal(signal, executed=True)
                self.latest_action = "OPEN_POSITION"
                self.latest_reason = receipt.reason
                await self._publish_position_opened(receipt.position, signal, state, profile)
            elif (
                self.settings.notifications.send_unentered_analysis_signals
                and (
                    "max_open_positions" in receipt.reason
                    or "max_group_exposure" in receipt.reason
                    or "max_concurrent_risk" in receipt.reason
                )
            ):
                await self._publish_analysis_signal(signal, state, profile, receipt.reason)

        if self.show_dashboard:
            self.dashboard.print_snapshot(self._snapshot_dashboard())

    async def _publish_position_opened(
        self,
        pos: Position,
        signal: TradeSignal,
        state: MarketStateSnapshot,
        profile: SymbolProfile,
    ) -> None:
        history = self.storage.load_closed_trades(limit=500)
        bundle = build_trade_opened_bundle(
            position=pos,
            signal=signal,
            market_state=state,
            symbol_cfg=profile,
            management_cfg=self.settings.management,
            risk_pct=self.settings.risk.risk_per_trade,
            symbol_history_trades=history,
        )
        target_channel = profile.slack_channel or self.settings.notifications.slack_channel
        slack_res, reddit_res = await self.notifier.notify_trade_opened(
            trade_id=pos.trade_id,
            channel=target_channel,
            slack_text=bundle.slack_text,
            slack_blocks=bundle.slack_blocks,
            reddit_title=bundle.title,
            reddit_body=bundle.reddit_markdown,
            reddit_flair=profile.reddit_flair,
        )
        if slack_res.channel:
            pos.metadata["slack_channel"] = slack_res.channel
        if slack_res.ts:
            pos.metadata["slack_thread_ts"] = slack_res.ts
        if reddit_res.post_id:
            pos.metadata["reddit_post_id"] = reddit_res.post_id
        self.storage.upsert_position(pos)

    def _classify_stop_exit_reason(self, pos: Position) -> ExitReason:
        """Classify a stop hit according to the current protection stage reached."""
        if pos.stage == TradeStage.TRAILING:
            return ExitReason.TRAILING_STOP
        locked_r = pos.r_for_price(pos.current_stop)
        if pos.stage == TradeStage.PROFIT_PROTECTION or locked_r >= self.settings.management.profit_lock_floor_r * 0.99:
            return ExitReason.PROFIT_LOCK_STOP
        if locked_r >= 0.0:
            return ExitReason.BREAKEVEN_STOP
        return ExitReason.STOP_LOSS

    async def _maybe_notify_management_event(
        self,
        pos: Position,
        prev_stop: float,
        decision: ManagementDecision,
        event: ManagementEvent,
        state: MarketStateSnapshot,
        profile: SymbolProfile,
    ) -> None:
        important_actions = {
            ManagementAction.MOVE_TO_BREAKEVEN,
            ManagementAction.LOCK_PROFIT,
            ManagementAction.TRAIL,
            ManagementAction.TIGHTEN_STOP,
            ManagementAction.REDUCE,
        }
        stop_delta_r = abs(pos.current_stop - prev_stop) / max(pos.initial_risk_distance, 1e-9)
        should_notify = decision.action in important_actions and (
            decision.action not in (ManagementAction.TRAIL, ManagementAction.TIGHTEN_STOP)
            or stop_delta_r >= self.settings.notifications.min_stop_change_r_for_alert
        )
        if not should_notify:
            return
        comment_bundle = build_management_comment_bundle(
            pos,
            event,
            state,
            profile,
            prev_stop,
            management_cfg=self.settings.management,
        )
        await self.notifier.notify_trade_comment(
            dedupe_key=f"mgmt:{pos.trade_id}:{decision.action.value}:{round(pos.current_stop, 6)}",
            trade_id=pos.trade_id,
            priority=AlertPriority.WARNING if decision.action == ManagementAction.REDUCE else AlertPriority.NOTICE,
            channel=pos.metadata.get("slack_channel") or profile.slack_channel,
            thread_ts=pos.metadata.get("slack_thread_ts"),
            slack_text=comment_bundle.slack_text,
            slack_blocks=comment_bundle.slack_blocks,
            reddit_post_id=pos.metadata.get("reddit_post_id"),
            reddit_comment_body=comment_bundle.reddit_markdown,
            label=decision.action.value.lower(),
        )

    async def _monitor_open_position_intrabar(
        self,
        pos: Position,
        bar: Candle,
        profile: SymbolProfile,
    ) -> None:
        """Continuously monitor open positions intra-bar for stop hits and protective upgrades."""
        if pos not in self.open_positions:
            return
        pos.update_mark(bar.close, bar.high, bar.low, bar.timestamp, is_new_bar=False)
        state = self.market_states.get(pos.symbol)
        if state is None:
            self.storage.upsert_position(pos)
            return

        stop_mgr = self.trade_manager.stop_manager
        if stop_mgr.check_stop_hit(pos, bar.high, bar.low):
            await self._close_position(
                pos,
                pos.current_stop,
                self._classify_stop_exit_reason(pos),
                state,
                profile,
            )
            return

        cur_r = pos.unrealized_r()
        mgmt = self.settings.management
        prev_stop = pos.current_stop
        action: ManagementAction | None = None
        target_stage = pos.stage
        reason = ""
        cand = prev_stop

        if cur_r >= mgmt.strong_profit_trigger_r or pos.stage == TradeStage.TRAILING:
            cand = stop_mgr.compute_dynamic_trailing_stop(pos, state, profile)
            action = ManagementAction.TRAIL
            target_stage = TradeStage.TRAILING
            reason = "intrabar_dynamic_trailing"
        elif cur_r >= mgmt.profit_lock_trigger_r:
            cand = stop_mgr.compute_profit_lock_stop(pos, mgmt.profit_lock_floor_r)
            action = ManagementAction.LOCK_PROFIT
            target_stage = TradeStage.PROFIT_PROTECTION
            reason = "intrabar_profit_lock"
        elif cur_r >= mgmt.breakeven_trigger_r and pos.r_for_price(prev_stop) < mgmt.breakeven_buffer_r:
            cand = stop_mgr.compute_breakeven_stop(pos)
            action = ManagementAction.MOVE_TO_BREAKEVEN
            target_stage = TradeStage.PROFITABLE
            reason = "intrabar_breakeven_protection"

        if action is not None and stop_mgr.is_better_stop(pos.direction, cand, prev_stop):
            decision = ManagementDecision(
                action=action,
                reason=reason,
                confidence=0.85,
                risk_state=self._current_risk_state().level.value,
                target_stage=target_stage,
                new_stop_price=cand,
                timestamp=bar.timestamp,
            )
            event = self.trade_manager.apply_decision(pos, decision)
            self.storage.upsert_position(pos)
            if event is not None:
                self.storage.record_management_event(event)
                await self._maybe_notify_management_event(pos, prev_stop, decision, event, state, profile)
            return

        self.storage.upsert_position(pos)

    async def _close_position(self, pos: Position, exit_price: float, reason: ExitReason, state: MarketStateSnapshot, profile) -> None:
        if self.settings.mode == OperatingMode.LIVE_TRADING and self.execution.live_broker is not None:
            fill_exit = await self.execution.live_broker.execute_exit(pos)
        else:
            fill_exit = self.execution.paper_broker.execute_exit(pos, profile, exit_price)

        closed = self.trade_manager.build_closed_trade(pos, fill_exit, reason, state)
        self.storage.save_closed_trade(closed, idempotency_key=pos.idempotency_key)
        if pos in self.open_positions:
            self.open_positions.remove(pos)

        self.daily_pnl += closed.pnl
        self.account.balance += closed.pnl
        self.consecutive_losses = 0 if closed.pnl >= 0 else self.consecutive_losses + 1
        self.latest_action = f"CLOSED:{reason.value}"
        self.latest_reason = closed.attribution_summary

        close_bundle = build_trade_closed_comment_bundle(closed, state, profile)
        await self.notifier.notify_trade_comment(
            dedupe_key=f"close:{closed.trade_id}",
            trade_id=closed.trade_id,
            priority=AlertPriority.NOTICE,
            channel=pos.metadata.get("slack_channel") or profile.slack_channel,
            thread_ts=pos.metadata.get("slack_thread_ts"),
            slack_text=close_bundle.slack_text,
            slack_blocks=close_bundle.slack_blocks,
            reddit_post_id=pos.metadata.get("reddit_post_id"),
            reddit_comment_body=close_bundle.reddit_markdown,
            label="closed",
            reply_broadcast=True,
        )

        risk_state = self._current_risk_state()
        if not risk_state.entries_allowed:
            self.storage.record_risk_event(risk_state, "circuit_breaker", ",".join(risk_state.reasons))
            await self.notifier.notify(
                dedupe_key=f"risk:{risk_state.level.value}",
                priority=AlertPriority.CRITICAL,
                slack_text=SlackNotifier.format_risk_alert(risk_state, self.settings.risk.max_daily_loss),
            )

    def _current_risk_state(self) -> RiskState:
        return self.risk_manager.evaluate_risk_state(
            self.account.balance,
            self.open_positions,
            self.daily_pnl,
            self.consecutive_losses,
        )

    def _snapshot_dashboard(self) -> DashboardState:
        rs = self._current_risk_state()
        daily_r = self.daily_pnl / max(self.account.balance * self.settings.risk.risk_per_trade, 1.0)
        return DashboardState(
            api_connected=self.client.is_connected,
            engine_status="RUNNING",
            mode=self.settings.mode.value,
            market_states=self.market_states,
            open_positions=self.open_positions,
            risk_state=rs,
            latest_action=self.latest_action,
            latest_reason=self.latest_reason,
            daily_pnl_r=daily_r,
        )

    async def _on_ws_disconnect(self, reason: str) -> None:
        self.storage.record_system_event(AlertPriority.WARNING, "deriv_ws", f"Disconnected: {reason}")
        await self.bus.publish(EngineEvent("deriv.disconnected", AlertPriority.WARNING, {"reason": reason}))

    async def _on_ws_reconnect(self) -> None:
        self.storage.record_system_event(AlertPriority.NOTICE, "deriv_ws", "Reconnected successfully")
        await self.bus.publish(EngineEvent("deriv.reconnected", AlertPriority.NOTICE, {}))

    async def _on_malformed_msg(self, detail: str) -> None:
        self.storage.record_system_event(AlertPriority.WARNING, "deriv_ws", detail)

    def request_stop(self) -> None:
        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(os_signal, name, None)
            if sig is not None:
                try:
                    os_signal.signal(sig, lambda _s, _f: self.request_stop())
                except (OSError, ValueError):
                    pass

    async def _publish_analysis_signal(
        self,
        signal: TradeSignal,
        state: MarketStateSnapshot,
        profile: SymbolProfile,
        cap_reason: str,
    ) -> None:
        """Broadcast rich analysis signal card when portfolio/group exposure cap is already full."""
        assert signal.direction is not None
        preview_pos = Position(
            trade_id=f"SIG-{signal.signal_id[:8].upper()}",
            idempotency_key=f"sig:{signal.signal_id}",
            symbol=signal.symbol,
            direction=signal.direction,
            stage=TradeStage.PENDING,
            entry_price=signal.entry_price,
            current_price=signal.entry_price,
            initial_stop=signal.suggested_stop,
            current_stop=signal.suggested_stop,
            take_profit=None,
            stake=0.0,
            remaining_fraction=1.0,
            multiplier=profile.multiplier,
            initial_risk_amount=0.0,
            initial_risk_distance=signal.initial_risk_distance,
            opened_at=signal.timestamp,
            last_updated_at=signal.timestamp,
            policy=self.settings.management.policy,
        )
        history = self.storage.load_closed_trades(limit=500)
        bundle = build_trade_opened_bundle(
            position=preview_pos,
            signal=signal,
            market_state=state,
            symbol_cfg=profile,
            management_cfg=self.settings.management,
            risk_pct=self.settings.risk.risk_per_trade,
            symbol_history_trades=history,
        )
        target_channel = profile.slack_channel or self.settings.notifications.slack_channel
        slack_text = f"👀 *ANALYSIS SIGNAL ({cap_reason})*\n{bundle.slack_text}"
        await self.notifier.notify_trade_opened(
            trade_id=preview_pos.trade_id,
            channel=target_channel,
            slack_text=slack_text,
            slack_blocks=bundle.slack_blocks,
            reddit_title=f"[SIGNAL WATCH] {bundle.title}",
            reddit_body=f"> **Exposure Note:** Portfolio/group cap active (`{cap_reason}`) — published for analysis only.\n\n{bundle.reddit_markdown}",
            reddit_flair=profile.reddit_flair,
        )

    async def _poll_sweep_loop(self) -> None:
        """Periodically poll all active Deriv symbols for newly closed candles without repainting."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.deriv.poll_interval_sec,
                )
                break
            except TimeoutError:
                pass

            if not self.client.is_connected:
                continue

            now_mono = time.monotonic()
            if (
                self.settings.deriv.discover_all_symbols
                and (now_mono - self._last_symbol_refresh) >= self.settings.deriv.symbol_refresh_sec
            ):
                try:
                    refreshed = await discover_active_symbols(self.client, overrides=self.settings.symbols)
                    if refreshed:
                        self.settings.symbols = refreshed
                        self._last_symbol_refresh = now_mono
                        for prof in refreshed.values():
                            if prof.enabled:
                                self._ensure_analyzer(prof)
                except Exception as exc:
                    logger.debug("Periodic symbol refresh skipped: %s", exc)

            now_epoch = int(datetime.now(UTC).timestamp())
            open_syms = {p.symbol for p in self.open_positions}
            profiles = sorted(
                self.settings.enabled_symbols(),
                key=lambda p: (0 if p.symbol in open_syms else 1, 0 if p.exchange_is_open else 1, p.symbol),
            )

            for profile in profiles:
                if self._stop_event.is_set():
                    break
                if not profile.exchange_is_open and profile.symbol not in open_syms:
                    continue
                tf = profile.primary_timeframe
                last_ep = self.market_data.last_closed_epoch(profile.symbol, tf)
                ran_closed = False
                if last_ep is None or (now_epoch // tf) > (last_ep // tf):
                    try:
                        new_candles = await self.market_data.fetch_new_closed_candles(
                            profile.symbol, tf, count=5
                        )
                        for c in new_candles:
                            await self.on_closed_candle(c)
                        ran_closed = bool(new_candles)
                    except Exception as exc:
                        logger.debug("Poll error for %s (%ds): %s", profile.symbol, tf, exc)
                if profile.symbol in open_syms and not ran_closed:
                    try:
                        bar = await self.market_data.fetch_latest_price_bar(profile.symbol, tf)
                        if bar is not None:
                            for pos in list(self.open_positions):
                                if pos.symbol == profile.symbol:
                                    await self._monitor_open_position_intrabar(pos, bar, profile)
                    except Exception as exc:
                        logger.debug("Intra-bar monitor error for %s (%ds): %s", profile.symbol, tf, exc)

    async def shutdown(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for pos in self.open_positions:
            self.storage.upsert_position(pos)
        await self.client.close()
        self.storage.close()
        logger.info("Engine shut down cleanly")
