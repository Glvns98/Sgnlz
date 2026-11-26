"""Priority-aware Notification Router with anti-spam cooldowns & threaded lifecycle support."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bot.config.settings import NotificationSettings
from bot.core.enums import AlertPriority
from bot.core.models import utc_now
from bot.notifications.reddit import RedditDeliveryResult, RedditPublisher
from bot.notifications.slack import SlackDeliveryResult, SlackNotifier


class NotificationRouter:
    """Routes alerts by priority level (INFO -> EMERGENCY) and manages Slack/Reddit threads."""

    def __init__(
        self,
        settings: NotificationSettings,
        *,
        cooldown_sec: float = 30.0,
    ) -> None:
        self.settings = settings
        self.slack = SlackNotifier(settings)
        self.reddit = RedditPublisher(settings)
        self.cooldown_sec = cooldown_sec
        self._last_sent: dict[str, datetime] = {}
        self.dispatched_log: list[dict[str, Any]] = []

    def _can_send(self, dedupe_key: str, priority: AlertPriority, now: datetime) -> bool:
        if priority >= AlertPriority.CRITICAL:
            return True
        prev = self._last_sent.get(dedupe_key)
        if prev is not None and (now - prev) < timedelta(seconds=self.cooldown_sec):
            return False
        self._last_sent[dedupe_key] = now
        return True

    async def notify(
        self,
        *,
        dedupe_key: str,
        priority: AlertPriority,
        slack_text: str | None = None,
        reddit_post: tuple[str, str] | None = None,
        channel: str | None = None,
    ) -> dict[str, bool]:
        now = utc_now()
        delivered = {"slack": False, "reddit": False, "filtered": False}

        if not self._can_send(dedupe_key, priority, now):
            delivered["filtered"] = True
            return delivered

        self.dispatched_log.append(
            {
                "timestamp": now.isoformat(),
                "priority": priority.value,
                "key": dedupe_key,
            }
        )

        if slack_text and priority >= self.settings.min_slack_priority:
            s_res = await self.slack.send(
                slack_text,
                channel=channel,
                meta={"priority": priority.value, "key": dedupe_key},
            )
            delivered["slack"] = s_res.ok

        if reddit_post and priority >= self.settings.min_reddit_priority:
            r_res = await self.reddit.publish_post(reddit_post[0], reddit_post[1])
            delivered["reddit"] = r_res.ok

        return delivered

    async def notify_trade_opened(
        self,
        *,
        trade_id: str,
        channel: str | None,
        slack_text: str,
        slack_blocks: list[dict[str, Any]] | None,
        reddit_title: str,
        reddit_body: str,
        reddit_flair: str | None = None,
    ) -> tuple[SlackDeliveryResult, RedditDeliveryResult]:
        now = utc_now()
        self.dispatched_log.append(
            {"timestamp": now.isoformat(), "priority": AlertPriority.NOTICE.value, "key": f"open:{trade_id}"}
        )
        slack_res = SlackDeliveryResult(ok=False)
        if AlertPriority.NOTICE >= self.settings.min_slack_priority:
            slack_res = await self.slack.send(
                slack_text,
                channel=channel,
                blocks=slack_blocks,
                meta={"priority": AlertPriority.NOTICE.value, "trade_id": trade_id, "event": "opened"},
            )
        reddit_res = RedditDeliveryResult(ok=False)
        if AlertPriority.NOTICE >= self.settings.min_reddit_priority:
            reddit_res = await self.reddit.publish_post(
                reddit_title,
                reddit_body,
                trade_id=trade_id,
                flair_text=reddit_flair,
            )
        return slack_res, reddit_res

    async def notify_trade_comment(
        self,
        *,
        dedupe_key: str,
        trade_id: str,
        priority: AlertPriority,
        channel: str | None,
        thread_ts: str | None,
        slack_text: str,
        slack_blocks: list[dict[str, Any]] | None = None,
        reddit_post_id: str | None,
        reddit_comment_body: str,
        label: str = "update",
        reply_broadcast: bool = False,
    ) -> tuple[SlackDeliveryResult, RedditDeliveryResult]:
        now = utc_now()
        if not self._can_send(dedupe_key, priority, now):
            return SlackDeliveryResult(ok=False, error="cooldown_filtered"), RedditDeliveryResult(ok=False, error="cooldown_filtered")

        mention = (self.settings.slack_alert_mention or "").strip()
        final_slack_text = slack_text
        final_slack_blocks = list(slack_blocks) if slack_blocks is not None else None
        if mention and priority >= AlertPriority.WARNING:
            if mention not in final_slack_text:
                final_slack_text = f"{mention} {final_slack_text}"
            if final_slack_blocks is not None:
                final_slack_blocks.append(
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"🔔 {mention}"}],
                    }
                )

        self.dispatched_log.append(
            {"timestamp": now.isoformat(), "priority": priority.value, "key": dedupe_key, "trade_id": trade_id}
        )
        slack_res = SlackDeliveryResult(ok=False)
        if priority >= self.settings.min_slack_priority:
            slack_res = await self.slack.send(
                final_slack_text,
                channel=channel,
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast,
                blocks=final_slack_blocks,
                meta={"priority": priority.value, "trade_id": trade_id, "label": label},
            )
        reddit_res = RedditDeliveryResult(ok=False)
        if priority >= self.settings.min_reddit_priority:
            reddit_mention = (self.settings.reddit_alert_mention or "").strip()
            final_reddit_body = reddit_comment_body
            if reddit_mention and reddit_mention.lower() not in final_reddit_body.lower():
                final_reddit_body = f"{final_reddit_body}\n\n---\n🔔 *Alert:* {reddit_mention}"
            reddit_res = await self.reddit.publish_comment(
                reddit_post_id,
                final_reddit_body,
                trade_id=trade_id,
                label=label,
            )
        return slack_res, reddit_res
