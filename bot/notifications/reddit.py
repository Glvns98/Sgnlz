"""Reddit Community Educational Publisher with strict sanitization & threaded comments."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from bot.config.settings import NotificationSettings
from bot.core.models import ClosedTrade, ManagementEvent, MarketStateSnapshot, utc_now

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(token|secret|password|api_key|app_id|authorization)\s*[:=]\s*\S+"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b"),
    re.compile(r"\b(?:account\s*balance|balance)\s*[:=]\s*\$?[0-9,]+(?:\.[0-9]+)?", re.IGNORECASE),
    re.compile(r"\bguarantee[ds]?\s+profit", re.IGNORECASE),
)


def sanitize_public_text(text: str) -> str:
    """Redact any accidental secrets, balances, or irresponsible profit-guarantee phrases."""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


@dataclass(frozen=True)
class RedditDeliveryResult:
    ok: bool
    post_id: str | None = None
    outbox_path: str | None = None
    error: str | None = None


class RedditPublisher:
    """Publishes sanitized educational posts & threaded lifecycle comments to Reddit + outbox."""

    def __init__(self, settings: NotificationSettings) -> None:
        self.settings = settings
        self.published_posts: list[dict[str, Any]] = []
        self._comment_seq: dict[str, int] = {}

    @staticmethod
    def build_trade_recap_post(trade: ClosedTrade, events: list[ManagementEvent] | None = None) -> tuple[str, str]:
        title = (
            f"[{trade.symbol}] Squeeze Trade Management Case Study: "
            f"{trade.direction.value} ({trade.r_multiple:+.2f}R, {trade.exit_reason.value})"
        )
        actions_text = ", ".join(trade.management_actions) if trade.management_actions else "HOLD"
        giveback = max(0.0, trade.mfe - trade.r_multiple)

        body = (
            f"### {trade.symbol} — Market & Management Breakdown\n\n"
            "**1. WHAT HAPPENED**\n"
            f"- Direction: `{trade.direction.value}` | Squeeze at Entry: `{trade.squeeze_state_at_entry}` "
            f"→ Exit: `{trade.squeeze_state_at_exit}`\n"
            f"- Outcome: `{trade.r_multiple:+.2f}R` over `{trade.bars_held}` bars (MFE: `{trade.mfe:+.2f}R`, MAE: `{trade.mae:+.2f}R`)\n\n"
            "**2. WHY IT HAPPENED**\n"
            f"- Entry momentum sensor (`Squeeze.pine`): `{trade.momentum_at_entry:+.4f}` → Exit: `{trade.momentum_at_exit:+.4f}`\n\n"
            "**3. WHAT THE SYSTEM DID**\n"
            f"- Active Management Policy: `{trade.policy.value}`\n"
            f"- Management Actions Taken: `{actions_text}`\n\n"
            "**4. WHY MANAGEMENT CHANGED**\n"
            f"- Exit Trigger: `{trade.exit_reason.value}` | Attribution: `{trade.attribution_summary}`\n\n"
            "**5. WHAT THE OUTCOME WAS**\n"
            f"- Profit Surrendered from Peak: `{giveback:.2f}R` | Entry Quality: `{trade.entry_quality}` | Management Quality: `{trade.management_quality}`\n\n"
            "_Note: Automated educational market-state & risk-management log. Trading carries risk and no outcomes are guaranteed._"
        )
        return sanitize_public_text(title), sanitize_public_text(body)

    @staticmethod
    def build_market_pulse_post(
        market_states: dict[str, MarketStateSnapshot],
        symbols_cfg: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        title = f"Squeeze Momentum Full-Universe Pulse ({len(market_states)} Deriv Instruments Analyzed)"
        if len(market_states) <= 12:
            rows = []
            for sym, ms in sorted(market_states.items()):
                sqz = "RELEASE" if ms.squeeze.sqz_fired else ms.squeeze_state.value
                rows.append(
                    f"| `{sym}` | `{sqz}` | `{ms.momentum_state.value}` | "
                    f"`{ms.regime.value}` | `{ms.squeeze.momentum_strength:.2f}` |"
                )
            table_hdr = "| Symbol | Squeeze State | Momentum State | Regime | Strength |\n|---|---|---|---|---|\n"
            table = table_hdr + ("\n".join(rows) if rows else "| - | - | - | - | - |")
        else:
            grouped: dict[str, list[MarketStateSnapshot]] = {}
            for sym, ms in market_states.items():
                grp = getattr(symbols_cfg.get(sym), "submarket", "other") if symbols_cfg and sym in symbols_cfg else "other"
                grouped.setdefault(grp, []).append(ms)
            rows = []
            for grp, items in sorted(grouped.items()):
                sqz_on = sum(1 for s in items if s.squeeze_state.value == "SQUEEZE_ON")
                fired = sum(1 for s in items if s.squeeze.sqz_fired)
                bull = sum(1 for s in items if "BULLISH" in s.momentum_state.value)
                bear = sum(1 for s in items if "BEARISH" in s.momentum_state.value)
                rows.append(f"| `{grp}` | {len(items)} | {sqz_on} | {fired} | {bull} / {bear} |")
            table_hdr = "| Market Group | Analyzed | Squeeze ON | Fired | Bull / Bear |\n|---|---|---|---|---|\n"
            table = table_hdr + ("\n".join(rows) if rows else "| - | 0 | 0 | 0 | 0 / 0 |")

        body = (
            "### Automated `Squeeze.pine` Market-State Snapshot\n\n"
            "**1. WHAT HAPPENED**\n"
            f"The engine scanned `{len(market_states)}` active Deriv instruments across all available market groups "
            "and evaluated LazyBear `Squeeze.pine` compression/expansion states.\n\n"
            "**2. WHY IT HAPPENED**\n"
            f"{table}\n\n"
            "**3. WHAT THE SYSTEM DID**\n"
            "Only symbols exhibiting verified squeeze release + aligned momentum acceleration outside abnormal volatility are promoted as trade signals.\n\n"
            "**4. WHY MANAGEMENT CHANGED**\n"
            "Dynamic lifecycle protection (`INITIAL_PROTECTION -> BREAKEVEN -> PROFIT_LOCK -> TRAILING -> MOMENTUM_DECAY`) activates automatically once any position opens.\n\n"
            "**5. WHAT THE OUTCOME WAS**\n"
            "Continuous closed-bar monitoring across all discovered markets is active.\n\n"
            "_Note: Automated educational market-state & risk-management log. Trading carries risk and no outcomes are guaranteed._"
        )
        return sanitize_public_text(title), sanitize_public_text(body)

    async def _oauth_token(self, session: aiohttp.ClientSession) -> str | None:
        if not (
            self.settings.reddit_subreddit
            and self.settings.reddit_client_id
            and self.settings.reddit_client_secret
            and self.settings.reddit_username
            and self.settings.reddit_password
        ):
            return None
        auth = aiohttp.BasicAuth(self.settings.reddit_client_id, self.settings.reddit_client_secret)
        headers = {"User-Agent": "deriv-squeeze-tme/0.1"}
        async with session.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            headers=headers,
            data={
                "grant_type": "password",
                "username": self.settings.reddit_username,
                "password": self.settings.reddit_password,
            },
        ) as tok_resp:
            tok_data = await tok_resp.json()
            return tok_data.get("access_token")

    async def publish_post(
        self,
        title: str,
        body: str,
        *,
        trade_id: str | None = None,
        flair_text: str | None = None,
    ) -> RedditDeliveryResult:
        safe_title = sanitize_public_text(title)
        safe_body = sanitize_public_text(body)
        record = {
            "kind": "post",
            "trade_id": trade_id,
            "flair": flair_text,
            "title": safe_title,
            "body": safe_body,
            "timestamp": utc_now().isoformat(),
        }
        self.published_posts.append(record)

        outbox = Path(self.settings.reddit_outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        if trade_id:
            t_dir = outbox / trade_id
            t_dir.mkdir(parents=True, exist_ok=True)
            flair_hdr = f"**Flair:** `{flair_text}`\n\n" if flair_text else ""
            post_md = f"# {safe_title}\n\n{flair_hdr}{safe_body}\n"
            post_file = t_dir / "00_post.md"
            post_file.write_text(post_md, encoding="utf-8")
            (t_dir / "thread_preview.md").write_text(post_md, encoding="utf-8")
            out_path = str(post_file)
        else:
            fname = f"reddit_{len(self.published_posts):04d}.md"
            post_file = outbox / fname
            post_file.write_text(f"# {safe_title}\n\n{safe_body}\n", encoding="utf-8")
            out_path = str(post_file)

        if not self.settings.reddit_enabled:
            return RedditDeliveryResult(ok=False, outbox_path=out_path, error="reddit_disabled")

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                access_token = await self._oauth_token(session)
                if not access_token:
                    logger.info("Reddit credentials incomplete; saved sanitized post preview to %s", out_path)
                    return RedditDeliveryResult(ok=False, outbox_path=out_path, error="missing_reddit_credentials")
                post_headers = {"Authorization": f"bearer {access_token}", "User-Agent": "deriv-squeeze-tme/0.1"}
                async with session.post(
                    "https://oauth.reddit.com/api/submit",
                    headers=post_headers,
                    data={
                        "api_type": "json",
                        "kind": "self",
                        "sr": self.settings.reddit_subreddit,
                        "title": safe_title,
                        "text": safe_body,
                    },
                ) as sub_resp:
                    resp_json = await sub_resp.json()
                    post_name = (((resp_json.get("json") or {}).get("data") or {}).get("name"))
                    ok = sub_resp.status < 300 and bool(post_name)
                    return RedditDeliveryResult(
                        ok=ok,
                        post_id=str(post_name) if post_name else None,
                        outbox_path=out_path,
                    )
        except Exception as exc:
            logger.warning("Reddit publish_post failed safely: %s", exc)
            return RedditDeliveryResult(ok=False, outbox_path=out_path, error=str(exc))

    async def publish_comment(
        self,
        post_id: str | None,
        body: str,
        *,
        trade_id: str | None = None,
        label: str = "update",
    ) -> RedditDeliveryResult:
        safe_body = sanitize_public_text(body)
        record = {
            "kind": "comment",
            "trade_id": trade_id,
            "post_id": post_id,
            "label": label,
            "body": safe_body,
            "timestamp": utc_now().isoformat(),
        }
        self.published_posts.append(record)

        outbox = Path(self.settings.reddit_outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        key = trade_id or "general"
        seq = self._comment_seq.get(key, 0) + 1
        self._comment_seq[key] = seq

        if trade_id:
            t_dir = outbox / trade_id
            t_dir.mkdir(parents=True, exist_ok=True)
            safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label.lower()).strip("_") or "update"
            c_file = t_dir / f"{seq:02d}_comment_{safe_label}.md"
            c_file.write_text(f"{safe_body}\n", encoding="utf-8")
            preview = t_dir / "thread_preview.md"
            with preview.open("a", encoding="utf-8") as f:
                f.write(f"\n\n---\n\n<!-- Comment #{seq} ({safe_label}) -->\n\n{safe_body}\n")
            out_path = str(c_file)
        else:
            c_file = outbox / f"comment_{seq:04d}.md"
            c_file.write_text(f"{safe_body}\n", encoding="utf-8")
            out_path = str(c_file)

        if not self.settings.reddit_enabled or not post_id or not str(post_id).startswith("t3_"):
            return RedditDeliveryResult(ok=False, post_id=post_id, outbox_path=out_path, error="local_preview_only")

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                access_token = await self._oauth_token(session)
                if not access_token:
                    return RedditDeliveryResult(ok=False, post_id=post_id, outbox_path=out_path, error="missing_reddit_credentials")
                post_headers = {"Authorization": f"bearer {access_token}", "User-Agent": "deriv-squeeze-tme/0.1"}
                async with session.post(
                    "https://oauth.reddit.com/api/comment",
                    headers=post_headers,
                    data={
                        "api_type": "json",
                        "thing_id": post_id,
                        "text": safe_body,
                    },
                ) as c_resp:
                    return RedditDeliveryResult(ok=c_resp.status < 300, post_id=post_id, outbox_path=out_path)
        except Exception as exc:
            logger.warning("Reddit publish_comment failed safely: %s", exc)
            return RedditDeliveryResult(ok=False, post_id=post_id, outbox_path=out_path, error=str(exc))

    async def publish(self, title: str, body: str) -> bool:
        res = await self.publish_post(title, body)
        return res.ok
