"""Centralized YAML + environment configuration loader with strict safety checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from bot.core.enums import AlertPriority, ManagementPolicy, OperatingMode

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    if isinstance(value, str):
        matches = _ENV_PATTERN.fullmatch(value.strip())
        if matches:
            env_val = os.environ.get(matches.group(1))
            return env_val if env_val != "" else None

        def _replace(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), "")

        return _ENV_PATTERN.sub(_replace, value)
    return value


class DerivSettings(BaseModel):
    app_id: str | None = None
    token: str | None = None
    endpoint: str = "wss://api.derivws.com/trading/v1/options/ws/public"
    rest_base_url: str = "https://api.derivws.com/trading/v1/options"
    discover_all_symbols: bool = True
    poll_interval_sec: float = Field(default=30.0, ge=10.0)
    symbol_refresh_sec: float = Field(default=3600.0, ge=300.0)
    ping_interval_sec: float = Field(default=25.0, ge=5.0)
    reconnect_base_sec: float = Field(default=2.0, ge=0.5)
    reconnect_max_sec: float = Field(default=60.0, ge=5.0)
    max_requests_per_sec: float = Field(default=5.0, gt=0.0)
    history_candles: int = Field(default=120, ge=60, le=5000)
    contract_type: str = "MULTIPLIER"

    @property
    def ws_url(self) -> str:
        if self.app_id and "/websockets/v3" in self.endpoint:
            sep = "&" if "?" in self.endpoint else "?"
            return f"{self.endpoint}{sep}app_id={self.app_id}"
        return self.endpoint


class SqueezeSettings(BaseModel):
    bb_length: int = Field(default=20, ge=1)
    bb_mult: float = Field(default=2.0, gt=0.0)
    kc_length: int = Field(default=20, ge=2)
    kc_mult: float = Field(default=1.5, gt=0.0)
    use_true_range: bool = True
    lazybear_mult_parity: bool = True
    tr_handle_na: bool = False


class RiskSettings(BaseModel):
    account_balance: float = Field(default=10000.0, gt=0.0)
    risk_per_trade: float = Field(default=0.01, gt=0.0, le=0.10)
    min_stake: float = Field(default=1.0, gt=0.0)
    max_stake: float = Field(default=250.0, gt=0.0)
    enforce_portfolio_caps: bool = False
    max_open_positions: int = Field(default=10000, ge=1)
    max_symbol_exposure: int = Field(default=1, ge=1)
    max_per_group_open: int = Field(default=10000, ge=1)
    max_concurrent_risk: float = Field(default=100.0, gt=0.0, le=1000.0)
    max_daily_loss: float = Field(default=1.0, gt=0.0, le=10.0)
    max_consecutive_losses: int = Field(default=10000, ge=1)
    allow_martingale: bool = False
    allow_averaging_down: bool = False
    abnormal_volatility_block: bool = True

    @model_validator(mode="after")
    def _enforce_hard_risk_prohibitions(self) -> RiskSettings:
        if self.allow_martingale:
            raise ValueError("Martingale is strictly prohibited in this engine")
        if self.allow_averaging_down:
            raise ValueError("Uncontrolled averaging down is strictly prohibited in this engine")
        if self.max_stake < self.min_stake:
            raise ValueError("max_stake must be >= min_stake")
        return self


class ManagementSettings(BaseModel):
    policy: ManagementPolicy = ManagementPolicy.DYNAMIC_MANAGEMENT
    breakeven_trigger_r: float = Field(default=0.50, gt=0.0)
    breakeven_buffer_r: float = Field(default=0.05, ge=0.0)
    profit_lock_trigger_r: float = Field(default=1.00, gt=0.0)
    profit_lock_floor_r: float = Field(default=0.45, gt=0.0)
    strong_profit_trigger_r: float = Field(default=1.50, gt=0.0)
    strong_profit_floor_r: float = Field(default=0.85, gt=0.0)
    runner_trigger_r: float = Field(default=2.00, gt=0.0)
    max_giveback_ratio: float = Field(default=0.35, gt=0.05, le=0.90)
    stagnation_bars: int = Field(default=12, ge=2)
    stagnation_min_r: float = Field(default=0.25)
    hard_invalidation_r: float = Field(default=-1.15, lt=0.0)
    momentum_exit_min_decay_bars: int = Field(default=2, ge=1)
    partial_reduce_fraction: float = Field(default=0.50, gt=0.0, lt=1.0)
    recovery_mode_enabled: bool = False


class StorageSettings(BaseModel):
    backend: str = "sqlite"
    sqlite_path: str = "data/squeeze_tme.db"
    postgres_dsn: str | None = None


class NotificationSettings(BaseModel):
    slack_enabled: bool = False
    slack_bot_token: str | None = None
    slack_channel: str | None = None
    slack_webhook_url: str | None = None
    reddit_enabled: bool = False
    reddit_subreddit: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_username: str | None = None
    reddit_password: str | None = None
    reddit_outbox_dir: str = "outbox/reddit"
    max_signals_per_sweep: int = Field(default=10000, ge=1)
    send_market_pulses: bool = False
    send_unentered_analysis_signals: bool = False
    min_stop_change_r_for_alert: float = Field(default=0.25, ge=0.0)
    min_tighten_change_r_for_alert: float = Field(default=0.10, ge=0.0)
    min_slack_priority: AlertPriority = AlertPriority.NOTICE
    min_reddit_priority: AlertPriority = AlertPriority.NOTICE
    broadcast_management_updates: bool = True
    notify_thesis_warnings: bool = True
    thesis_warning_escalation_only: bool = True
    drawdown_warning_levels_r: list[float] = Field(default_factory=lambda: [-0.50, -0.75])
    thesis_warning_cooldown_bars: int = Field(default=2, ge=1)
    slack_alert_mention: str | None = None
    reddit_alert_mention: str | None = None


class DashboardSettings(BaseModel):
    enabled: bool = True
    refresh_hz: float = Field(default=2.0, gt=0.1, le=20.0)


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_log_path: str = "logs/engine.jsonl"
    text_log_path: str = "logs/engine.log"


class SymbolProfile(BaseModel):
    symbol: str
    enabled: bool = True
    description: str = ""
    market: str = "synthetic_index"
    submarket: str = "random_index"
    exchange_is_open: bool = True
    primary_timeframe: int = Field(default=900, ge=60)
    higher_timeframe: int | None = None
    lower_timeframe: int | None = None
    multiplier: float = Field(default=50.0, gt=0.0)
    risk_multiplier: float = Field(default=1.0, gt=0.0, le=2.0)
    atr_stop_multiple: float = Field(default=2.0, gt=0.5, le=10.0)
    trailing_factor: float = Field(default=1.25, gt=0.3, le=5.0)
    min_confidence: float = Field(default=0.58, ge=0.0, le=1.0)
    cooldown_bars: int = Field(default=0, ge=0)
    max_exposure: int = Field(default=1, ge=1)
    spread_bps: float = Field(default=3.0, ge=0.0)
    slippage_bps: float = Field(default=1.5, ge=0.0)
    slack_channel: str | None = None
    reddit_flair: str = "📈 #volatility-indices"
    squeeze: SqueezeSettings | None = None


class EngineSettings(BaseModel):
    mode: OperatingMode = OperatingMode.ANALYSIS_ONLY
    deriv: DerivSettings = Field(default_factory=DerivSettings)
    squeeze: SqueezeSettings = Field(default_factory=SqueezeSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    management: ManagementSettings = Field(default_factory=ManagementSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    symbols: dict[str, SymbolProfile] = Field(default_factory=dict)
    config_version: str = ""

    def enabled_symbols(self) -> list[SymbolProfile]:
        return [profile for profile in self.symbols.values() if profile.enabled]

    def squeeze_for(self, symbol: str) -> SqueezeSettings:
        profile = self.symbols.get(symbol)
        if profile is not None and profile.squeeze is not None:
            return profile.squeeze
        return self.squeeze


def load_settings(
    settings_path: str | Path = "config/settings.yaml",
    symbols_path: str | Path = "config/symbols.yaml",
    env_file: str | Path | None = ".env",
    mode_override: OperatingMode | None = None,
) -> EngineSettings:
    """Load YAML configuration, interpolate env secrets, and compute config hash."""
    if env_file is not None and Path(env_file).exists():
        load_dotenv(env_file, override=False)

    settings_data: dict[str, Any] = {}
    s_path = Path(settings_path)
    if s_path.exists():
        raw = yaml.safe_load(s_path.read_text(encoding="utf-8")) or {}
        settings_data = _interpolate_env(raw)

    symbols_data: dict[str, SymbolProfile] = {}
    sym_path = Path(symbols_path)
    if sym_path.exists():
        raw_syms = yaml.safe_load(sym_path.read_text(encoding="utf-8")) or {}
        raw_syms = _interpolate_env(raw_syms).get("symbols", {})
        for sym_name, sym_cfg in raw_syms.items():
            cfg = dict(sym_cfg or {})
            cfg["symbol"] = sym_name
            symbols_data[sym_name] = SymbolProfile(**cfg)

    if mode_override is not None:
        settings_data["mode"] = mode_override.value

    settings_data["symbols"] = symbols_data
    settings = EngineSettings(**settings_data)

    # Compute deterministic config version excluding secret tokens
    safe_dump = settings.model_dump(
        mode="json",
        exclude={
            "deriv": {"token"},
            "notifications": {
                "slack_bot_token",
                "slack_webhook_url",
                "reddit_client_secret",
                "reddit_password",
            },
        },
    )
    digest = hashlib.sha256(
        json.dumps(safe_dump, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    settings.config_version = digest
    return settings
