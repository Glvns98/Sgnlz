"""Zero-Look-Ahead Historical Replay Engine & Synthetic Multi-Regime Market Generator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bot.config.settings import EngineSettings
from bot.core.enums import (
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    SignalType,
    TradeStage,
)
from bot.core.models import Candle, ClosedTrade, MarketStateSnapshot, Position, TradeSignal
from bot.deriv.execution import PaperBroker
from bot.management.trade_manager import TradeManager
from bot.risk.risk_manager import RiskManager
from bot.strategy.market_state import MultiTimeframeAnalyzer
from bot.strategy.signals import SignalEngine


@dataclass(frozen=True)
class ReplayResult:
    policy: ManagementPolicy
    trades: list[ClosedTrade]
    signals: list[TradeSignal]
    ending_balance: float
    equity_curve: list[float]
    market_states_count: int


def generate_synthetic_deriv_candles(
    symbol: str,
    bars: int = 1200,
    timeframe_sec: int = 900,
    seed: int = 42,
    start_epoch: int = 1767225600,
    base_price: float = 1000.0,
) -> list[Candle]:
    """Generate deterministic multi-regime OHLC bars (compression -> breakout -> exhaustion -> chop)."""
    rng = np.random.default_rng(seed)
    candles: list[Candle] = []
    price = base_price

    # Cycle through realistic volatility regimes
    idx = 0
    while idx < bars:
        # 1. Compression (squeeze build)
        comp_len = min(bars - idx, int(rng.integers(24, 36)))
        for _ in range(comp_len):
            ret = rng.normal(0.0, 0.00055)
            open_p = price
            close_p = open_p * (1.0 + ret)
            wick = abs(rng.normal(0.0004, 0.00015)) * open_p
            high_p = max(open_p, close_p) + wick
            low_p = min(open_p, close_p) - wick
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe_sec=timeframe_sec,
                    epoch=start_epoch + idx * timeframe_sec,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                )
            )
            price = close_p
            idx += 1

        if idx >= bars:
            break

        # 2. Directional expansion trend (bullish or bearish)
        direction = 1.0 if rng.random() > 0.45 else -1.0
        exp_len = min(bars - idx, int(rng.integers(18, 32)))
        for k in range(exp_len):
            decay = 1.0 - (k / max(exp_len * 1.4, 1.0))
            drift = direction * 0.0022 * decay
            ret = drift + rng.normal(0.0, 0.0014)
            open_p = price
            close_p = open_p * (1.0 + ret)
            wick = abs(rng.normal(0.0009, 0.0003)) * open_p
            high_p = max(open_p, close_p) + wick
            low_p = min(open_p, close_p) - wick
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe_sec=timeframe_sec,
                    epoch=start_epoch + idx * timeframe_sec,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                )
            )
            price = close_p
            idx += 1

        if idx >= bars:
            break

        # 3. Chop / pullback with occasional volatility spike
        chop_len = min(bars - idx, int(rng.integers(14, 24)))
        for k in range(chop_len):
            spike = 2.8 if (k == chop_len // 2 and rng.random() < 0.20) else 1.0
            ret = rng.normal(0.0, 0.0013 * spike)
            open_p = price
            close_p = open_p * (1.0 + ret)
            wick = abs(rng.normal(0.0010 * spike, 0.0003)) * open_p
            high_p = max(open_p, close_p) + wick
            low_p = min(open_p, close_p) - wick
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe_sec=timeframe_sec,
                    epoch=start_epoch + idx * timeframe_sec,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                )
            )
            price = close_p
            idx += 1

    return candles


def load_candles_from_csv(path: str | Path, symbol: str, timeframe_sec: int = 900) -> list[Candle]:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    candles: list[Candle] = []
    for idx, row in df.iterrows():
        if "epoch" in cols:
            epoch = int(row[cols["epoch"]])
        elif "timestamp" in cols:
            epoch = int(pd.Timestamp(row[cols["timestamp"]]).timestamp())
        else:
            epoch = 1767225600 + int(idx) * timeframe_sec
        open_p = float(row[cols.get("open", cols["close"])])
        high_p = float(row[cols["high"]])
        low_p = float(row[cols["low"]])
        close_p = float(row[cols["close"]])
        candles.append(
            Candle(
                symbol=symbol,
                timeframe_sec=timeframe_sec,
                epoch=epoch,
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
            )
        )
    return candles


class HistoricalReplayEngine:
    """Strict causal bar-by-bar replay reusing production indicator, signal, risk, and management logic."""

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings

    def replay(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        *,
        policy: ManagementPolicy = ManagementPolicy.DYNAMIC_MANAGEMENT,
        close_open_at_end: bool = True,
    ) -> ReplayResult:
        """Replay interleaved symbol bars strictly chronologically with zero look-ahead."""
        risk_manager = RiskManager(self.settings.risk)
        trade_manager = TradeManager(self.settings.management)
        signal_engine = SignalEngine()
        broker = PaperBroker()

        analyzers: dict[str, MultiTimeframeAnalyzer] = {}
        htf_accum: dict[str, dict[str, Any]] = {}
        for sym, profile in self.settings.symbols.items():
            if sym in candles_by_symbol:
                analyzers[sym] = MultiTimeframeAnalyzer(
                    symbol=sym,
                    primary_tf=profile.primary_timeframe,
                    higher_tf=profile.higher_timeframe,
                    lower_tf=profile.lower_timeframe,
                    squeeze_config=self._squeeze_cfg_for(sym),
                )
                htf_accum[sym] = {}

        # Merge all symbol candles into strict chronological sequence (no future leakage)
        timeline: list[Candle] = []
        for sym, seq in candles_by_symbol.items():
            if sym in analyzers:
                timeline.extend(seq)
        timeline.sort(key=lambda c: (c.epoch, c.symbol))

        balance = self.settings.risk.account_balance
        daily_pnl = 0.0
        current_day: str | None = None
        consecutive_losses = 0
        open_positions: list[Position] = []
        closed_trades: list[ClosedTrade] = []
        emitted_signals: list[TradeSignal] = []
        equity_curve: list[float] = [balance]
        latest_states: dict[str, MarketStateSnapshot] = {}
        trade_counter = 0

        for candle in timeline:
            sym = candle.symbol
            profile = self.settings.symbols[sym]
            analyzer = analyzers[sym]

            day_key = candle.timestamp.strftime("%Y-%m-%d")
            if current_day != day_key:
                current_day = day_key
                daily_pnl = 0.0

            # Causal HTF aggregation: emit HTF bar ONLY once the full HTF bucket closes
            if profile.higher_timeframe and profile.higher_timeframe > candle.timeframe_sec:
                htf_candle = self._step_htf_bucket(htf_accum[sym], candle, profile.higher_timeframe)
                if htf_candle is not None:
                    analyzer.update(htf_candle)

            state = analyzer.update(candle)
            if state is None:
                continue
            latest_states[sym] = state

            # 1. Manage existing open positions on this symbol first
            sym_open = [p for p in open_positions if p.symbol == sym]
            for pos in list(sym_open):
                pos.update_mark(candle.close, candle.high, candle.low, candle.timestamp, is_new_bar=True)

                # Check intra-bar stop hit using conservative adverse high/low
                if trade_manager.stop_manager.check_stop_hit(pos, candle.high, candle.low):
                    exit_px = broker.execute_exit(pos, profile, pos.current_stop)
                    stop_reason = self._classify_stop_exit_reason(pos)
                    closed = trade_manager.build_closed_trade(pos, exit_px, stop_reason, state)
                    closed_trades.append(closed)
                    open_positions.remove(pos)
                    balance += closed.pnl
                    daily_pnl += closed.pnl
                    consecutive_losses = 0 if closed.pnl >= 0 else consecutive_losses + 1
                    equity_curve.append(balance)
                    continue

                risk_state = risk_manager.evaluate_risk_state(balance, open_positions, daily_pnl, consecutive_losses)
                decision = trade_manager.evaluate(pos, state, symbol_cfg=profile, risk_level=risk_state.level.value)
                trade_manager.apply_decision(pos, decision)

                if decision.action in (ManagementAction.EXIT, ManagementAction.EMERGENCY_EXIT):
                    exit_px = broker.execute_exit(pos, profile, candle.close)
                    closed = trade_manager.build_closed_trade(
                        pos,
                        exit_px,
                        decision.exit_reason or ExitReason.MOMENTUM_DETERIORATION,
                        state,
                    )
                    closed_trades.append(closed)
                    open_positions.remove(pos)
                    balance += closed.pnl
                    daily_pnl += closed.pnl
                    consecutive_losses = 0 if closed.pnl >= 0 else consecutive_losses + 1
                    equity_curve.append(balance)

            # 2. Evaluate new opportunity signal
            active_for_sym = next((p for p in open_positions if p.symbol == sym), None)
            signal = signal_engine.evaluate(state, profile, active_position=active_for_sym)
            if signal.signal_type != SignalType.NO_ACTION:
                emitted_signals.append(signal)

            if active_for_sym is None and signal.signal_type in (SignalType.NEW_OPPORTUNITY, SignalType.CONTINUATION):
                risk_state = risk_manager.evaluate_risk_state(balance, open_positions, daily_pnl, consecutive_losses)
                approval = risk_manager.check_entry_allowed(signal, profile, open_positions, risk_state, state)
                if approval.allowed and signal.direction is not None:
                    trade_counter += 1
                    contract_id, fill_price = broker.execute_entry(signal, profile, approval.stake)
                    pos = Position(
                        trade_id=f"RPL-{trade_counter:05d}",
                        idempotency_key=f"{sym}:{signal.timestamp.isoformat()}",
                        symbol=sym,
                        direction=signal.direction,
                        stage=TradeStage.ENTERED,
                        entry_price=fill_price,
                        current_price=fill_price,
                        initial_stop=signal.suggested_stop,
                        current_stop=signal.suggested_stop,
                        take_profit=None,
                        stake=approval.stake,
                        remaining_fraction=1.0,
                        multiplier=profile.multiplier,
                        initial_risk_amount=approval.risk_amount,
                        initial_risk_distance=max(abs(fill_price - signal.suggested_stop), signal.initial_risk_distance),
                        opened_at=signal.timestamp,
                        last_updated_at=signal.timestamp,
                        squeeze_state_at_entry=signal.squeeze_state.value,
                        momentum_state_at_entry=signal.momentum_state.value,
                        momentum_at_entry=state.squeeze.val,
                        contract_id=contract_id,
                        policy=policy,
                    )
                    open_positions.append(pos)

        if close_open_at_end:
            for pos in list(open_positions):
                state = latest_states[pos.symbol]
                profile = self.settings.symbols[pos.symbol]
                exit_px = broker.execute_exit(pos, profile, pos.current_price)
                closed = trade_manager.build_closed_trade(pos, exit_px, ExitReason.END_OF_REPLAY, state)
                closed_trades.append(closed)
                balance += closed.pnl
                equity_curve.append(balance)

        return ReplayResult(
            policy=policy,
            trades=closed_trades,
            signals=emitted_signals,
            ending_balance=round(balance, 2),
            equity_curve=equity_curve,
            market_states_count=len(timeline),
        )

    def _squeeze_cfg_for(self, symbol: str):
        from bot.indicators.squeeze import SqueezeConfig

        sq = self.settings.squeeze_for(symbol)
        return SqueezeConfig(
            bb_length=sq.bb_length,
            bb_mult=sq.bb_mult,
            kc_length=sq.kc_length,
            kc_mult=sq.kc_mult,
            use_true_range=sq.use_true_range,
            lazybear_mult_parity=sq.lazybear_mult_parity,
            tr_handle_na=sq.tr_handle_na,
        )

    @staticmethod
    def _step_htf_bucket(bucket_state: dict[str, Any], candle: Candle, htf_sec: int) -> Candle | None:
        """Build HTF bar strictly causally; emit completed HTF bar only when bucket rolls over."""
        bucket_epoch = (candle.epoch // htf_sec) * htf_sec
        cur: Candle | None = bucket_state.get("forming")
        if cur is None:
            bucket_state["forming"] = Candle(
                symbol=candle.symbol,
                timeframe_sec=htf_sec,
                epoch=bucket_epoch,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                is_closed=False,
            )
            return None

        if bucket_epoch > cur.epoch:
            closed = Candle(
                symbol=cur.symbol,
                timeframe_sec=htf_sec,
                epoch=cur.epoch,
                open=cur.open,
                high=cur.high,
                low=cur.low,
                close=cur.close,
                is_closed=True,
            )
            bucket_state["forming"] = Candle(
                symbol=candle.symbol,
                timeframe_sec=htf_sec,
                epoch=bucket_epoch,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                is_closed=False,
            )
            return closed

        bucket_state["forming"] = Candle(
            symbol=cur.symbol,
            timeframe_sec=htf_sec,
            epoch=cur.epoch,
            open=cur.open,
            high=max(cur.high, candle.high),
            low=min(cur.low, candle.low),
            close=candle.close,
            is_closed=False,
        )
        return None

    @staticmethod
    def _classify_stop_exit_reason(pos: Position) -> ExitReason:
        stop_r = pos.r_for_price(pos.current_stop)
        if pos.stage == TradeStage.TRAILING:
            return ExitReason.TRAILING_STOP
        if stop_r >= 0.35:
            return ExitReason.PROFIT_LOCK_STOP
        if stop_r >= -0.01:
            return ExitReason.BREAKEVEN_STOP
        return ExitReason.STOP_LOSS
