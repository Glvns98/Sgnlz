"""Account Monitor: balance tracking, open contract monitoring, and restart reconciliation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bot.core.models import Position
from bot.deriv.client import DerivAPIClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationReport:
    reconciled_ids: tuple[str, ...]
    missing_on_broker_ids: tuple[str, ...]
    untracked_broker_contract_ids: tuple[str, ...]


class AccountMonitor:
    """Tracks Deriv balance, open contracts, and reconciles persisted positions on restart."""

    def __init__(self, client: DerivAPIClient | None = None, initial_balance: float = 10000.0) -> None:
        self.client = client
        self.balance: float = initial_balance
        self.currency: str = "USD"
        self.open_contracts: dict[str, dict[str, Any]] = {}

    async def refresh_balance(self) -> float:
        if self.client is None or not self.client.is_authorized:
            return self.balance
        if self.client.rest_account is not None and "balance" in self.client.rest_account:
            self.balance = float(self.client.rest_account["balance"])
            self.currency = str(self.client.rest_account.get("currency", self.currency))
            return self.balance
        if self.client.is_v1_options_api:
            return self.balance
        resp = await self.client.request({"balance": 1})
        bal = resp.get("balance", {})
        if "balance" in bal:
            self.balance = float(bal["balance"])
            self.currency = str(bal.get("currency", self.currency))
        return self.balance

    async def refresh_portfolio(self) -> dict[str, dict[str, Any]]:
        if self.client is None or not self.client.is_authorized or self.client.is_v1_options_api:
            return dict(self.open_contracts)
        resp = await self.client.request({"portfolio": 1})
        contracts = resp.get("portfolio", {}).get("contracts", [])
        self.open_contracts = {str(c["contract_id"]): c for c in contracts if "contract_id" in c}
        return dict(self.open_contracts)

    async def subscribe_balance(self) -> None:
        if self.client is None or not self.client.is_authorized or self.client.is_v1_options_api:
            return

        def _on_bal(msg: dict[str, Any]) -> None:
            bal = msg.get("balance", {})
            if "balance" in bal:
                self.balance = float(bal["balance"])

        await self.client.subscribe({"balance": 1}, _on_bal)

    def reconcile_positions(self, persisted_positions: list[Position]) -> ReconciliationReport:
        """Compare SQLite-restored positions against live Deriv open contracts."""
        broker_ids = set(self.open_contracts.keys())
        reconciled: list[str] = []
        missing: list[str] = []
        tracked_contract_ids: set[str] = set()

        for pos in persisted_positions:
            if pos.contract_id is None:
                # Paper / local managed positions have no live broker contract_id
                reconciled.append(pos.trade_id)
                continue
            tracked_contract_ids.add(pos.contract_id)
            if pos.contract_id in broker_ids:
                reconciled.append(pos.trade_id)
            else:
                missing.append(pos.trade_id)

        untracked = sorted(broker_ids - tracked_contract_ids)
        return ReconciliationReport(
            reconciled_ids=tuple(reconciled),
            missing_on_broker_ids=tuple(missing),
            untracked_broker_contract_ids=tuple(untracked),
        )
