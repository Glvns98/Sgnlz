import { MARKET_CHANNELS, getChannelDef } from '../shared/channels.js';
import type {
  BridgeOverviewDto,
  CalcResultDto,
  ChannelViewDto,
  OpenTradeDto,
  PulseItemDto,
  ScanResultDto,
  SignalDto,
  SummaryRowDto,
  TradeEventDto,
} from '../shared/types.js';

function fmtPx(value: number, decimals: number): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtSigned(value: number, decimals: number): string {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${fmtPx(value, decimals)}`;
}

function fmtUtc(epoch: number): string {
  const d = new Date(epoch * 1000);
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm} UTC`;
}

function fmtPf(pf: number | null, inf: boolean): string {
  if (inf) return '∞';
  if (pf === null || pf === undefined) return '—';
  return pf.toFixed(2);
}

function biasArrow(bias: string): string {
  if (bias === 'Bullish') return '▲';
  if (bias === 'Bearish') return '▼';
  return '◆';
}

export function formatOpenTradePost(t: OpenTradeDto): { title: string; body: string } {
  const dot = t.direction === 'LONG' ? '🟢' : '🔴';
  const ch = getChannelDef(t.channel_name);
  const dec = t.decimals;
  const strat = t.bar_seconds >= 3600 ? 'v3 1h' : t.bar_seconds >= 1800 ? 'v3 30m' : t.bar_seconds >= 900 ? 'v2 15m' : 'v1 5m';
  const stageTxt =
    t.stage >= 2
      ? `TP2 Banked (+${t.banked.toFixed(2)}R)`
      : t.stage === 1
        ? `TP1 Banked (+${t.banked.toFixed(2)}R)`
        : t.be_locked
          ? 'Breakeven Locked'
          : 'Active (Stage 0)';
  const title = `${dot} ${t.direction} · ${t.name} (${t.symbol}) · Grade ${t.grade || 'B+'} · ${strat} Live Signal`.slice(
    0,
    295
  );
  const body = [
    `**${ch.flairText}** · \`${t.symbol}\` · ${t.regime_badge} · **Status:** ${stageTxt}`,
    '',
    '### 📋 Active Trade Plan',
    '',
    '| Level | Price | Target |',
    '|---|---:|---|',
    `| **Entry** | \`${fmtPx(t.entry, dec)}\` | Market spot close |`,
    `| **Active Stop Loss** | \`${fmtPx(t.active_sl, dec)}\` | Initial SL \`${fmtPx(t.sl, dec)}\` |`,
    `| **TP1** | \`${fmtPx(t.tp1, dec)}\` | \`${t.r1.toFixed(2)}R\` (Bank partial, SL → BE) |`,
    `| **TP2** | \`${fmtPx(t.tp2, dec)}\` | \`${t.r2.toFixed(2)}R\` (Bank partial, SL → TP1) |`,
    `| **TP3** | \`${fmtPx(t.tp3, dec)}\` | \`${t.r3.toFixed(2)}R\` (ATR trailing runner) |`,
    '',
    `- **Progress:** Bar \`${t.bars}/${t.horizon}\` · **MFE:** \`${t.max_favorable_r >= 0 ? '+' : ''}${t.max_favorable_r.toFixed(2)}R\` · **MAE:** \`${t.max_adverse_r >= 0 ? '+' : ''}${t.max_adverse_r.toFixed(2)}R\``,
    '',
    '---',
    `*Live monitoring is ON: TP1/TP2/TP3, SL→BE, trailing stops, and exits will be commented directly below this post. Comment \`!calc ${t.symbol} 1000 1\` for exact position sizing or \`!scan ${t.symbol}\` for a live refresh. Not financial advice.*`,
  ].join('\n');
  return { title, body };
}

export function formatSignalPostTitle(sig: SignalDto): string {
  const dot = sig.direction === 'LONG' ? '🟢' : '🔴';
  const strat =
    sig.strategy_mode === 'v3_regime'
      ? `v3 ${sig.timeframe}`
      : sig.strategy_mode === 'v2_structure'
        ? 'v2 15m'
        : 'v1 5m';
  const raw = `${dot} ${sig.direction} · ${sig.name} (${sig.symbol}) · Grade ${sig.grade} · Score ${Math.round(sig.score)}/100 · ${strat}`;
  return raw.slice(0, 295);
}

export function formatSignalPostBody(sig: SignalDto): string {
  const { plan, adaptation: ad, decimals: dec } = sig;
  const ch = getChannelDef(sig.channel_name);
  const [r1, r2, r3] = plan.tp_r;
  const p1Pct = Math.round(ad.partial_tp1 * 100);
  const p2Pct = Math.round((1 - ad.partial_tp1) * 50);
  const remPct = Math.max(0, 100 - p1Pct - p2Pct);
  const riskPct = plan.entry ? (plan.risk / plan.entry) * 100 : 0;
  const atrMult = plan.atr ? plan.risk / plan.atr : 0;
  const edgeBadge =
    sig.edge_status === 'validated'
      ? '✅ Proven Net-of-Cost Edge'
      : sig.edge_status === 'negative'
        ? '🚫 Negative Historical Edge'
        : '⚠️ Unvalidated Edge';
  const stratTag =
    sig.strategy_mode === 'v3_regime'
      ? `Strategy v3 (${sig.timeframe.toUpperCase()} Regime)`
      : sig.strategy_mode === 'v2_structure'
        ? 'Strategy v2 (15m Structure)'
        : 'Strategy v1 (5m Squeeze)';

  const dirSign = sig.direction === 'LONG' ? 1 : -1;
  const bePx = plan.entry + dirSign * plan.risk * 0.5;
  const lines: string[] = [
    `- 📍 **Entry:** \`${fmtPx(plan.entry, dec)}\`${Math.abs(plan.entry_limit - plan.entry) > 1e-9 ? ` (Pullback \`${fmtPx(plan.entry_limit, dec)}\` until ${fmtUtc(sig.valid_until)})` : ''}`,
    `- 🛡️ **Stop Loss:** \`${fmtPx(plan.sl, dec)}\` (${riskPct.toFixed(2)}% · \`${atrMult.toFixed(1)}× ATR\`)`,
    `- ⚖️ **Position Risk:** \`≤ ${ad.size_mult.toFixed(2)}%\` equity  ·  ${stratTag} · ${edgeBadge}`,
    `- ⏱️ **Timeframe & Monitor:** \`${sig.timeframe || '5m'}\` pure squeeze · Monitored live until close`,
    '',
    '### 🪜 Profit & Protection Ladder',
    '',
    `🔐 **Breakeven** (+0.50R) ➜ \`${fmtPx(bePx, dec)}\` · Move stop to entry`,
    `🔒 **TP1 (${r1.toFixed(2)}R)** ➜ \`${fmtPx(plan.tp1, dec)}\` · Bank **${p1Pct}%**, move SL → Breakeven`,
    `📐 **TP2 (${r2.toFixed(2)}R)** ➜ \`${fmtPx(plan.tp2, dec)}\` · Bank **${p2Pct}%**, move SL → TP1`,
    `🚀 **TP3 (${r3.toFixed(2)}R)** ➜ \`${fmtPx(plan.tp3, dec)}\` · Trail **${remPct}%** runner at \`${ad.trail_atr_mult.toFixed(1)}× ATR\``,
    '',
  ];

  const passed = sig.checks.filter((c) => c.passed).map((c) => c.label);
  const missed = sig.checks.filter((c) => !c.passed).map((c) => c.label);
  lines.push(`### ✅ Confluence (${passed.length}/${sig.checks.length} Passed)`);
  if (passed.length) lines.push(`- **Passed:** ${passed.join(' · ')}`);
  if (missed.length) lines.push(`- **Missed:** ${missed.join(' · ')}`);
  lines.push('');

  if (sig.reasons.length) {
    lines.push('### 💡 Why This Setup');
    for (const r of sig.reasons) lines.push(`- ${r}`);
    lines.push('');
  }

  if (sig.warnings.length) {
    lines.push('### ⚠️ Watch-outs');
    for (const w of sig.warnings) lines.push(`- ${w}`);
    lines.push('');
  }

  const e = sig.edge_dir;
  const costNote = sig.net_of_costs ? ' (net of spread & gap costs)' : '';
  lines.push(`📒 **Track Record${costNote}:**`);
  if (e.n > 0) {
    lines.push(
      `\`${e.n}\` trades · TP1 Hit \`${Math.round(e.tp1_rate * 100)}%\` · Win \`${Math.round(e.win_rate * 100)}%\` · Avg \`${e.avg_r >= 0 ? '+' : ''}${e.avg_r.toFixed(2)}R\` · ${e.consistent ? '✅ Stable across both halves' : '⚠️ Variable across halves'}`
    );
  } else {
    lines.push('No qualifying historical trades for this symbol/direction yet.');
  }

  lines.push(
    '',
    '---',
    `*Live monitoring is ON: TP1/TP2/TP3, SL→BE, trailing stops, risk warnings, and early exits will be commented directly below this post. Comment \`!follow\` to get instant reply & DM alerts on every move, \`!calc ${sig.symbol} 1000 1\` for sizing, or \`!scan ${sig.symbol}\` for a live refresh. Not financial advice.*`
  );
  return lines.join('\n');
}

export function formatLifecycleComment(ev: TradeEventDto): string {
  const dec = ev.decimals;
  const p1Pct = Math.round(ev.partial_tp1 * 100);
  const p2Pct = Math.round(ev.partial_tp2 * 100);
  const remPct = Math.round(ev.runner_fraction * 100);
  const oldS = fmtPx(ev.old_sl ?? ev.active_sl, dec);
  const newS = fmtPx(ev.new_sl ?? ev.active_sl, dec);
  const rTxt = `${ev.r_total >= 0 ? '+' : ''}${ev.r_total.toFixed(2)}R`;

  let headline = `🔔 ${ev.kind} · ${ev.direction} ${ev.name}`;
  let doNow = ev.reason || `Update recorded at \`${fmtPx(ev.price, dec)}\` (\`${rTxt}\`).`;

  if (ev.kind === 'PARAM_BE') {
    headline = '🛡️ PARAMETER · Move Stop to Breakeven';
    doNow = `Move stop from \`${oldS}\` to **\`${newS}\`** right now to eliminate downside risk.`;
  } else if (ev.kind === 'PARAM_TRAIL') {
    headline = '📐 PARAMETER · Trailing Stop Raised';
    doNow = `Raise trailing stop from \`${oldS}\` to **\`${newS}\`** (\`${rTxt}\` minimum locked).`;
  } else if (ev.kind === 'PARAM_TP') {
    headline = '🎚️ PARAMETER · Take Profit Modified';
    doNow = `Update TP2 to **\`${fmtPx(ev.new_tp ?? ev.tp2, dec)}\`** (TP3 \`${fmtPx(ev.tp3, dec)}\`).`;
  } else if (ev.kind === 'TP1') {
    headline = `🎯 TP1 HIT · Bank ${p1Pct}% & Move SL to Breakeven`;
    doNow = `Bank **${p1Pct}%** (\`${rTxt}\` locked). Move stop from \`${oldS}\` to **\`${fmtPx(ev.entry, dec)}\`**. Keep remaining ${100 - p1Pct}% active for TP2/TP3.`;
  } else if (ev.kind === 'TP2') {
    headline = `🥈 TP2 HIT · Bank ${p2Pct}% & Move SL to TP1`;
    doNow = `TP2 (\`${fmtPx(ev.tp2, dec)}\`) reached (\`${rTxt}\` banked). Bank **${p2Pct}%**, raise SL to **\`${newS}\`** (TP1), and trail final **${remPct}%** toward TP3 (\`${fmtPx(ev.tp3, dec)}\`).`;
  } else if (ev.kind === 'TP3') {
    headline = '👑 TP3 HIT · Full Runner Target Reached';
    doNow = `Full target TP3 (\`${fmtPx(ev.tp3, dec)}\`) reached — final result **\`${rTxt}\`**. Trade complete.`;
  } else if (ev.kind === 'BE') {
    headline = '🛡️ BREAKEVEN EXIT · Capital Protected';
    doNow = `Price returned to entry (\`${fmtPx(ev.entry, dec)}\`). Final result **\`${rTxt}\`**.`;
  } else if (ev.kind === 'TRAIL_SL') {
    headline = '📈 TRAILING STOP HIT · Runner Profit Locked';
    doNow = `Profit-locked trailing stop triggered at \`${fmtPx(ev.price, dec)}\`. Final result **\`${rTxt}\`**.`;
  } else if (ev.kind === 'SL') {
    headline = '🛑 STOP LOSS HIT · Trade Closed';
    doNow = `Initial stop reached (\`${rTxt}\`). Risk stayed capped — wait for the next setup.`;
  } else if (ev.kind === 'INVALIDATED') {
    headline = `⚠️ EARLY EXIT · Structure Invalidated (${rTxt})`;
    doNow = ev.reason || `Momentum flipped and price closed across EMA21 at \`${fmtPx(ev.price, dec)}\`. Close manually.`;
  } else if (ev.kind === 'TIME') {
    headline = '⏱️ TIME EXPIRY · Horizon Reached';
    doNow = `Horizon (${ev.horizon} bars) expired — close remaining position at \`${fmtPx(ev.price, dec)}\` (\`${rTxt}\`).`;
  }

  const statusTxt = ev.final ? `🏁 Closed · \`${rTxt}\`` : `🟢 Active · \`${rTxt}\` banked`;
  return [
    `### ${headline}`,
    '',
    `> **Do now:** ${doNow}`,
    '',
    '| Entry | Current | Stop | Status | Progress |',
    '|---:|---:|---:|---|---|',
    `| \`${fmtPx(ev.entry, dec)}\` | \`${fmtPx(ev.price, dec)}\` | \`${newS}\` | ${statusTxt} | Bar ${ev.bars}/${ev.horizon} (${ev.bars * 5}m) |`,
  ].join('\n');
}

export function formatPulsePost(pulse: PulseItemDto): { title: string; body: string } {
  if (pulse.kind === 'eod_recap') {
    const d = pulse.data as {
      trading_day?: string;
      stats?: BridgeOverviewDto['stats'];
      open_trades?: OpenTradeDto[];
      signals_sent?: number;
      signals_filtered?: number;
      pulses_sent?: number;
    };
    const day = d.trading_day ?? pulse.trading_day;
    const st = d.stats;
    const open = d.open_trades ?? [];
    const netTxt = st ? `${st.total_r >= 0 ? '+' : ''}${st.total_r.toFixed(2)}R` : '+0.00R';
    const title = `🌙 END-OF-DAY DESK RECAP · ${day} UTC · Net ${netTxt} · ${st?.n ?? 0} Closed Trades`;

    const lines: string[] = [
      `Session closed at \`00:00 UTC\` · \`${d.pulses_sent ?? 0}\` intraday pulses completed · Daily report counters reset cleanly for the new UTC trading day.`,
      '',
      '| Metric | Value | Metric | Value |',
      '|---|---|---|---|',
      `| **Day Net Result** | \`${netTxt}\` | **Closed Trades** | \`${st?.n ?? 0}\` (${st?.wins ?? 0}W / ${st?.losses ?? 0}L / ${st?.breakevens ?? 0}BE) |`,
      `| **Win Rate / TP1** | \`${st?.n ? `${Math.round(st.win_rate * 100)}%` : '—'}\` / \`${st?.n ? `${Math.round(st.tp1_rate * 100)}%` : '—'}\` | **Profit Factor** | \`${st?.n ? fmtPf(st.profit_factor, st.profit_factor_inf) : '—'}\` |`,
      `| **Max Intraday DD** | \`-${(st?.max_drawdown_r ?? 0).toFixed(2)}R\` | **Signals Sent / Filtered** | \`${d.signals_sent ?? 0}\` / \`${d.signals_filtered ?? 0}\` |`,
      `| **Carried Overnight** | \`${open.length}\` open trades | **Daily Reset** | \`00:00 UTC\` |`,
      '',
    ];
    if (st && st.by_regime.length) {
      lines.push('### Attribution by Regime');
      for (const b of st.by_regime.slice(0, 5)) {
        lines.push(`- \`${b.label}\` · ${b.n} trades · Win \`${Math.round(b.win_rate * 100)}%\` · \`${b.total_r >= 0 ? '+' : ''}${b.total_r.toFixed(2)}R\``);
      }
      lines.push('');
    }
    if (open.length) {
      lines.push('### Positions Carried Overnight');
      for (const t of open.slice(0, 10)) {
        lines.push(
          `- ${t.direction === 'LONG' ? '🟢 **LONG**' : '🔴 **SHORT**'} ${t.name} (\`${t.symbol}\`) · Entry \`${fmtPx(t.entry, t.decimals)}\` · Active SL \`${fmtPx(t.active_sl, t.decimals)}\` · Stage ${t.stage}`
        );
      }
      lines.push('');
    }
    lines.push('---', '*Full 7-day & all-time ledger remains preserved inside the pinned Interactive Command Center and via `!stats`.*');
    return { title: title.slice(0, 295), body: lines.join('\n') };
  }

  const d = pulse.data as unknown as ChannelViewDto;
  const slot = pulse.pulse_slot ?? d.pulse_slot ?? [1, 2];
  const day = pulse.trading_day || d.trading_day;
  const b = d.breadth ?? { total: 0, bull_1h: 0, bear_1h: 0, sqz_fired: 0, sqz_on: 0 };
  const open = d.open_trades ?? [];
  const rows: SummaryRowDto[] = d.summaries ?? [];
  const gainers = [...rows].sort((a, c) => c.change_1h_pct - a.change_1h_pct).slice(0, 3);
  const losers = [...rows].sort((a, c) => a.change_1h_pct - c.change_1h_pct).slice(0, 3);
  const p12 = d.perf['12h'] ?? d.perf['3h'];

  const title = `🌐 DERIV MARKET PULSE · 12-Hour Brief (${slot[0]}/${slot[1]}) · ${day} UTC · ${b.total} Symbols`;
  const lines: string[] = [
    `**${day} UTC** (resets daily at \`00:00 UTC\`) · **${b.total}** instruments tracked · **${open.length}** open trades`,
    '',
    '| Metric | Readout |',
    '|---|---|',
    `| **1H Macro Breadth** | 🟢 \`${b.bull_1h}\` Bullish · 🔴 \`${b.bear_1h}\` Bearish |`,
    `| **Squeeze Radar** | ⚡ \`${b.sqz_fired}\` fired · ● \`${b.sqz_on}\` coiling |`,
    `| **Closed (12h / Today)** | 12h \`${p12.n}\` (\`${p12.total_r >= 0 ? '+' : ''}${p12.total_r.toFixed(2)}R\`) · Today \`${d.perf.today.n}\` (\`${d.perf.today.total_r >= 0 ? '+' : ''}${d.perf.today.total_r.toFixed(2)}R\`) |`,
    `| **1H Leaders** | ${gainers.map((g) => `\`${g.symbol}\` ${g.change_1h_pct >= 0 ? '+' : ''}${g.change_1h_pct.toFixed(2)}%`).join(' · ') || 'n/a'} |`,
    `| **1H Decliners** | ${losers.map((l) => `\`${l.symbol}\` ${l.change_1h_pct >= 0 ? '+' : ''}${l.change_1h_pct.toFixed(2)}%`).join(' · ') || 'n/a'} |`,
    '',
  ];

  if (open.length) {
    lines.push(`### 🎯 Active Trades (${open.length})`);
    for (const t of open.slice(0, 10)) {
      lines.push(
        `- ${t.direction === 'LONG' ? '🟢 **LONG**' : '🔴 **SHORT**'} **${t.name}** (\`${t.symbol}\`) · Entry \`${fmtPx(t.entry, t.decimals)}\` · SL \`${fmtPx(t.active_sl, t.decimals)}\` · Stage ${t.stage} · Bar ${t.bars}/${t.horizon}`
      );
    }
    lines.push('');
  }

  const byChannel = new Map<string, SummaryRowDto[]>();
  for (const row of rows) {
    const arr = byChannel.get(row.channel_name) ?? [];
    arr.push(row);
    byChannel.set(row.channel_name, arr);
  }
  for (const ch of MARKET_CHANNELS) {
    const items = byChannel.get(ch.id);
    if (!items || !items.length) continue;
    lines.push(`### ${ch.flairText} (${items.length})`);
    lines.push('| Symbol | Price | 1H % | MTF (5m/15m/1H) | Status |');
    lines.push('|---|---:|---:|---|---|');
    for (const s of items.slice(0, 12)) {
      const arrows = `${biasArrow(s.bias_5m)}${biasArrow(s.bias_15m)}${biasArrow(s.bias_1h)}`;
      lines.push(
        `| \`${s.symbol}\` | \`${s.price_formatted}\` | \`${s.change_1h_pct >= 0 ? '+' : ''}${s.change_1h_pct.toFixed(2)}%\` | ${arrows} ${s.trend_label} | ${s.active_trade_status || s.progress_label} |`
      );
    }
    lines.push('');
  }

  return { title: title.slice(0, 295), body: lines.join('\n').slice(0, 39000) };
}

export function formatChannelDirectoryBody(subredditName: string): string {
  const lines: string[] = [
    `Welcome to **r/${subredditName} — Cerynaean Hind Trading Radar (Strategy v3 · 30m/1H Adaptive Regimes)**!`,
    '',
    'Just like our Slack workspace, every Deriv market class is organized into its own **dedicated Channel Section** below. Click any channel link to filter the feed exclusively to that market, or use the pinned **Interactive Command Center** post to switch channels live!',
    '',
    '### 📡 Dedicated Market Channels',
    '',
    '| Channel | Focus | Direct Feed Link |',
    '|---|---|---|',
  ];

  for (const ch of MARKET_CHANNELS) {
    const q = encodeURIComponent(`flair:"${ch.flairText}"`);
    const url = `https://www.reddit.com/r/${subredditName}/search/?q=${q}&restrict_sr=1&sort=new`;
    lines.push(`| **${ch.flairText}** | ${ch.description} | [Open ${ch.label} →](${url}) |`);
  }

  lines.push(
    '',
    '### 🤖 Interactive Comment Commands',
    'Reply on any post in this community with:',
    '- `!scan <symbol>` — Live 30m/1H Strategy v3 technical read & blockers (e.g. `!scan EURUSD`, `!scan BTCUSD`)',
    '- `!calc <symbol> <equity> [risk%]` — Exact position sizing & TP1/TP2/TP3 `$` projections (e.g. `!calc EURUSD 1000 1`)',
    '- `!status` — Live Deriv stream health, active strategy engine & validated pair count',
    '- `!open` — List all currently active tracked trades',
    '- `!stats` — Live track record (24h / 7d / All-Time, Profit Factor, Max Drawdown)',
    '- `!edges` — Symbols currently holding a validated net-of-cost walk-forward edge',
    '- `!help` — Show all commands',
    '',
    '---',
    '*Automated decision-support & risk management radar powered by Strategy v3. Not financial advice.*'
  );
  return lines.join('\n');
}

export function formatScanComment(scan: ScanResultDto): string {
  const stratName =
    scan.strategy_mode === 'v3_regime'
      ? 'Strategy v3'
      : scan.strategy_mode === 'v2_structure'
        ? 'Strategy v2'
        : 'Strategy v1';
  const blockersTxt = scan.blockers.length
    ? '**Why no signal fired on the last closed bar:**\n' + scan.blockers.map((b) => `- ${b}`).join('\n')
    : `**✅ Every ${stratName} gate passes** on the last closed bar!`;
  return [
    `### 🔎 Live ${stratName} Read · ${scan.name} (\`${scan.symbol}\`)`,
    `*${scan.class_label} · #${scan.channel_name} · Last closed ${scan.timeframe} bar ${fmtUtc(scan.epoch + scan.bar_seconds)}*`,
    '',
    '| Close | Direction Hint | Squeeze | ATR14 | ADX / RSI | EMA Stack |',
    '|---:|---|---|---:|---|---|',
    `| \`${scan.price_formatted}\` | **${scan.direction_hint}** | \`${scan.squeeze}\` | \`${fmtPx(scan.atr, scan.decimals)}\` | \`${scan.adx ?? '—'}\` / \`${scan.rsi ?? '—'}\` | ${scan.ema_stack} |`,
    '',
    '**Timeframe Alignment:**',
    ...scan.views.map(
      (v) => `- **${v.label.toUpperCase()}**: ${v.bias}, ${v.momentum} (Sqz \`${v.squeeze}\`) ${v.aligned ? '✅' : '⚠️'}`
    ),
    '',
    blockersTxt,
  ].join('\n');
}

export function formatCalcComment(calc: CalcResultDto): string {
  const r = calc.result;
  const dec = calc.decimals;
  return [
    `### 🧮 Position Sizing · ${calc.direction} ${calc.name} (\`${calc.symbol}\`)`,
    `*${calc.regime_label || 'Live Plan'} · Equity \`$${r.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\` · Base \`${r.base_risk_pct.toFixed(2)}%\` × \`${r.size_mult.toFixed(2)}\` = \`${r.effective_risk_pct.toFixed(2)}%\`*`,
    '',
    '| Metric | Value | Metric | Value |',
    '|---|---:|---|---:|',
    `| **Dollar Risk (1R)** | \`$${r.risk_amount.toFixed(2)}\` | **Position Size** | \`${r.units.toFixed(4)}\` units |`,
    `| **Entry** | \`${fmtPx(r.entry, dec)}\` | **Stop Loss** | \`${fmtPx(r.sl, dec)}\` (${r.stop_pips.toFixed(1)} ${r.pip_unit}) |`,
    `| **TP1 Potential** | \`+$${r.tp1_profit.toFixed(2)}\` | **TP2 Potential** | \`+$${r.tp2_profit.toFixed(2)}\` |`,
    `| **TP3 Potential** | \`+$${r.tp3_profit.toFixed(2)}\` | **Stop Distance** | \`${fmtPx(r.stop_distance, dec)}\` |`,
  ].join('\n');
}

export function formatOverviewComment(ov: BridgeOverviewDto, mode: 'status' | 'open' | 'stats' | 'edges'): string {
  if (mode === 'status') {
    const s = ov.status;
    return [
      '### 🛰️ Cerynaean Hind — Live System Status',
      `- **Stream:** ${s.connected ? '🟢 Live' : '🟠 Reconnecting'} (\`${s.phase}\`)`,
      `- **Strategy Engine:** \`${ov.settings.strategy_mode}\` · Edge Mode \`${ov.settings.edge_mode}\` · Min Grade \`${ov.settings.min_grade}\``,
      `- **Symbols Scanned:** \`${s.symbols_loaded} / ${s.symbols_total}\` · **Validated Net-of-Cost Pairs:** \`${s.validated_pairs}\``,
      `- **Open Trades:** \`${ov.open_trades.length}\` · **Today UTC:** \`${ov.daily.signals_sent}\` sent / \`${ov.daily.signals_filtered}\` filtered`,
    ].join('\n');
  }

  if (mode === 'open') {
    if (!ov.open_trades.length) return '🎯 **No active tracked trades right now.** Use `!scan <symbol>` to inspect live setups.';
    return [
      `### 🎯 Active Tracked Trades (${ov.open_trades.length})`,
      ...ov.open_trades.slice(0, 15).map(
        (t) =>
          `- ${t.direction === 'LONG' ? '🟢 **LONG**' : '🔴 **SHORT**'} **${t.name}** (\`${t.symbol}\`) · Grade \`${t.grade || '-'}\` · Entry \`${fmtPx(t.entry, t.decimals)}\` · SL \`${fmtPx(t.active_sl, t.decimals)}\` · Stage ${t.stage} · Bar ${t.bars}/${t.horizon}`
      ),
    ].join('\n');
  }

  if (mode === 'edges') {
    if (!ov.validated_edges.length) return '🧪 **No validated net-of-cost walk-forward pairs currently active.** Check back after the next 6h calibration.';
    return [
      `### 🧪 Validated Net-of-Cost Walk-Forward Edges (${ov.validated_edges.length})`,
      ...ov.validated_edges.slice(0, 20).map(
        (r) =>
          `- ${r.direction === 'LONG' ? '🟢' : '🔴'} \`${r.symbol}\` **${r.direction}** · Grade **${r.grade}** · \`${r.n}\` trades · TP1 \`${Math.round(r.tp1 * 100)}%\` · Avg \`${r.avg_r >= 0 ? '+' : ''}${r.avg_r.toFixed(2)}R\``
      ),
    ].join('\n');
  }

  const st = ov.stats;
  return [
    '### 📒 Live Track Record & Attribution',
    `- **Today UTC:** \`${ov.perf.today.n}\` trades · Win \`${Math.round(ov.perf.today.win_rate * 100)}%\` · Net \`${ov.perf.today.total_r >= 0 ? '+' : ''}${ov.perf.today.total_r.toFixed(2)}R\``,
    `- **7 Days:** \`${ov.perf['7d'].n}\` trades · Win \`${Math.round(ov.perf['7d'].win_rate * 100)}%\` · Net \`${ov.perf['7d'].total_r >= 0 ? '+' : ''}${ov.perf['7d'].total_r.toFixed(2)}R\``,
    `- **All-Time:** \`${st.n}\` trades (${st.wins}W / ${st.losses}L / ${st.breakevens}BE) · PF \`${fmtPf(st.profit_factor, st.profit_factor_inf)}\` · Max DD \`-${st.max_drawdown_r.toFixed(2)}R\` · Total \`${st.total_r >= 0 ? '+' : ''}${st.total_r.toFixed(2)}R\``,
  ].join('\n');
}

export function formatHelpComment(): string {
  return [
    '### 🤖 Cerynaean Hind — Reddit Commands (`r/GlvnsOrg`)',
    '- `!follow` (or `!entered`) — Subscribe to a signal post for instant reply + DM alerts on every management move & warning until close',
    '- `!unfollow` (or `!exited`) — Unsubscribe from alerts on that signal post',
    '- `!scan <symbol>` — Live Strategy v3 30m/1H read & blockers (e.g. `!scan EURUSD`)',
    '- `!calc <symbol> <equity> [risk%]` — Exact position size & TP1/TP2/TP3 `$` projections (e.g. `!calc EURUSD 1000 1`)',
    '- `!status` — Stream health, Strategy v3 status, symbols scanned',
    '- `!open` — Currently active tracked trades & stages',
    '- `!stats` — Live track record (Today UTC / 7d / All-Time, Profit Factor, Drawdown)',
    '- `!edges` — Validated net-of-cost walk-forward pairs',
    '',
    '*Tip: Open the subreddit menu (`🔔 Enable Live Trade Push Alerts`) to opt in to live push notifications for all entered positions.*',
  ].join('\n');
}

export function parseAlertUsernames(raw: unknown): string[] {
  if (typeof raw !== 'string' || !raw.trim()) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(/[\s,;]+/)) {
    const cleaned = token.trim().replace(/^\/?u\//i, '');
    if (!cleaned) continue;
    if (!/^[A-Za-z0-9_-]{3,24}$/.test(cleaned)) continue;
    const lower = cleaned.toLowerCase();
    if (lower === 'cerynaean-hind') continue;
    if (seen.has(lower)) continue;
    seen.add(lower);
    out.push(cleaned);
  }
  return out;
}

export function buildLiveFlairText(baseFlair: string, kindTag: string, rLabel: string): string {
  const tag = (kindTag || 'UPDATE').trim();
  const r = (rLabel || '').trim();
  const suffix = r ? `${tag} (${r})` : tag;
  return `${baseFlair} · ${suffix}`.slice(0, 64);
}

export function appendRedditMentions(body: string, usernames: string[]): string {
  const unique: string[] = [];
  const seen = new Set<string>();
  for (const raw of usernames) {
    const clean = raw.trim().replace(/^\/?u\//i, '');
    if (!clean) continue;
    const lower = clean.toLowerCase();
    if (lower === 'cerynaean-hind' || seen.has(lower)) continue;
    seen.add(lower);
    unique.push(clean);
    if (unique.length >= 3) break;
  }
  if (!unique.length) return body;
  const pings = unique.map((u) => `u/${u}`).join(' · ');
  return `${body}\n\n---\n🔔 *Alert:* ${pings}`;
}

export function formatLifecycleDm(params: {
  subredditName: string;
  postId: string;
  commentId?: string;
  title: string;
  kindTag: string;
  rLabel: string;
  body: string;
}): { subject: string; text: string } {
  const postShort = params.postId.replace(/^t3_/, '');
  const commentShort = params.commentId ? params.commentId.replace(/^t1_/, '') : '';
  const url = commentShort
    ? `https://www.reddit.com/r/${params.subredditName}/comments/${postShort}/_/${commentShort}/`
    : `https://www.reddit.com/r/${params.subredditName}/comments/${postShort}/`;

  const rPart = params.rLabel ? ` (${params.rLabel})` : '';
  const subject = `[r/${params.subredditName}] ${params.kindTag}${rPart} — ${params.title}`.slice(0, 100);
  const text = [
    `**${params.kindTag}${rPart}** on **${params.title}**`,
    '',
    `👉 [Open live trade thread on r/${params.subredditName}](${url})`,
    '',
    '---',
    '',
    params.body.slice(0, 8000),
    '',
    '---',
    '*Reply `!unfollow` on the post to stop receiving alerts for this position.*',
  ].join('\n');
  return { subject, text };
}

const IMPORTANT_LIFECYCLE_TAGS = new Set([
  'BREAKEVEN',
  'BREAKEVEN SECURED',
  'PROFIT LOCKED',
  'TRAILING STOP',
  'TRAILING STOP MOVED',
  'STOP TIGHTENED',
  'PARTIAL',
  'PARTIAL TAKEN',
  'RISK WARNING',
  'DANGER ZONE',
  'REVERSAL WARNING',
  'EXIT WARNING',
  'TP1',
  'TP2',
  'TP3',
  'STOP LOSS',
  'EARLY EXIT',
  'TIME EXPIRY',
  'MANUAL CLOSE',
  'WIN',
  'SCRATCH',
  'LOSS CAPPED',
]);

export function isImportantLifecycleEvent(kindTag: string, isFinal: boolean): boolean {
  if (isFinal) return true;
  return IMPORTANT_LIFECYCLE_TAGS.has((kindTag || '').trim().toUpperCase());
}

export function formatPushAlert(params: {
  title: string;
  kindTag: string;
  rLabel: string;
  body: string;
}): { title: string; body: string } {
  const rPart = params.rLabel ? ` (${params.rLabel})` : '';
  const title = `${params.kindTag}${rPart} · ${params.title}`.slice(0, 100);
  const plainBody = params.body
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/[*_`>]/g, '')
    .replace(/\r?\n+/g, ' · ')
    .replace(/\s{2,}/g, ' ')
    .trim()
    .slice(0, 200);
  return {
    title,
    body: plainBody || title,
  };
}
