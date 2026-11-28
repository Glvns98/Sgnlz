"""Uniform, engaging, emoji-rich signal cards and threaded lifecycle comment formatters for Slack & Reddit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.config.settings import ManagementSettings, SymbolProfile
from bot.core.enums import (
    ExitReason,
    ManagementAction,
    MomentumTransition,
    SqueezeState,
    TradeDirection,
)
from bot.core.models import (
    ClosedTrade,
    ManagementEvent,
    MarketStateSnapshot,
    Position,
    TradeSignal,
)
from bot.deriv.discovery import normalize_symbol_description


def _symbol_label(symbol: str, description: str) -> str:
    desc = normalize_symbol_description(symbol, description)
    return f"{desc} (`{symbol}`)" if desc.upper() != symbol.upper() else f"`{symbol}`"


def _symbol_plain(symbol: str, description: str) -> str:
    desc = normalize_symbol_description(symbol, description)
    return f"{desc} ({symbol})" if desc.upper() != symbol.upper() else symbol


@dataclass(frozen=True)
class TradeOpenedBundle:
    title: str
    reddit_markdown: str
    slack_text: str
    slack_blocks: list[dict[str, Any]]


@dataclass(frozen=True)
class CommentBundle:
    reddit_markdown: str
    slack_text: str
    slack_blocks: list[dict[str, Any]]


def score_and_grade(confidence: float) -> tuple[int, str]:
    score = max(0, min(100, round(confidence * 100)))
    if score >= 90:
        return score, "A+"
    if score >= 82:
        return score, "A"
    if score >= 74:
        return score, "B+"
    if score >= 66:
        return score, "B"
    if score >= 58:
        return score, "C+"
    return score, "C"


def _grade_badge(grade: str) -> str:
    return {
        "A+": "💎 A+",
        "A": "🏆 A",
        "B+": "🔥 B+",
        "B": "⚡ B",
        "C+": "✨ C+",
        "C": "🎯 C",
    }.get(grade, f"🎯 {grade}")


def _confidence_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    return f"{'🟩' * filled}{'⬜' * (10 - filled)} {score}/100"


def _stars(score: int) -> str:
    n = max(1, min(5, round(score / 20)))
    return "⭐" * n + "☆" * (5 - n)


def _r_meter(r: float, cap: float = 2.0, width: int = 10) -> str:
    filled = max(0, min(width, round(max(r, 0.0) / cap * width)))
    return "▰" * filled + "▱" * (width - filled)


def _duration(minutes: int) -> str:
    mins = max(0, int(minutes))
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h {m:02d}m"


def _regime_badge(regime: str) -> str:
    return {
        "EXPANSION_TREND": "🚀 Expansion Trend",
        "COMPRESSION": "🫧 Tight Squeeze Coil",
        "MOMENTUM_EXHAUSTION": "🌙 Late-Cycle Momentum",
        "CHOP": "🌊 Choppy Range",
        "ABNORMAL_VOLATILITY": "⚡ Volatility Spike",
        "WARMUP": "⏳ Warming Up",
    }.get(regime, regime)


def _bcolor_badge(bcolor: str | None) -> str:
    return {
        "lime": "🟢 Lime (Surging)",
        "green": "🌿 Green (Cooling)",
        "red": "🔴 Red (Surging Down)",
        "maroon": "🍷 Maroon (Easing)",
    }.get(str(bcolor), "⚪ Neutral")


def _scolor_badge(scolor: str | None) -> str:
    return {
        "black": "⚫ Squeeze ON",
        "gray": "⚪ Squeeze Released",
        "blue": "🔵 Transition",
    }.get(str(scolor), "⚪ Neutral")


def format_price(value: float) -> str:
    abs_v = abs(value)
    if abs_v >= 100.0:
        return f"{value:,.2f}"
    if abs_v >= 1.0:
        return f"{value:,.4f}"
    return f"{value:,.5f}"


def format_timeframe(sec: int | None) -> str:
    if sec is None:
        return "N/A"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}H"
    return f"{sec // 86400}D"


def _dir_meta(direction: TradeDirection) -> tuple[str, str]:
    if direction == TradeDirection.BUY:
        return "🟢", "LONG"
    return "🔴", "SHORT"


def _price_at_r(entry: float, risk_dist: float, direction: TradeDirection, r_mult: float) -> float:
    offset = risk_dist * r_mult
    return (entry + offset) if direction == TradeDirection.BUY else (entry - offset)


def _offset_str(price: float, entry: float) -> str:
    diff = price - entry
    sign = "+" if diff >= 0 else "-"
    return f"{sign}{format_price(abs(diff))}"


def _next_milestone(position: Position, management_cfg: ManagementSettings) -> str:
    r = max(position.unrealized_r(), position.mfe_r)
    entry = position.entry_price
    dist = position.initial_risk_distance
    direction = position.direction

    if r < management_cfg.breakeven_trigger_r:
        trig = management_cfg.breakeven_trigger_r
        px = format_price(_price_at_r(entry, dist, direction, trig))
        return f"🔐 Breakeven at `{px}` (+{trig:.2f}R)"
    if r < management_cfg.profit_lock_trigger_r:
        trig = management_cfg.profit_lock_trigger_r
        px = format_price(_price_at_r(entry, dist, direction, trig))
        return f"🔒 Profit Lock at `{px}` (+{trig:.2f}R)"
    if r < management_cfg.strong_profit_trigger_r:
        trig = management_cfg.strong_profit_trigger_r
        px = format_price(_price_at_r(entry, dist, direction, trig))
        return f"📐 Dynamic Trail at `{px}` (+{trig:.2f}R)"
    if r < management_cfg.runner_trigger_r:
        trig = management_cfg.runner_trigger_r
        px = format_price(_price_at_r(entry, dist, direction, trig))
        return f"🚀 Runner at `{px}` (+{trig:.2f}R)"
    return "🏃 Runner mode: trailing dynamically"


_ACTION_META: dict[ManagementAction, tuple[str, str, str]] = {
    ManagementAction.MOVE_TO_BREAKEVEN: (
        "🛡️",
        "BREAKEVEN SECURED",
        "Risk is off the table: stop moved to entry. 🙌",
    ),
    ManagementAction.LOCK_PROFIT: (
        "🔒",
        "PROFIT LOCKED",
        "Minimum `{locked:+.2f}R` is now protected, whatever happens next. 💰",
    ),
    ManagementAction.TRAIL: (
        "📐",
        "TRAILING STOP MOVED",
        "Momentum is still in control. Riding the wave while locking `{locked:+.2f}R`. 🌊",
    ),
    ManagementAction.TIGHTEN_STOP: (
        "⚡",
        "STOP TIGHTENED",
        "Momentum is softening, so protection was tightened to guard capital. 🛡️",
    ),
    ManagementAction.REDUCE: (
        "✂️",
        "PARTIAL TAKEN",
        "Trimmed `{pct:.0f}%` of exposure to bank progress. The rest stays on. 💵",
    ),
}

_EXIT_LABEL: dict[ExitReason, str] = {
    ExitReason.TRAILING_STOP: "📐 Trailing stop hit",
    ExitReason.PROFIT_LOCK_STOP: "🔒 Profit-lock stop hit",
    ExitReason.BREAKEVEN_STOP: "🛡️ Breakeven stop hit",
    ExitReason.STOP_LOSS: "🛑 Initial stop hit",
    ExitReason.MOMENTUM_DETERIORATION: "📉 Momentum faded, exited early",
    ExitReason.THESIS_INVALIDATION: "⚠️ Thesis invalidated",
    ExitReason.TIME_STAGNATION: "⏱️ Went nowhere, capital freed",
    ExitReason.ABNORMAL_REGIME: "🚨 Volatility spike exit",
    ExitReason.HARD_INVALIDATION: "🚨 Emergency exit",
    ExitReason.TAKE_PROFIT: "🎯 Target reached",
}


def _confluence_checks(
    direction: TradeDirection,
    state: MarketStateSnapshot,
    signal: TradeSignal,
) -> tuple[list[str], list[str]]:
    is_buy = direction == TradeDirection.BUY
    passed: list[str] = []
    missed: list[str] = []

    checks = [
        (
            "Squeeze Expansion Active (⚪ Gray Cross)",
            state.squeeze.sqz_fired or state.squeeze_state == SqueezeState.SQUEEZE_OFF,
        ),
        (
            "Early Trend Trigger Fired (🔥 Fire / ⚡ Flip / 🔁 Bounce)",
            any(
                r in signal.reasons
                for r in (
                    "squeeze_fire_early_entry",
                    "zero_line_flip_early_entry",
                    "zero_line_bounce_resumption",
                    "squeeze_release_bar",
                )
            ),
        ),
        (
            "Momentum Accelerating (🟩 Lime / 🟥 Red)",
            (
                is_buy
                and (
                    state.squeeze.bcolor == "lime"
                    or state.has_transition(MomentumTransition.BULLISH_ACCELERATION)
                )
            )
            or (
                (not is_buy)
                and (
                    state.squeeze.bcolor == "red"
                    or state.has_transition(MomentumTransition.BEARISH_ACCELERATION)
                )
            ),
        ),
        (
            "Trigger Candle Confirms Direction",
            state.structure_bullish_confirmed if is_buy else state.structure_bearish_confirmed,
        ),
        (
            "Fresh Unexhausted Move (No Late Chasing)",
            state.accel_run_bars <= 2,
        ),
        (
            "Clean Volatility & Structural Stop",
            state.regime.value != "ABNORMAL_VOLATILITY" and signal.confidence >= 0.58,
        ),
    ]
    for label, ok in checks:
        if ok:
            passed.append(label)
        else:
            missed.append(label)
    return passed, missed


def build_trade_opened_bundle(
    position: Position,
    signal: TradeSignal,
    market_state: MarketStateSnapshot,
    symbol_cfg: SymbolProfile,
    management_cfg: ManagementSettings,
    risk_pct: float,
    symbol_history_trades: list[ClosedTrade] | None = None,
) -> TradeOpenedBundle:
    emoji, dir_label = _dir_meta(position.direction)
    desc = normalize_symbol_description(position.symbol, symbol_cfg.description or position.symbol)
    score, grade = score_and_grade(signal.confidence)
    grade_badge = _grade_badge(grade)
    stars = _stars(score)
    regime_badge = _regime_badge(market_state.regime.value)
    tf_label = format_timeframe(symbol_cfg.primary_timeframe)
    utc_str = position.opened_at.strftime("%H:%M UTC")
    risk_dist = position.initial_risk_distance
    entry = position.entry_price
    sl = position.initial_stop
    sl_pct = (risk_dist / max(entry, 1e-9)) * 100.0

    be_px = _price_at_r(entry, risk_dist, position.direction, management_cfg.breakeven_trigger_r)
    lock_px = _price_at_r(entry, risk_dist, position.direction, management_cfg.profit_lock_trigger_r)
    trail_px = _price_at_r(entry, risk_dist, position.direction, management_cfg.strong_profit_trigger_r)
    runner_px = _price_at_r(entry, risk_dist, position.direction, management_cfg.runner_trigger_r)

    title = f"{emoji} {dir_label} · {desc} ({position.symbol}) · {grade_badge} · {score}/100"

    passed, missed = _confluence_checks(position.direction, market_state, signal)
    total_checks = len(passed) + len(missed)

    hist = [
        t
        for t in (symbol_history_trades or [])
        if t.symbol == position.symbol and t.direction == position.direction
    ]
    if hist:
        wins = sum(1 for t in hist if t.r_multiple > 0)
        wr = (wins / len(hist)) * 100.0
        avg_r = sum(t.r_multiple for t in hist) / len(hist)
        edge_badge = "✅ Validated Edge" if (len(hist) >= 5 and avg_r > 0) else "🌱 Early Sample"
        track_line = f"{len(hist)} trades | Win {wr:.0f}% | Avg {avg_r:+.2f}R | {edge_badge}"
    else:
        track_line = "First recorded 15m setup for this pair/direction"

    snap = market_state.squeeze
    sqz_desc = (
        f"💥 Fired ({snap.squeeze_bars} bars coiled)"
        if snap.sqz_fired
        else f"{market_state.squeeze_state.value.replace('_', ' ').title()} (BB/KC {snap.bb_kc_ratio:.2f})"
    )
    bcolor_txt = _bcolor_badge(snap.bcolor)
    scolor_txt = _scolor_badge(snap.scolor)
    setup_label = (
        "💥 15m Squeeze Breakout"
        if signal.signal_type.value == "NEW_OPPORTUNITY"
        else "🚀 15m Momentum Continuation"
    )

    ladder_plain_lines = [
        f"🔐 *Breakeven* (+{management_cfg.breakeven_trigger_r:.2f}R) ➜ `{format_price(be_px)}` · Move stop to entry (+{management_cfg.breakeven_buffer_r:.2f}R)",
        f"🔒 *Profit Lock* (+{management_cfg.profit_lock_trigger_r:.2f}R) ➜ `{format_price(lock_px)}` · Lock +{management_cfg.profit_lock_floor_r:.2f}R floor",
        f"📐 *Dynamic Trail* (+{management_cfg.strong_profit_trigger_r:.2f}R) ➜ `{format_price(trail_px)}` · Trail momentum wave",
        f"🚀 *Runner Mode* (+{management_cfg.runner_trigger_r:.2f}R) ➜ `{format_price(runner_px)}` · Let trend run free",
    ]
    ladder_slack = "\n".join(ladder_plain_lines)
    ladder_reddit = "\n".join(line.replace("*", "**") for line in ladder_plain_lines)

    why_lines_slack = [f"✅ {item}" for item in passed[:4]]
    if not why_lines_slack:
        why_lines_slack = ["✅ Aligned 15m Squeeze momentum"]
    why_lines_reddit = "\n".join(f"- {line}" for line in why_lines_slack)

    header_plain = f"{emoji} {dir_label}  ·  {desc} ({position.symbol})  ·  {tf_label} Scalp"[:148]
    squeeze_summary = (
        f"{scolor_txt}  ·  {bcolor_txt}  ·  `val` `{snap.val:+.4f}`  ·  "
        f"Strength `{snap.momentum_strength:.2f}`  ·  {sqz_desc}"
    )

    reddit_md = (
        f"## {emoji} {dir_label} · {desc} (`{position.symbol}`)\n"
        f"🏷️ `{position.symbol}` · ⚡ `{tf_label} Scalp` · {symbol_cfg.reddit_flair} · ⏰ {utc_str} · 🆔 `{position.trade_id}`\n\n"
        f"**{grade_badge} {stars} · {score}/100** · {regime_badge} · {setup_label}\n\n"
        "---\n\n"
        "### 🎯 Scalp Execution Plan\n\n"
        f"- 📍 **Entry:** `{format_price(entry)}`\n"
        f"- 🛡️ **Stop Loss:** `{format_price(sl)}` ({sl_pct:.2f}% risk width)\n"
        f"- ⚖️ **Position Risk:** `{risk_pct * 100:.2f}%` equity\n"
        f"- ⏱️ **Timeframe & Monitor:** `{tf_label}` pure squeeze · Monitored live until close\n\n"
        "### 🪜 Profit & Protection Ladder\n\n"
        f"{ladder_reddit}\n\n"
        f"### ⚡ 15m Scalp Readout ({len(passed)}/{total_checks} checks)\n\n"
        f"{squeeze_summary}\n\n"
        f"{why_lines_reddit}\n\n"
        f"📒 **Track Record:** {track_line}\n\n"
        "---\n\n"
        "💬 *Follow this post: I monitor this scalp continuously until close and broadcast updates on key moves (🛡️ BE · 🔒 Lock · 📐 Trail · ⚡ Tighten · ⚠️ Risk · ✂️ Trim · 🏁 Exit).* ⚠️ *Not financial advice.*"
    )

    slack_blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_plain, "emoji": True}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"🏷️ `{position.symbol}`  ·  ⚡ `{tf_label} Scalp`  ·  {symbol_cfg.reddit_flair}  ·  "
                        f"⏰ {utc_str}  ·  🆔 `{position.trade_id}`"
                    ),
                }
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{grade_badge}  {stars}  ·  *{score}/100*\n{regime_badge}  ·  {setup_label}"[:2900],
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎯 SCALP EXECUTION PLAN*"},
            "fields": [
                {"type": "mrkdwn", "text": f"*📍 Entry*\n`{format_price(entry)}`"},
                {"type": "mrkdwn", "text": f"*🛡️ Stop Loss*\n`{format_price(sl)}` ({sl_pct:.2f}%)"},
                {"type": "mrkdwn", "text": f"*⚖️ Risk*\n`{risk_pct * 100:.2f}%` equity"},
                {"type": "mrkdwn", "text": f"*⏱️ Monitor*\n`{tf_label}` · Live until close"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🪜 PROFIT & PROTECTION LADDER*\n{ladder_slack}"[:2900],
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*⚡ 15m SCALP READOUT*  ({len(passed)}/{total_checks} checks)\n"
                    f"{squeeze_summary}\n" + "\n".join(why_lines_slack)
                )[:2900],
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "💬 *Follow this thread*: monitored live until close · broadcasts on 🛡️ BE · 🔒 Lock · 📐 Trail · ⚡ Tighten · ⚠️ Risk · ✂️ Trim · 🏁 Exit",
                },
                {
                    "type": "mrkdwn",
                    "text": f"📒 {track_line}  ·  ⚠️ Not financial advice",
                },
            ],
        },
    ]

    slack_text = (
        f"{emoji} {dir_label} · {desc} ({position.symbol}) ({tf_label} Scalp) · {grade_badge} · "
        f"Entry {format_price(entry)} · SL {format_price(sl)}"
    )
    return TradeOpenedBundle(
        title=title,
        reddit_markdown=reddit_md,
        slack_text=slack_text,
        slack_blocks=slack_blocks,
    )


def build_management_comment_bundle(
    position: Position,
    event: ManagementEvent,
    market_state: MarketStateSnapshot,
    symbol_cfg: SymbolProfile,
    prev_stop: float,
    management_cfg: ManagementSettings | None = None,
) -> CommentBundle:
    cfg = management_cfg or ManagementSettings()
    emoji, dir_label = _dir_meta(position.direction)
    action = event.decision.action
    new_stop = (
        event.decision.new_stop_price
        if event.decision.new_stop_price is not None
        else position.current_stop
    )
    locked = position.r_for_price(new_stop)
    cur = event.unrealized_r
    tf_min = max(1, symbol_cfg.primary_timeframe // 60)
    dur_str = _duration(position.bars_held * tf_min)
    meter = _r_meter(cur)
    next_str = _next_milestone(position, cfg)

    icon, action_title, note_tpl = _ACTION_META.get(
        action,
        ("🔄", action.value, "Position stage updated to `{stage}`."),
    )
    note = note_tpl.format(
        locked=locked,
        pct=event.decision.reduce_fraction * 100.0,
        stage=event.stage_after.value,
    )

    old_px = format_price(prev_stop)
    new_px = format_price(new_stop)
    entry_px = format_price(position.entry_price)
    cur_px = format_price(event.current_price)
    sym_md = _symbol_label(position.symbol, symbol_cfg.description)
    sym_plain = _symbol_plain(position.symbol, symbol_cfg.description)

    reddit_md = (
        f"### {icon} {action_title} · {emoji} {dir_label} · {sym_md}\n"
        f"`{meter}` **{cur:+.2f}R**\n\n"
        f"> 💡 {note}\n\n"
        f"- 📍 **Entry:** `{entry_px}`  ·  💹 **Now:** `{cur_px}` (`{cur:+.2f}R`)\n"
        f"- 🛡️ **Stop Protection:** `{old_px}` ➜ `{new_px}`  ·  💰 **Locked:** `{locked:+.2f}R`\n"
        f"- ⏱️ **Elapsed:** {dur_str}  ·  📈 **Peak:** `{event.mfe_r:+.2f}R`  ·  🎯 **Next:** {next_str}"
    )

    slack_blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{icon} {action_title}*  ·  {emoji} {dir_label} · {sym_md}\n"
                    f"`{meter}`  *{cur:+.2f}R*"
                )[:2900],
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"> 💡 {note}"[:2900]},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*📍 Entry*\n`{entry_px}`"},
                {"type": "mrkdwn", "text": f"*💹 Now*\n`{cur_px}` ({cur:+.2f}R)"},
                {"type": "mrkdwn", "text": f"*🛡️ Stop*\n`{old_px}` ➜ `{new_px}`"},
                {"type": "mrkdwn", "text": f"*💰 Locked*\n`{locked:+.2f}R`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"⏱️ {dur_str}  ·  📈 Peak `{event.mfe_r:+.2f}R`  ·  "
                        f"🎯 Next: {next_str}  ·  🆔 `{position.trade_id}`"
                    ),
                }
            ],
        },
    ]

    slack_text = f"{icon} {action_title} · {sym_plain} · Stop {old_px} ➜ {new_px} · {cur:+.2f}R"
    return CommentBundle(reddit_markdown=reddit_md, slack_text=slack_text, slack_blocks=slack_blocks)


def build_position_warning_comment_bundle(
    position: Position,
    market_state: MarketStateSnapshot,
    symbol_cfg: SymbolProfile,
    *,
    warning_kind: str,
    headline: str,
    note: str,
    detail: str = "",
) -> CommentBundle:
    """Format an early-warning position comment without matching final-exit text."""
    emoji, dir_label = _dir_meta(position.direction)
    cur = position.unrealized_r()
    stop_r = position.r_for_price(position.current_stop)
    remaining_to_stop_r = max(0.0, cur - stop_r)
    tf_min = max(1, symbol_cfg.primary_timeframe // 60)
    dur_str = _duration(position.bars_held * tf_min)

    entry_px = format_price(position.entry_price)
    cur_px = format_price(position.current_price)
    stop_px = format_price(position.current_stop)
    sym_md = _symbol_label(position.symbol, symbol_cfg.description)
    sym_plain = _symbol_plain(position.symbol, symbol_cfg.description)
    detail_line = detail.strip() or (
        f"Kind: {warning_kind} · Squeeze: {market_state.squeeze_state.value} · "
        f"Momentum: {market_state.momentum_state.value}"
    )

    reddit_md = (
        f"### {headline} · {emoji} {dir_label} · {sym_md}\n"
        f"📉 **Live PnL:** `{cur:+.2f}R`  ·  📏 **Buffer to Stop:** `{remaining_to_stop_r:.2f}R`\n\n"
        f"> 💡 {note}\n"
        f"> {detail_line}\n\n"
        f"- 📍 **Entry:** `{entry_px}`  ·  💹 **Now:** `{cur_px}` (`{cur:+.2f}R`)\n"
        f"- 🛡️ **Stop:** `{stop_px}` (`{stop_r:+.2f}R`)  ·  📏 **Buffer to Stop:** `{remaining_to_stop_r:.2f}R`\n"
        f"- ⏱️ **Elapsed:** {dur_str}  ·  📈 **Peak:** `{position.mfe_r:+.2f}R`  ·  📉 **Dip:** `{position.mae_r:+.2f}R`"
    )

    note_block_text = f"> 💡 {note}"
    if detail_line:
        note_block_text = f"{note_block_text}\n{detail_line}"

    slack_blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{headline}*  ·  {emoji} {dir_label} · {sym_md}\n"
                    f"📉 Live PnL *{cur:+.2f}R*  ·  📏 Buffer to Stop `{remaining_to_stop_r:.2f}R`"
                )[:2900],
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": note_block_text[:2900]},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*📍 Entry*\n`{entry_px}`"},
                {"type": "mrkdwn", "text": f"*💹 Now*\n`{cur_px}` ({cur:+.2f}R)"},
                {"type": "mrkdwn", "text": f"*🛡️ Stop*\n`{stop_px}` ({stop_r:+.2f}R)"},
                {"type": "mrkdwn", "text": f"*📏 Buffer to Stop*\n`{remaining_to_stop_r:.2f}R`"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"⏱️ {dur_str}  ·  📈 Peak `{position.mfe_r:+.2f}R`  ·  "
                        f"📉 Dip `{position.mae_r:+.2f}R`  ·  🆔 `{position.trade_id}`"
                    ),
                }
            ],
        },
    ]

    slack_text = (
        f"{headline} · {sym_plain} · {cur:+.2f}R · "
        f"Stop {stop_px} ({remaining_to_stop_r:.2f}R buffer)"
    )
    return CommentBundle(reddit_markdown=reddit_md, slack_text=slack_text, slack_blocks=slack_blocks)


def build_trade_closed_comment_bundle(
    trade: ClosedTrade,
    market_state: MarketStateSnapshot,
    symbol_cfg: SymbolProfile,
) -> CommentBundle:
    emoji, dir_label = _dir_meta(trade.direction)
    tf_min = max(1, symbol_cfg.primary_timeframe // 60)
    minutes = max(round(trade.duration_sec / 60), trade.bars_held * tf_min)
    dur_str = _duration(minutes)
    giveback = max(0.0, trade.mfe - trade.r_multiple)

    r = trade.r_multiple
    if r > 0.05:
        headline = f"🎉 WIN · {r:+.2f}R"
        note = "Clean execution: profits banked. 🥂"
    elif abs(r) <= 0.05:
        headline = f"😌 SCRATCH · {r:+.2f}R"
        note = "No harm done: capital protected. 🧘"
    else:
        headline = f"🛡️ LOSS CAPPED · {r:+.2f}R"
        note = "Risk capped exactly as planned. Discipline over emotion. 💪"

    exit_label = _EXIT_LABEL.get(trade.exit_reason, "🏁 Closed")
    entry_px = format_price(trade.entry)
    exit_px = format_price(trade.exit)
    sym_md = _symbol_label(trade.symbol, symbol_cfg.description)
    sym_plain = _symbol_plain(trade.symbol, symbol_cfg.description)

    reddit_md = (
        f"### {headline} · {emoji} {dir_label} · {sym_md}\n"
        f"{exit_label}\n\n"
        f"> 💡 {note}\n\n"
        f"- 📍 **Entry ➜ 🏁 Exit:** `{entry_px}` ➜ `{exit_px}`\n"
        f"- 💰 **Result:** **{r:+.2f}R**  ·  📈 **Peak / 📉 Dip:** `{trade.mfe:+.2f}R` / `{trade.mae:+.2f}R`\n"
        f"- ⏱️ **Duration:** {dur_str} ({trade.bars_held} bars)  ·  📉 **Giveback:** `{giveback:.2f}R`\n\n"
        f"🏁 *Trade complete · Quality `{trade.entry_quality}` / `{trade.management_quality}`*"
    )

    slack_blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{headline}*  ·  {emoji} {dir_label} · {sym_md}\n{exit_label}"[:2900],
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"> 💡 {note}"[:2900]},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*📍 Entry ➜ 🏁 Exit*\n`{entry_px}` ➜ `{exit_px}`"},
                {"type": "mrkdwn", "text": f"*💰 Result*\n*{r:+.2f}R*"},
                {"type": "mrkdwn", "text": f"*📈 Peak / 📉 Dip*\n`{trade.mfe:+.2f}R` / `{trade.mae:+.2f}R`"},
                {"type": "mrkdwn", "text": f"*⏱️ Duration*\n{dur_str} ({trade.bars_held} bars)"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"🏁 Trade complete  ·  Gave back `{giveback:.2f}R` from peak  ·  "
                        f"Quality `{trade.entry_quality}` / `{trade.management_quality}`  ·  🆔 `{trade.trade_id}`"
                    ),
                }
            ],
        },
    ]

    slack_text = f"{headline} · {sym_plain} · {exit_label}"
    return CommentBundle(reddit_markdown=reddit_md, slack_text=slack_text, slack_blocks=slack_blocks)
