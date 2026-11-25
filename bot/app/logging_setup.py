"""Structured JSONL decision observability & UTF-8 safe file/console logging."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from bot.config.settings import LoggingSettings
from bot.core.models import utc_now


def configure_logging(settings: LoggingSettings) -> None:
    log_path = Path(settings.text_log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    Path(settings.json_log_path).parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


class DecisionAuditLogger:
    """Writes structured JSONL observability records for every management decision (Section 31)."""

    def __init__(self, json_log_path: str | Path = "logs/engine.jsonl") -> None:
        self.path = Path(json_log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_decision(
        self,
        *,
        trade_id: str,
        market_state: dict[str, Any],
        position_state: dict[str, Any],
        decision: dict[str, Any],
        risk_state: str,
    ) -> None:
        record = {
            "timestamp": utc_now().isoformat(),
            "trade_id": trade_id,
            "market_state": market_state,
            "position_state": position_state,
            "decision": decision,
            "reason": decision.get("reason"),
            "inputs": decision.get("inputs", {}),
            "risk_state": risk_state,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
