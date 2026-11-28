"""Tests for AlertPriority router, Slack message templates, Reddit sanitizer, and Terminal Dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.config.settings import ManagementSettings, NotificationSettings, SymbolProfile
from bot.core.enums import (
    AlertPriority,
    ExitReason,
    ManagementAction,
    ManagementPolicy,
    MarketRegime,
    MomentumState,
    RiskLevel,
    SignalType,
    SqueezeState,
    TradeDirection,
    TradeStage,
)
from bot.core.models import (
    ClosedTrade,
    ManagementDecision,
    ManagementEvent,
    Position,
    RiskState,
    TradeSignal,
)
from bot.dashboard.terminal import DashboardState, TerminalDashboard
from bot.notifications.formatter import (
    build_management_comment_bundle,
    build_position_warning_comment_bundle,
    build_trade_closed_comment_bundle,
    build_trade_opened_bundle,
)
from bot.notifications.reddit import RedditPublisher, sanitize_public_text
from bot.notifications.router import NotificationRouter
from bot.notifications.slack import SlackNotifier
from bot.tests.conftest import make_market_state


def test_reddit_sanitizer_strips_secrets_balances_and_guarantees() -> None:
    unsafe = (
        "token=my_secret_123 xoxb-12345-abcde account balance: $12,345.67 "
        "This setup guarantees profit every single day!"
    )
    cleaned = sanitize_public_text(unsafe)
    assert "my_secret_123" not in cleaned
    assert "xoxb-12345" not in cleaned
    assert "12,345.67" not in cleaned
    assert "guarantees profit" not in cleaned.lower()


@pytest.mark.asyncio
async def test_notification_priority_cooldown_and_formatters(tmp_path) -> None:
    cfg = NotificationSettings(
        slack_enabled=False,
        reddit_enabled=False,
        reddit_outbox_dir=str(tmp_path / "reddit"),
        min_slack_priority=AlertPriority.NOTICE,
        min_reddit_priority=AlertPriority.NOTICE,
    )
    router = NotificationRouter(cfg, cooldown_sec=60.0)

    # INFO filtered out by min priority
    r_info = await router.notify(
        dedupe_key="state:R_100",
        priority=AlertPriority.INFO,
        slack_text="routine state",
    )
    assert len(router.slack.sent_messages) == 0
    assert r_info["filtered"] is False

    # NOTICE accepted once, duplicate suppressed by cooldown
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sig = TradeSignal(
        signal_id="s1",
        symbol="R_100",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.82,
        confidence=0.76,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=now,
        timeframe_sec=900,
        entry_price=100.0,
        suggested_stop=98.0,
        initial_risk_distance=2.0,
    )
    msg = SlackNotifier.format_signal(sig)
    assert "NEW TRADE OPPORTUNITY" in msg

    await router.notify(dedupe_key="sig:R_100", priority=AlertPriority.NOTICE, slack_text=msg)
    dup = await router.notify(dedupe_key="sig:R_100", priority=AlertPriority.NOTICE, slack_text=msg)
    assert dup["filtered"] is True
    assert len(router.slack.sent_messages) == 1

    # CRITICAL bypasses cooldown
    rs = RiskState(RiskLevel.HALTED, 9500.0, 10000.0, -500.0, 0.05, 5, 0, 0.0, False, ("max_daily_loss",))
    await router.notify(
        dedupe_key="sig:R_100",
        priority=AlertPriority.CRITICAL,
        slack_text=SlackNotifier.format_risk_alert(rs, 0.05),
    )
    assert len(router.slack.sent_messages) == 2

    # Reddit educational recap writes sanitized markdown preview
    ct = ClosedTrade(
        trade_id="T-9",
        timestamp=now,
        closed_at=now,
        symbol="R_100",
        direction=TradeDirection.BUY,
        entry=100.0,
        initial_risk=100.0,
        initial_stop=98.0,
        final_stop=101.0,
        exit=102.5,
        exit_reason=ExitReason.TRAILING_STOP,
        duration_sec=900.0,
        bars_held=4,
        pnl=125.0,
        r_multiple=1.25,
        mfe=1.50,
        mae=-0.10,
        highest_profit=150.0,
        lowest_drawdown=-10.0,
        squeeze_state_at_entry="SQUEEZE_OFF",
        squeeze_state_at_exit="SQUEEZE_OFF",
        momentum_at_entry=1.5,
        momentum_at_exit=0.8,
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
        management_actions=("MOVE_TO_BREAKEVEN", "TRAIL"),
        entry_quality="GOOD_ENTRY",
        management_quality="WELL_MANAGED",
        attribution_summary="Clean trend follow-through",
    )
    title, body = RedditPublisher.build_trade_recap_post(ct)
    await router.notify(dedupe_key="red:T-9", priority=AlertPriority.NOTICE, reddit_post=(title, body))
    assert (tmp_path / "reddit" / "reddit_0001.md").exists()


def test_terminal_dashboard_renders_cleanly() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pos = Position(
        trade_id="T-DASH",
        idempotency_key="kdash",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.TRAILING,
        entry_price=100.0,
        current_price=103.0,
        initial_stop=98.0,
        current_stop=101.5,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=20.0,
        initial_risk_amount=100.0,
        initial_risk_distance=2.0,
        opened_at=now,
        last_updated_at=now,
    )
    dash = TerminalDashboard()
    rendered = dash.render_text(
        DashboardState(
            api_connected=True,
            engine_status="RUNNING",
            mode="PAPER_TRADING",
            market_states={"R_100": make_market_state(close=103.0)},
            open_positions=[pos],
            risk_state=RiskState(RiskLevel.NORMAL, 10150.0, 10150.0, 150.0, 0.0, 0, 1, 0.01, True),
            latest_action="TRAIL",
            latest_reason="strong_trend_continuation",
            daily_pnl_r=1.5,
        )
    )
    assert "GLVNS SQUEEZE TRADE MANAGEMENT ENGINE" in rendered
    assert "R_100" in rendered
    assert "TRAIL" in rendered


@pytest.mark.asyncio
async def test_rich_glvnsorg_card_and_threaded_comments(tmp_path) -> None:
    now = datetime(2026, 1, 1, 13, 15, tzinfo=UTC)
    profile = SymbolProfile(symbol="R_100", description="Volatility 100 Index", slack_channel="C0CJWBD99QD")
    mgmt_cfg = ManagementSettings()
    ms = make_market_state(close=1653.50, val=7.1658, prev_val=3.8587, sqz_fired=True)
    sig = TradeSignal(
        signal_id="sig-rich-1",
        symbol="R_100",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.82,
        confidence=0.78,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=now,
        timeframe_sec=900,
        entry_price=1653.50,
        suggested_stop=1622.00,
        initial_risk_distance=31.50,
        reasons=("squeeze_release_bar", "strong_aligned_momentum"),
    )
    pos = Position(
        trade_id="TRD-RICH1",
        idempotency_key="idem-rich",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.ENTERED,
        entry_price=1653.50,
        current_price=1653.50,
        initial_stop=1622.00,
        current_stop=1622.00,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=20.0,
        initial_risk_amount=100.0,
        initial_risk_distance=31.50,
        opened_at=now,
        last_updated_at=now,
        bars_held=12,
    )

    bundle = build_trade_opened_bundle(pos, sig, ms, profile, mgmt_cfg, risk_pct=0.01)
    assert bundle.title == "🟢 LONG · Volatility 100 Index (R_100) · 🔥 B+ · 78/100"
    assert "### 🎯 Scalp Execution Plan" in bundle.reddit_markdown
    assert "### 🪜 Profit & Protection Ladder" in bundle.reddit_markdown
    assert "### ⚡ 15m Scalp Readout" in bundle.reddit_markdown
    assert "### 🧭 Timeframes" not in bundle.reddit_markdown
    assert "| Milestone |" not in bundle.reddit_markdown
    assert "```" not in bundle.reddit_markdown
    assert "⭐" in bundle.reddit_markdown
    assert "[REDACTED]" not in bundle.reddit_markdown
    assert bundle.slack_blocks[0]["type"] == "header"
    assert len(bundle.slack_blocks[0]["text"]["text"]) <= 150
    assert any(
        "🪜 PROFIT & PROTECTION LADDER" in (blk.get("text") or {}).get("text", "")
        and "```" not in (blk.get("text") or {}).get("text", "")
        for blk in bundle.slack_blocks
    )
    assert all(
        "TIMEFRAMES" not in (blk.get("text") or {}).get("text", "")
        and "```" not in (blk.get("text") or {}).get("text", "")
        for blk in bundle.slack_blocks
    )
    assert len(bundle.slack_blocks) >= 7

    router = NotificationRouter(
        NotificationSettings(slack_enabled=False, reddit_enabled=False, reddit_outbox_dir=str(tmp_path / "reddit"))
    )
    await router.notify_trade_opened(
        trade_id=pos.trade_id,
        channel=profile.slack_channel,
        slack_text=bundle.slack_text,
        slack_blocks=bundle.slack_blocks,
        reddit_title=bundle.title,
        reddit_body=bundle.reddit_markdown,
        reddit_flair=profile.reddit_flair,
    )

    dec = ManagementDecision(
        action=ManagementAction.TRAIL,
        reason="profit_lock_and_trail",
        confidence=0.84,
        risk_state="NORMAL",
        target_stage=TradeStage.TRAILING,
        new_stop_price=1672.16,
        timestamp=now,
    )
    ev = ManagementEvent(
        event_id="ev-rich-1",
        trade_id=pos.trade_id,
        timestamp=now,
        stage_before=TradeStage.PROFIT_PROTECTION,
        stage_after=TradeStage.TRAILING,
        decision=dec,
        current_price=1691.78,
        unrealized_pnl=121.5,
        unrealized_r=1.22,
        mfe_r=1.30,
        mae_r=-0.10,
    )
    mgmt_bundle = build_management_comment_bundle(pos, ev, ms, profile, prev_stop=1653.50)
    assert "📐 TRAILING STOP MOVED" in mgmt_bundle.reddit_markdown
    assert "> 💡" in mgmt_bundle.reddit_markdown
    assert "`1,653.50` ➜ `1,672.16`" in mgmt_bundle.reddit_markdown
    assert "3h 00m" in mgmt_bundle.reddit_markdown
    assert "🎯 **Next:**" in mgmt_bundle.reddit_markdown
    assert "| 📍" not in mgmt_bundle.reddit_markdown
    assert len(mgmt_bundle.slack_blocks) >= 4

    await router.notify_trade_comment(
        dedupe_key="mgmt:TRD-RICH1:trail:12",
        trade_id=pos.trade_id,
        priority=AlertPriority.NOTICE,
        channel=profile.slack_channel,
        thread_ts="1795543168.175089",
        slack_text=mgmt_bundle.slack_text,
        slack_blocks=mgmt_bundle.slack_blocks,
        reddit_post_id=None,
        reddit_comment_body=mgmt_bundle.reddit_markdown,
        label="trail",
    )

    ct = ClosedTrade(
        trade_id=pos.trade_id,
        timestamp=now,
        closed_at=now,
        symbol="R_100",
        direction=TradeDirection.BUY,
        entry=1653.50,
        initial_risk=100.0,
        initial_stop=1622.00,
        final_stop=1707.52,
        exit=1707.52,
        exit_reason=ExitReason.TRAILING_STOP,
        duration_sec=14400.0,
        bars_held=16,
        pnl=171.49,
        r_multiple=1.71,
        mfe=2.10,
        mae=-0.10,
        highest_profit=210.0,
        lowest_drawdown=-10.0,
        squeeze_state_at_entry="SQUEEZE_OFF",
        squeeze_state_at_exit="SQUEEZE_OFF",
        momentum_at_entry=7.16,
        momentum_at_exit=2.10,
        policy=ManagementPolicy.DYNAMIC_MANAGEMENT,
        management_actions=("MOVE_TO_BREAKEVEN", "TRAIL"),
        entry_quality="GOOD_ENTRY",
        management_quality="WELL_MANAGED",
        attribution_summary="GOOD_ENTRY | WELL_MANAGED",
    )
    close_bundle = build_trade_closed_comment_bundle(ct, ms, profile)
    assert "🎉 WIN · +1.71R" in close_bundle.reddit_markdown
    assert "📐 Trailing stop hit" in close_bundle.reddit_markdown
    assert "| 📍" not in close_bundle.reddit_markdown
    assert len(close_bundle.slack_blocks) >= 4
    await router.notify_trade_comment(
        dedupe_key="close:TRD-RICH1",
        trade_id=pos.trade_id,
        priority=AlertPriority.NOTICE,
        channel=profile.slack_channel,
        thread_ts="1795543168.175089",
        slack_text=close_bundle.slack_text,
        slack_blocks=close_bundle.slack_blocks,
        reddit_post_id=None,
        reddit_comment_body=close_bundle.reddit_markdown,
        label="closed",
        reply_broadcast=True,
    )

    t_dir = tmp_path / "reddit" / "TRD-RICH1"
    assert (t_dir / "00_post.md").exists()
    assert (t_dir / "01_comment_trail.md").exists()
    assert (t_dir / "02_comment_closed.md").exists()
    assert "Trailing stop hit" in (t_dir / "thread_preview.md").read_text(encoding="utf-8")
    assert router.slack.sent_messages[-1]["thread_ts"] == "1795543168.175089"
    assert router.slack.sent_messages[-1]["reply_broadcast"] is True
    assert router.slack.sent_messages[-1]["blocks"] is not None


def test_noise_suppression_settings_defaults() -> None:
    cfg = NotificationSettings()
    assert cfg.send_market_pulses is False
    assert cfg.send_unentered_analysis_signals is False
    assert cfg.min_stop_change_r_for_alert == 0.25
    assert cfg.min_tighten_change_r_for_alert == 0.10
    assert cfg.broadcast_management_updates is True
    assert cfg.notify_thesis_warnings is True
    assert cfg.thesis_warning_escalation_only is True
    assert cfg.drawdown_warning_levels_r == [-0.50, -0.75]


@pytest.mark.asyncio
async def test_warning_comment_bundle_and_alert_mention(tmp_path) -> None:
    now = datetime(2026, 1, 1, 13, 15, tzinfo=UTC)
    profile = SymbolProfile(symbol="R_100", description="Volatility 100 Index", slack_channel="C0CJWBD99QD")
    ms = make_market_state(close=98.9, val=-0.6, prev_val=-0.2, momentum_state=MomentumState.WEAK_BEARISH)
    pos = Position(
        trade_id="TRD-WARN-1",
        idempotency_key="idem-warn-1",
        symbol="R_100",
        direction=TradeDirection.BUY,
        stage=TradeStage.INITIAL_PROTECTION,
        entry_price=100.0,
        current_price=98.9,
        initial_stop=98.0,
        current_stop=98.0,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=20.0,
        initial_risk_amount=100.0,
        initial_risk_distance=2.0,
        opened_at=now,
        last_updated_at=now,
        bars_held=3,
        mfe_r=0.20,
        mae_r=-0.55,
    )
    bundle = build_position_warning_comment_bundle(
        pos,
        ms,
        profile,
        warning_kind="DRAWDOWN_WARNING",
        headline="⚠️ RISK WARNING · DRAWDOWN",
        note="Position moved against entry. Watch protective stop and momentum closely.",
    )
    assert bundle.slack_blocks[0]["type"] == "section"
    assert "⚠️ RISK WARNING · DRAWDOWN" in bundle.slack_blocks[0]["text"]["text"]
    assert "⚠️ RISK WARNING · DRAWDOWN" in bundle.reddit_markdown
    combined_text = (
        bundle.slack_text
        + "\n"
        + bundle.reddit_markdown
        + "\n"
        + str(bundle.slack_blocks)
    ).lower()
    for forbidden in ("stop loss", "closed", "stop hit"):
        assert forbidden not in combined_text

    router = NotificationRouter(
        NotificationSettings(
            slack_enabled=False,
            reddit_enabled=False,
            reddit_outbox_dir=str(tmp_path / "reddit"),
            slack_alert_mention="<!here>",
            reddit_alert_mention="u/Gelvinus",
        )
    )
    await router.notify_trade_comment(
        dedupe_key="warn:TRD-WARN-1:dd:-0.50",
        trade_id=pos.trade_id,
        priority=AlertPriority.WARNING,
        channel=profile.slack_channel,
        thread_ts="1795543168.175089",
        slack_text=bundle.slack_text,
        slack_blocks=bundle.slack_blocks,
        reddit_post_id=None,
        reddit_comment_body=bundle.reddit_markdown,
        label="risk_warning",
        reply_broadcast=True,
    )
    sent_warn = router.slack.sent_messages[-1]
    assert sent_warn["text"].startswith("<!here> ")
    assert sent_warn["reply_broadcast"] is True
    assert sent_warn["blocks"][0]["type"] == "section"
    assert sent_warn["blocks"][-1]["type"] == "context"
    assert "<!here>" in sent_warn["blocks"][-1]["elements"][0]["text"]

    await router.notify_trade_comment(
        dedupe_key="mgmt:TRD-WARN-1:be",
        trade_id=pos.trade_id,
        priority=AlertPriority.NOTICE,
        channel=profile.slack_channel,
        thread_ts="1795543168.175089",
        slack_text="🛡️ BREAKEVEN SECURED · R_100",
        slack_blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "*🛡️ BREAKEVEN SECURED*"}}],
        reddit_post_id=None,
        reddit_comment_body="Breakeven secured",
        label="move_to_breakeven",
        reply_broadcast=True,
    )
    sent_notice = router.slack.sent_messages[-1]
    assert "<!here>" not in sent_notice["text"]
    assert len(sent_notice["blocks"]) == 1

    warn_comment = (tmp_path / "reddit" / "TRD-WARN-1" / "01_comment_risk_warning.md").read_text(encoding="utf-8")
    notice_comment = (tmp_path / "reddit" / "TRD-WARN-1" / "02_comment_move_to_breakeven.md").read_text(encoding="utf-8")
    assert "🔔 *Alert:* u/Gelvinus" in warn_comment
    assert "🔔 *Alert:* u/Gelvinus" in notice_comment


def test_volatility_1s_vs_standard_index_disambiguation() -> None:
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    prof_1s = SymbolProfile(symbol="1HZ15V", description="Volatility 15 Index")
    ms_1s = make_market_state(symbol="1HZ15V", close=2450.0, val=1.8, prev_val=1.2)
    sig_1s = TradeSignal(
        signal_id="sig-1hz15v",
        symbol="1HZ15V",
        direction=TradeDirection.BUY,
        signal_type=SignalType.NEW_OPPORTUNITY,
        squeeze_state=SqueezeState.SQUEEZE_OFF,
        momentum_state=MomentumState.STRONG_BULLISH,
        momentum_strength=0.8,
        confidence=0.82,
        regime=MarketRegime.EXPANSION_TREND,
        timestamp=now,
        timeframe_sec=900,
        entry_price=2450.0,
        suggested_stop=2430.0,
        initial_risk_distance=20.0,
    )
    pos_1s = Position(
        trade_id="TRD-1HZ15V",
        idempotency_key="idem-1hz15v",
        symbol="1HZ15V",
        direction=TradeDirection.BUY,
        stage=TradeStage.ENTERED,
        entry_price=2450.0,
        current_price=2450.0,
        initial_stop=2430.0,
        current_stop=2430.0,
        take_profit=None,
        stake=100.0,
        remaining_fraction=1.0,
        multiplier=40.0,
        initial_risk_amount=100.0,
        initial_risk_distance=20.0,
        opened_at=now,
        last_updated_at=now,
    )
    opened = build_trade_opened_bundle(pos_1s, sig_1s, ms_1s, prof_1s, ManagementSettings(), risk_pct=0.01)
    assert "Volatility 15 (1s) Index (1HZ15V)" in opened.title
    assert "Volatility 15 (1s) Index (1HZ15V)" in opened.slack_blocks[0]["text"]["text"]
    assert "Volatility 15 (1s) Index (1HZ15V)" in opened.slack_text

    warn = build_position_warning_comment_bundle(
        pos_1s,
        ms_1s,
        prof_1s,
        warning_kind="MOMENTUM_WEAKENING",
        headline="🟡 TREND WEAKENING · MOMENTUM FADING",
        note="Histogram shifted from bright to dark.",
    )
    assert "Volatility 15 (1s) Index (`1HZ15V`)" in warn.reddit_markdown
    assert "Volatility 15 (1s) Index (1HZ15V)" in warn.slack_text
