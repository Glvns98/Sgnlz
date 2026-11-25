"""SQLite persistence engine (PostgreSQL-ready repository interface) covering all 9 audit tables."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from bot.core.enums import (
    AlertPriority,
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    TradeDirection,
    TradeStage,
)
from bot.core.models import (
    ClosedTrade,
    ManagementDecision,
    ManagementEvent,
    MarketStateSnapshot,
    Position,
    RiskState,
    TradeSignal,
    utc_now,
)


class StorageRepository(Protocol):
    """Repository protocol upgradeable to PostgreSQL."""

    def initialize(self) -> None: ...
    def save_config_version(self, version_hash: str, config_json: str) -> None: ...
    def upsert_position(self, position: Position) -> None: ...
    def delete_open_position(self, trade_id: str) -> None: ...
    def load_open_positions(self) -> list[Position]: ...
    def save_closed_trade(self, trade: ClosedTrade) -> None: ...
    def load_closed_trades(self, limit: int = 1000) -> list[ClosedTrade]: ...
    def record_management_event(self, event: ManagementEvent) -> None: ...
    def record_signal(self, signal: TradeSignal, executed: bool = False) -> None: ...
    def record_market_state(self, state: MarketStateSnapshot) -> None: ...
    def record_risk_event(self, risk_state: RiskState, event_type: str, detail: str) -> None: ...
    def record_system_event(self, level: AlertPriority, component: str, message: str, payload: dict[str, Any] | None = None) -> None: ...
    def save_performance_snapshot(self, label: str, metrics: dict[str, Any]) -> None: ...
    def has_idempotency_key(self, idempotency_key: str) -> bool: ...


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config_versions (
    version_hash TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    trade_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    stage TEXT NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL NOT NULL,
    initial_stop REAL NOT NULL,
    current_stop REAL NOT NULL,
    take_profit REAL,
    stake REAL NOT NULL,
    remaining_fraction REAL NOT NULL,
    multiplier REAL NOT NULL,
    initial_risk_amount REAL NOT NULL,
    initial_risk_distance REAL NOT NULL,
    opened_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    bars_held INTEGER NOT NULL,
    mfe_price REAL NOT NULL,
    mae_price REAL NOT NULL,
    mfe_r REAL NOT NULL,
    mae_r REAL NOT NULL,
    max_unrealized_pnl REAL NOT NULL,
    min_unrealized_pnl REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    squeeze_state_at_entry TEXT NOT NULL,
    momentum_state_at_entry TEXT NOT NULL,
    momentum_at_entry REAL NOT NULL,
    contract_id TEXT,
    policy TEXT NOT NULL,
    decay_bars INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    timestamp TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    initial_risk REAL NOT NULL,
    initial_stop REAL NOT NULL,
    final_stop REAL NOT NULL,
    exit_price REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    bars_held INTEGER NOT NULL,
    pnl REAL NOT NULL,
    r_multiple REAL NOT NULL,
    mfe REAL NOT NULL,
    mae REAL NOT NULL,
    highest_profit REAL NOT NULL,
    lowest_drawdown REAL NOT NULL,
    squeeze_state_at_entry TEXT NOT NULL,
    squeeze_state_at_exit TEXT NOT NULL,
    momentum_at_entry REAL NOT NULL,
    momentum_at_exit REAL NOT NULL,
    policy TEXT NOT NULL,
    management_events_json TEXT NOT NULL,
    management_actions_json TEXT NOT NULL,
    entry_quality TEXT NOT NULL,
    management_quality TEXT NOT NULL,
    attribution_summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS management_events (
    event_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    stage_before TEXT NOT NULL,
    stage_after TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    risk_state TEXT NOT NULL,
    new_stop_price REAL,
    reduce_fraction REAL NOT NULL,
    current_price REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    unrealized_r REAL NOT NULL,
    mfe_r REAL NOT NULL,
    mae_r REAL NOT NULL,
    inputs_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT,
    signal_type TEXT NOT NULL,
    squeeze_state TEXT NOT NULL,
    momentum_state TEXT NOT NULL,
    momentum_strength REAL NOT NULL,
    confidence REAL NOT NULL,
    regime TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    timeframe_sec INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    suggested_stop REAL NOT NULL,
    initial_risk_distance REAL NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe_sec INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    close REAL NOT NULL,
    squeeze_state TEXT NOT NULL,
    momentum_state TEXT NOT NULL,
    regime TEXT NOT NULL,
    val REAL,
    normalized_momentum REAL NOT NULL,
    volatility_ratio REAL NOT NULL,
    transitions_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    balance REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    daily_drawdown_pct REAL NOT NULL,
    consecutive_losses INTEGER NOT NULL,
    open_positions_count INTEGER NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    priority TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    label TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);
"""


class SQLiteStorageEngine:
    """Thread-safe SQLite persistence with WAL mode and explicit connection lifecycle."""

    def __init__(self, db_path: str | Path = "data/squeeze_tme.db") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._mem_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._mem_conn is not None:
            yield self._mem_conn
            self._mem_conn.commit()
            return
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def close(self) -> None:
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    def save_config_version(self, version_hash: str, config_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO config_versions (version_hash, config_json, created_at)
                VALUES (?, ?, ?)
                """,
                (version_hash, config_json, utc_now().isoformat()),
            )

    def upsert_position(self, position: Position) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    trade_id, idempotency_key, symbol, direction, stage,
                    entry_price, current_price, initial_stop, current_stop, take_profit,
                    stake, remaining_fraction, multiplier, initial_risk_amount, initial_risk_distance,
                    opened_at, last_updated_at, bars_held, mfe_price, mae_price,
                    mfe_r, mae_r, max_unrealized_pnl, min_unrealized_pnl, realized_pnl,
                    squeeze_state_at_entry, momentum_state_at_entry, momentum_at_entry,
                    contract_id, policy, decay_bars, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    stage=excluded.stage,
                    current_price=excluded.current_price,
                    current_stop=excluded.current_stop,
                    remaining_fraction=excluded.remaining_fraction,
                    last_updated_at=excluded.last_updated_at,
                    bars_held=excluded.bars_held,
                    mfe_price=excluded.mfe_price,
                    mae_price=excluded.mae_price,
                    mfe_r=excluded.mfe_r,
                    mae_r=excluded.mae_r,
                    max_unrealized_pnl=excluded.max_unrealized_pnl,
                    min_unrealized_pnl=excluded.min_unrealized_pnl,
                    realized_pnl=excluded.realized_pnl,
                    contract_id=excluded.contract_id,
                    decay_bars=excluded.decay_bars,
                    metadata_json=excluded.metadata_json
                """,
                (
                    position.trade_id,
                    position.idempotency_key,
                    position.symbol,
                    position.direction.value,
                    position.stage.value,
                    position.entry_price,
                    position.current_price,
                    position.initial_stop,
                    position.current_stop,
                    position.take_profit,
                    position.stake,
                    position.remaining_fraction,
                    position.multiplier,
                    position.initial_risk_amount,
                    position.initial_risk_distance,
                    position.opened_at.isoformat(),
                    position.last_updated_at.isoformat(),
                    position.bars_held,
                    position.mfe_price,
                    position.mae_price,
                    position.mfe_r,
                    position.mae_r,
                    position.max_unrealized_pnl,
                    position.min_unrealized_pnl,
                    position.realized_pnl,
                    position.squeeze_state_at_entry,
                    position.momentum_state_at_entry,
                    position.momentum_at_entry,
                    position.contract_id,
                    position.policy.value,
                    position.decay_bars,
                    json.dumps(position.metadata, default=str),
                ),
            )

    def delete_open_position(self, trade_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE trade_id = ?", (trade_id,))

    def load_open_positions(self) -> list[Position]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY opened_at ASC").fetchall()
            events_rows = conn.execute("SELECT * FROM management_events ORDER BY timestamp ASC").fetchall()

        events_by_trade: dict[str, list[ManagementEvent]] = {}
        for er in events_rows:
            dec = ManagementDecision(
                action=ManagementAction(er["action"]),
                reason=er["reason"],
                confidence=er["confidence"],
                risk_state=er["risk_state"],
                target_stage=TradeStage(er["stage_after"]),
                new_stop_price=er["new_stop_price"],
                reduce_fraction=er["reduce_fraction"],
                inputs=json.loads(er["inputs_json"]),
                timestamp=datetime.fromisoformat(er["timestamp"]),
            )
            ev = ManagementEvent(
                event_id=er["event_id"],
                trade_id=er["trade_id"],
                timestamp=datetime.fromisoformat(er["timestamp"]),
                stage_before=TradeStage(er["stage_before"]),
                stage_after=TradeStage(er["stage_after"]),
                decision=dec,
                current_price=er["current_price"],
                unrealized_pnl=er["unrealized_pnl"],
                unrealized_r=er["unrealized_r"],
                mfe_r=er["mfe_r"],
                mae_r=er["mae_r"],
            )
            events_by_trade.setdefault(er["trade_id"], []).append(ev)

        result: list[Position] = []
        for r in rows:
            result.append(
                Position(
                    trade_id=r["trade_id"],
                    idempotency_key=r["idempotency_key"],
                    symbol=r["symbol"],
                    direction=TradeDirection(r["direction"]),
                    stage=TradeStage(r["stage"]),
                    entry_price=r["entry_price"],
                    current_price=r["current_price"],
                    initial_stop=r["initial_stop"],
                    current_stop=r["current_stop"],
                    take_profit=r["take_profit"],
                    stake=r["stake"],
                    remaining_fraction=r["remaining_fraction"],
                    multiplier=r["multiplier"],
                    initial_risk_amount=r["initial_risk_amount"],
                    initial_risk_distance=r["initial_risk_distance"],
                    opened_at=datetime.fromisoformat(r["opened_at"]),
                    last_updated_at=datetime.fromisoformat(r["last_updated_at"]),
                    bars_held=r["bars_held"],
                    mfe_price=r["mfe_price"],
                    mae_price=r["mae_price"],
                    mfe_r=r["mfe_r"],
                    mae_r=r["mae_r"],
                    max_unrealized_pnl=r["max_unrealized_pnl"],
                    min_unrealized_pnl=r["min_unrealized_pnl"],
                    realized_pnl=r["realized_pnl"],
                    squeeze_state_at_entry=r["squeeze_state_at_entry"],
                    momentum_state_at_entry=r["momentum_state_at_entry"],
                    momentum_at_entry=r["momentum_at_entry"],
                    contract_id=r["contract_id"],
                    policy=ManagementPolicy(r["policy"]),
                    decay_bars=r["decay_bars"],
                    management_events=events_by_trade.get(r["trade_id"], []),
                    metadata=json.loads(r["metadata_json"]),
                )
            )
        return result

    def save_closed_trade(self, trade: ClosedTrade, idempotency_key: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trades (
                    trade_id, idempotency_key, timestamp, closed_at, symbol, direction,
                    entry, initial_risk, initial_stop, final_stop, exit_price, exit_reason,
                    duration_sec, bars_held, pnl, r_multiple, mfe, mae,
                    highest_profit, lowest_drawdown, squeeze_state_at_entry, squeeze_state_at_exit,
                    momentum_at_entry, momentum_at_exit, policy,
                    management_events_json, management_actions_json,
                    entry_quality, management_quality, attribution_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id,
                    idempotency_key,
                    trade.timestamp.isoformat(),
                    trade.closed_at.isoformat(),
                    trade.symbol,
                    trade.direction.value,
                    trade.entry,
                    trade.initial_risk,
                    trade.initial_stop,
                    trade.final_stop,
                    trade.exit,
                    trade.exit_reason.value,
                    trade.duration_sec,
                    trade.bars_held,
                    trade.pnl,
                    trade.r_multiple,
                    trade.mfe,
                    trade.mae,
                    trade.highest_profit,
                    trade.lowest_drawdown,
                    trade.squeeze_state_at_entry,
                    trade.squeeze_state_at_exit,
                    trade.momentum_at_entry,
                    trade.momentum_at_exit,
                    trade.policy.value,
                    json.dumps(list(trade.management_events), default=str),
                    json.dumps(list(trade.management_actions)),
                    trade.entry_quality,
                    trade.management_quality,
                    trade.attribution_summary,
                ),
            )
            conn.execute("DELETE FROM positions WHERE trade_id = ?", (trade.trade_id,))

    def load_closed_trades(self, limit: int = 1000) -> list[ClosedTrade]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY closed_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        trades: list[ClosedTrade] = []
        for r in rows:
            trades.append(
                ClosedTrade(
                    trade_id=r["trade_id"],
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                    closed_at=datetime.fromisoformat(r["closed_at"]),
                    symbol=r["symbol"],
                    direction=TradeDirection(r["direction"]),
                    entry=r["entry"],
                    initial_risk=r["initial_risk"],
                    initial_stop=r["initial_stop"],
                    final_stop=r["final_stop"],
                    exit=r["exit_price"],
                    exit_reason=ExitReason(r["exit_reason"]),
                    duration_sec=r["duration_sec"],
                    bars_held=r["bars_held"],
                    pnl=r["pnl"],
                    r_multiple=r["r_multiple"],
                    mfe=r["mfe"],
                    mae=r["mae"],
                    highest_profit=r["highest_profit"],
                    lowest_drawdown=r["lowest_drawdown"],
                    squeeze_state_at_entry=r["squeeze_state_at_entry"],
                    squeeze_state_at_exit=r["squeeze_state_at_exit"],
                    momentum_at_entry=r["momentum_at_entry"],
                    momentum_at_exit=r["momentum_at_exit"],
                    policy=ManagementPolicy(r["policy"]),
                    management_events=tuple(json.loads(r["management_events_json"])),
                    management_actions=tuple(json.loads(r["management_actions_json"])),
                    entry_quality=r["entry_quality"],
                    management_quality=r["management_quality"],
                    attribution_summary=r["attribution_summary"],
                )
            )
        return trades

    def record_management_event(self, event: ManagementEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO management_events (
                    event_id, trade_id, timestamp, stage_before, stage_after,
                    action, reason, confidence, risk_state, new_stop_price,
                    reduce_fraction, current_price, unrealized_pnl, unrealized_r,
                    mfe_r, mae_r, inputs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.trade_id,
                    event.timestamp.isoformat(),
                    event.stage_before.value,
                    event.stage_after.value,
                    event.decision.action.value,
                    event.decision.reason,
                    event.decision.confidence,
                    event.decision.risk_state,
                    event.decision.new_stop_price,
                    event.decision.reduce_fraction,
                    event.current_price,
                    event.unrealized_pnl,
                    event.unrealized_r,
                    event.mfe_r,
                    event.mae_r,
                    json.dumps(event.decision.inputs, default=str),
                ),
            )

    def record_signal(self, signal: TradeSignal, executed: bool = False) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO signals (
                    signal_id, symbol, direction, signal_type, squeeze_state,
                    momentum_state, momentum_strength, confidence, regime,
                    timestamp, timeframe_sec, entry_price, suggested_stop,
                    initial_risk_distance, executed, reasons_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.symbol,
                    signal.direction.value if signal.direction else None,
                    signal.signal_type.value,
                    signal.squeeze_state.value,
                    signal.momentum_state.value,
                    signal.momentum_strength,
                    signal.confidence,
                    signal.regime.value,
                    signal.timestamp.isoformat(),
                    signal.timeframe_sec,
                    signal.entry_price,
                    signal.suggested_stop,
                    signal.initial_risk_distance,
                    1 if executed else 0,
                    json.dumps(list(signal.reasons)),
                    json.dumps(signal.metadata, default=str),
                ),
            )

    def record_market_state(self, state: MarketStateSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_states (
                    symbol, timeframe_sec, timestamp, close, squeeze_state,
                    momentum_state, regime, val, normalized_momentum,
                    volatility_ratio, transitions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.symbol,
                    state.timeframe_sec,
                    state.timestamp.isoformat(),
                    state.close,
                    state.squeeze_state.value,
                    state.momentum_state.value,
                    state.regime.value,
                    None if str(state.squeeze.val) == "nan" else state.squeeze.val,
                    state.squeeze.normalized_momentum,
                    state.volatility_ratio,
                    json.dumps([t.value for t in state.transitions]),
                ),
            )

    def record_risk_event(self, risk_state: RiskState, event_type: str, detail: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (
                    timestamp, risk_level, event_type, balance, daily_pnl,
                    daily_drawdown_pct, consecutive_losses, open_positions_count, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    risk_state.level.value,
                    event_type,
                    risk_state.balance,
                    risk_state.daily_pnl,
                    risk_state.daily_drawdown_pct,
                    risk_state.consecutive_losses,
                    risk_state.open_positions_count,
                    detail,
                ),
            )

    def record_system_event(
        self,
        level: AlertPriority,
        component: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO system_events (timestamp, priority, component, message, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    level.value,
                    component,
                    message,
                    json.dumps(payload or {}, default=str),
                ),
            )

    def save_performance_snapshot(self, label: str, metrics: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO performance_metrics (timestamp, label, metrics_json)
                VALUES (?, ?, ?)
                """,
                (utc_now().isoformat(), label, json.dumps(metrics, default=str)),
            )

    def has_idempotency_key(self, idempotency_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM positions WHERE idempotency_key = ?
                UNION ALL
                SELECT 1 FROM trades WHERE idempotency_key = ?
                LIMIT 1
                """,
                (idempotency_key, idempotency_key),
            ).fetchone()
        return row is not None

    def table_counts(self) -> dict[str, int]:
        tables = (
            "config_versions",
            "positions",
            "trades",
            "management_events",
            "signals",
            "market_states",
            "risk_events",
            "system_events",
            "performance_metrics",
        )
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for tbl in tables:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()
                counts[tbl] = int(row["c"]) if row else 0
        return counts
