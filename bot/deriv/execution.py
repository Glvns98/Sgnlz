"""Execution Safety Engine: 7-stage pre-trade pipeline, idempotency, PaperBroker, and double-locked LiveBroker."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass

from bot.config.settings import EngineSettings, SymbolProfile
from bot.core.enums import ManagementPolicy, OperatingMode, TradeDirection, TradeStage
from bot.core.models import MarketStateSnapshot, Position, RiskState, TradeSignal
from bot.deriv.client import DerivAPIClient
from bot.risk.risk_manager import RiskManager
from bot.storage.database import StorageRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionReceipt:
    accepted: bool
    trade_id: str | None
    contract_id: str | None
    fill_price: float | None
    reason: str
    position: Position | None = None


def build_idempotency_key(signal: TradeSignal) -> str:
    raw = (
        f"{signal.symbol}:{signal.direction.value if signal.direction else 'NONE'}:"
        f"{signal.timeframe_sec}:{signal.timestamp.isoformat()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class PaperBroker:
    """Realistic simulated Multiplier broker with spread/slippage modeling."""

    def execute_entry(
        self,
        signal: TradeSignal,
        symbol_cfg: SymbolProfile,
        stake: float,
    ) -> tuple[str, float]:
        assert signal.direction is not None
        cost_bps = (symbol_cfg.spread_bps + symbol_cfg.slippage_bps) / 10000.0
        if signal.direction == TradeDirection.BUY:
            fill = signal.entry_price * (1.0 + cost_bps)
        else:
            fill = signal.entry_price * (1.0 - cost_bps)
        contract_id = f"PAPER-{uuid.uuid4().hex[:10].upper()}"
        return contract_id, fill

    def execute_exit(
        self,
        position: Position,
        symbol_cfg: SymbolProfile,
        exit_price: float,
    ) -> float:
        cost_bps = (symbol_cfg.spread_bps + symbol_cfg.slippage_bps) / 10000.0
        if position.direction == TradeDirection.BUY:
            return exit_price * (1.0 - cost_bps)
        return exit_price * (1.0 + cost_bps)


class DerivLiveBroker:
    """Live Deriv Multiplier execution locked behind double explicit opt-in."""

    def __init__(self, client: DerivAPIClient) -> None:
        self.client = client

    async def execute_entry(
        self,
        signal: TradeSignal,
        symbol_cfg: SymbolProfile,
        stake: float,
    ) -> tuple[str, float]:
        if os.environ.get("DERIV_ALLOW_LIVE_EXECUTION", "").lower() != "true":
            raise PermissionError(
                "LIVE_TRADING blocked: set DERIV_ALLOW_LIVE_EXECUTION=true explicitly in environment."
            )
        if not self.client.is_authorized:
            raise PermissionError("Deriv client is not authorized for live execution")
        assert signal.direction is not None
        contract_type = "MULTUP" if signal.direction == TradeDirection.BUY else "MULTDOWN"
        proposal = await self.client.request(
            {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "multiplier": int(symbol_cfg.multiplier),
                "symbol": signal.symbol,
            }
        )
        prop_obj = proposal.get("proposal") or {}
        prop_id = prop_obj.get("id")
        if not prop_id:
            raise RuntimeError("Deriv proposal response missing proposal.id")

        buy_resp = await self.client.request({"buy": prop_id, "price": stake})
        buy_obj = buy_resp.get("buy") or {}
        contract_id = str(buy_obj["contract_id"])
        fill_price = float(buy_obj.get("spot", signal.entry_price))
        return contract_id, fill_price

    async def execute_exit(self, position: Position) -> float:
        if not position.contract_id or not self.client.is_authorized:
            return position.current_price
        resp = await self.client.request({"sell": int(position.contract_id), "price": 0})
        sell_obj = resp.get("sell") or {}
        return float(sell_obj.get("sold_for", position.current_price))


class ExecutionEngine:
    """Strict 7-stage safety pipeline preventing duplicate, unconfirmed, or unauthorized orders."""

    def __init__(
        self,
        settings: EngineSettings,
        risk_manager: RiskManager,
        storage: StorageRepository,
        client: DerivAPIClient | None = None,
    ) -> None:
        self.settings = settings
        self.risk_manager = risk_manager
        self.storage = storage
        self.paper_broker = PaperBroker()
        self.live_broker = DerivLiveBroker(client) if client is not None else None
        self._seen_keys: set[str] = set()

    async def submit_signal(
        self,
        signal: TradeSignal,
        symbol_cfg: SymbolProfile,
        open_positions: list[Position],
        risk_state: RiskState,
        market_state: MarketStateSnapshot,
        *,
        policy: ManagementPolicy | None = None,
    ) -> ExecutionReceipt:
        """Run: Signal -> Risk -> Exposure -> Duplicate -> Validation -> Execution -> Confirmation -> Register."""
        if self.settings.mode == OperatingMode.ANALYSIS_ONLY:
            return ExecutionReceipt(False, None, None, None, "analysis_only_mode_no_execution")

        if signal.direction is None or signal.initial_risk_distance <= 0.0:
            return ExecutionReceipt(False, None, None, None, "invalid_signal_contract_params")

        # Stage 1 & 2: Risk & Exposure check
        approval = self.risk_manager.check_entry_allowed(
            signal,
            symbol_cfg,
            open_positions,
            risk_state,
            market_state,
        )
        if not approval.allowed:
            return ExecutionReceipt(False, None, None, None, ",".join(approval.reasons))

        # Stage 3: Duplicate / Idempotency check (in-memory + SQLite)
        idem_key = build_idempotency_key(signal)
        if idem_key in self._seen_keys or self.storage.has_idempotency_key(idem_key):
            return ExecutionReceipt(False, None, None, None, "duplicate_idempotency_key_blocked")

        # Stage 4: Contract validation
        if signal.direction == TradeDirection.BUY and signal.suggested_stop >= signal.entry_price:
            return ExecutionReceipt(False, None, None, None, "invalid_buy_stop_geometry")
        if signal.direction == TradeDirection.SELL and signal.suggested_stop <= signal.entry_price:
            return ExecutionReceipt(False, None, None, None, "invalid_sell_stop_geometry")

        # Stage 5 & 6: Broker execution + Confirmation
        try:
            if self.settings.mode == OperatingMode.LIVE_TRADING:
                if self.live_broker is None:
                    return ExecutionReceipt(False, None, None, None, "live_broker_not_initialized")
                contract_id, fill_price = await self.live_broker.execute_entry(
                    signal, symbol_cfg, approval.stake
                )
            else:
                contract_id, fill_price = self.paper_broker.execute_entry(
                    signal, symbol_cfg, approval.stake
                )
        except Exception as exc:
            logger.error("Execution rejected/failed for %s: %s", signal.symbol, exc)
            return ExecutionReceipt(False, None, None, None, f"execution_failed:{exc}")

        # Stage 7: Position Registration
        self._seen_keys.add(idem_key)
        trade_id = f"TRD-{uuid.uuid4().hex[:8].upper()}"
        stop_dist = abs(fill_price - signal.suggested_stop)
        position = Position(
            trade_id=trade_id,
            idempotency_key=idem_key,
            symbol=signal.symbol,
            direction=signal.direction,
            stage=TradeStage.ENTERED,
            entry_price=fill_price,
            current_price=fill_price,
            initial_stop=signal.suggested_stop,
            current_stop=signal.suggested_stop,
            take_profit=None,
            stake=approval.stake,
            remaining_fraction=1.0,
            multiplier=symbol_cfg.multiplier,
            initial_risk_amount=approval.risk_amount,
            initial_risk_distance=max(stop_dist, signal.initial_risk_distance),
            opened_at=signal.timestamp,
            last_updated_at=signal.timestamp,
            squeeze_state_at_entry=signal.squeeze_state.value,
            momentum_state_at_entry=signal.momentum_state.value,
            momentum_at_entry=market_state.squeeze.val,
            contract_id=contract_id,
            policy=policy or self.settings.management.policy,
            metadata={
                "signal_id": signal.signal_id,
                "confidence": signal.confidence,
                "submarket": symbol_cfg.submarket,
                "market": symbol_cfg.market,
            },
        )
        self.storage.upsert_position(position)
        return ExecutionReceipt(
            accepted=True,
            trade_id=trade_id,
            contract_id=contract_id,
            fill_price=fill_price,
            reason="confirmed_and_registered",
            position=position,
        )
