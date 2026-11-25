"""Slack event-driven notifier with Block Kit cards and threaded lifecycle updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from bot.config.settings import NotificationSettings
from bot.core.models import (
    ClosedTrade,
    ManagementEvent,
    MarketStateSnapshot,
    Position,
    RiskState,
    TradeSignal,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackDeliveryResult:
    ok: bool
    channel: str | None = None
    ts: str | None = None
    error: str | None = None


class SlackNotifier:
    """Formats clean, non-spammy Slack messages & threaded replies via Bot Token or Webhook."""

    def __init__(self, settings: NotificationSettings) -> None:
        self.settings = settings
        self.sent_messages: list[dict[str, Any]] = []

    @staticmethod
    def format_signal(signal: TradeSignal, risk_pct: float = 0.01) -> str:
        sqz_label = "RELEASE" if signal.squeeze_state.value == "SQUEEZE_OFF" else signal.squeeze_state.value
        return (
            "🟢 *NEW TRADE OPPORTUNITY*\n\n"
            f"Symbol: `{signal.symbol}`\n"
            f"Direction: *{signal.direction.value if signal.direction else 'N/A'}*\n"
            f"Squeeze: `{sqz_label}`\n"
            f"Momentum: `{signal.momentum_state.value}` (strength {signal.momentum_strength:.2f})\n"
            f"Risk: `{risk_pct * 100:.1f}%`\n"
            "Management Mode: `ACTIVE`"
        )

    @staticmethod
    def format_trade_opened(position: Position) -> str:
        return (
            "🚀 *POSITION OPENED*\n\n"
            f"Trade ID: `{position.trade_id}`\n"
            f"Symbol: `{position.symbol}`\n"
            f"Direction: *{position.direction.value}*\n"
            f"Entry: `{position.entry_price:.4f}`\n"
            f"Initial Stop: `{position.initial_stop:.4f}`\n"
            f"Initial Risk: `${position.initial_risk_amount:.2f}`\n"
            f"Management Policy: `{position.policy.value}`"
        )

    @staticmethod
    def format_management_update(event: ManagementEvent, symbol: str) -> str:
        return (
            "🛡 *TRADE MANAGEMENT UPDATE*\n\n"
            f"Trade `{event.trade_id}` (`{symbol}`)\n"
            f"Profit: `{event.unrealized_r:+.2f}R`\n"
            f"Action: *{event.decision.action.value}*\n"
            f"Stage: `{event.stage_before.value}` → `{event.stage_after.value}`\n"
            f"Reason: `{event.decision.reason}`"
        )

    @staticmethod
    def format_trade_closed(trade: ClosedTrade) -> str:
        giveback = max(0.0, trade.mfe - trade.r_multiple)
        mins = trade.duration_sec / 60.0
        return (
            "🔴 *POSITION CLOSED*\n\n"
            f"Trade `{trade.trade_id}` (`{trade.symbol}` {trade.direction.value})\n"
            f"Result: *{trade.r_multiple:+.2f}R* (`${trade.pnl:+.2f}`)\n"
            f"Duration: `{mins:.1f}m` ({trade.bars_held} bars)\n"
            f"Exit Reason: `{trade.exit_reason.value}`\n"
            f"Maximum Profit: `{trade.mfe:+.2f}R`\n"
            f"Profit Given Back: `{giveback:.2f}R`"
        )

    @staticmethod
    def format_risk_alert(risk_state: RiskState, max_dd_pct: float) -> str:
        status_note = (
            "New entries restricted. Existing positions remain under management."
            if not risk_state.entries_allowed
            else "Monitoring risk closely."
        )
        return (
            "⚠️ *RISK ALERT*\n\n"
            f"Risk Level: `{risk_state.level.value}`\n"
            f"Daily drawdown: `{risk_state.daily_drawdown_pct * 100:.1f}%`\n"
            f"Maximum allowed: `{max_dd_pct * 100:.1f}%`\n"
            f"{status_note}"
        )

    @staticmethod
    def format_market_pulse(
        mode: str,
        market_states: dict[str, MarketStateSnapshot],
        open_positions: list[Position],
        risk_state: RiskState,
        symbols_cfg: dict[str, Any] | None = None,
    ) -> str:
        lines = [
            "📡 *SQUEEZE TRADE MANAGEMENT ENGINE — MARKET PULSE*",
            f"Mode: `{mode}` | Risk: `{risk_state.level.value}` | Analyzed: `{len(market_states)}` | Open Trades: `{len(open_positions)}`",
            "",
        ]
        if not market_states:
            lines.append("• No symbols hydrated yet.")
            return "\n".join(lines)

        if len(market_states) <= 12:
            for sym, ms in sorted(market_states.items()):
                sqz = "RELEASE" if ms.squeeze.sqz_fired else ms.squeeze_state.value.replace("SQUEEZE_", "")
                lines.append(
                    f"• `{sym}` @ `{ms.close:.4f}` | Squeeze: `{sqz}` | "
                    f"Momentum: `{ms.momentum_state.value}` | Regime: `{ms.regime.value}`"
                )
            return "\n".join(lines)

        grouped: dict[str, list[MarketStateSnapshot]] = {}
        for sym, ms in market_states.items():
            grp = getattr(symbols_cfg.get(sym), "submarket", "other") if symbols_cfg and sym in symbols_cfg else "other"
            grouped.setdefault(grp, []).append(ms)

        for grp, items in sorted(grouped.items()):
            sqz_on = sum(1 for s in items if s.squeeze_state.value == "SQUEEZE_ON")
            fired = sum(1 for s in items if s.squeeze.sqz_fired)
            bull = sum(1 for s in items if "BULLISH" in s.momentum_state.value)
            bear = sum(1 for s in items if "BEARISH" in s.momentum_state.value)
            lines.append(
                f"• *{grp}* (`{len(items)}`): Squeeze ON `{sqz_on}` · Fired `{fired}` · Bull `{bull}` / Bear `{bear}`"
            )

        if open_positions:
            lines.append("")
            lines.append("*Active Exposure:* " + " · ".join(f"`{p.symbol}` {p.direction.value} ({p.unrealized_r():+.2f}R)" for p in open_positions[:8]))

        return "\n".join(lines)[:2900]

    async def send(
        self,
        text: str,
        *,
        channel: str | None = None,
        thread_ts: str | None = None,
        reply_broadcast: bool = False,
        blocks: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SlackDeliveryResult:
        target_channel = channel or self.settings.slack_channel
        self.sent_messages.append(
            {
                "text": text,
                "channel": target_channel,
                "thread_ts": thread_ts,
                "reply_broadcast": reply_broadcast,
                "blocks": blocks,
                "meta": meta or {},
            }
        )
        if not self.settings.slack_enabled:
            return SlackDeliveryResult(ok=False, channel=target_channel, error="slack_disabled")

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                if self.settings.slack_webhook_url:
                    wh_payload: dict[str, Any] = {"text": text}
                    if blocks:
                        wh_payload["blocks"] = blocks
                    async with session.post(self.settings.slack_webhook_url, json=wh_payload) as resp:
                        ok = resp.status < 300
                        logger.info("Slack webhook delivery status=%s ok=%s", resp.status, ok)
                        return SlackDeliveryResult(ok=ok, channel=target_channel)
                if self.settings.slack_bot_token and target_channel:
                    headers = {"Authorization": f"Bearer {self.settings.slack_bot_token}"}
                    payload: dict[str, Any] = {"channel": target_channel, "text": text}
                    if blocks:
                        payload["blocks"] = blocks
                    if thread_ts:
                        payload["thread_ts"] = thread_ts
                        if reply_broadcast:
                            payload["reply_broadcast"] = True
                    async with session.post(
                        "https://slack.com/api/chat.postMessage",
                        headers=headers,
                        json=payload,
                    ) as resp:
                        data = await resp.json()
                        ok = bool(data.get("ok"))
                        if ok:
                            logger.info(
                                "Slack message posted to %s (ts=%s, thread_ts=%s)",
                                data.get("channel"),
                                data.get("ts"),
                                thread_ts,
                            )
                            return SlackDeliveryResult(
                                ok=True,
                                channel=str(data.get("channel", target_channel)),
                                ts=str(data.get("ts")) if data.get("ts") else None,
                            )
                        err = str(data.get("error", "unknown_error"))
                        logger.warning("Slack chat.postMessage error (%s): %s", target_channel, err)
                        return SlackDeliveryResult(ok=False, channel=target_channel, error=err)
        except Exception as exc:
            logger.warning("Slack delivery failed safely: %s", exc)
            return SlackDeliveryResult(ok=False, channel=target_channel, error=str(exc))
        return SlackDeliveryResult(ok=False, channel=target_channel, error="missing_slack_credentials")
