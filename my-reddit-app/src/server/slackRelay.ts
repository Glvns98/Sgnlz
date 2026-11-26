import { MARKET_CHANNELS, getChannelDef } from '../shared/channels.js';
import type { SignalCardDetail, SignalCardPostData } from '../shared/types.js';

const SLACK_EMOJI_MAP: Record<string, string> = {
  green_circle: '🟢',
  red_circle: '🔴',
  white_circle: '⚪',
  large_green_circle: '🟢',
  large_red_circle: '🔴',
  dart: '🎯',
  second_place_medal: '🥈',
  crown: '👑',
  octagonal_sign: '🛑',
  shield: '🛡️',
  triangular_ruler: '📐',
  level_slider: '🎚️',
  warning: '⚠️',
  stopwatch: '⏱️',
  raised_hand: '✋',
  chart_with_upwards_trend: '📈',
  fire: '🔥',
  ocean: '🌊',
  zap: '⚡',
  white_check_mark: '✅',
  no_entry_sign: '🚫',
  globe_with_meridians: '🌐',
  crescent_moon: '🌙',
  boom: '💥',
  ladder: '🪜',
  kangaroo: '🦘',
  package: '📦',
  arrows_counterclockwise: '🔄',
  currency_exchange: '💱',
  basket: '🧺',
  first_place_medal: '🥇',
  bar_chart: '📊',
  cyclone: '🌀',
  satellite: '🛰️',
  mag_right: '🔎',
  abacus: '🧮',
  book: '📖',
  white_small_square: '▫️',
  x: '❌',
};

function decodeSlackEntities(input: string): string {
  return input.replace(/&amp;/g, '&').replace(/&gt;/g, '>').replace(/&lt;/g, '<');
}

export function slackToMarkdown(input: string): string {
  if (!input) return '';
  let out = decodeSlackEntities(input);
  out = out.replace(/:([a-z0-9_+-]+):/gi, (m, code: string) => SLACK_EMOJI_MAP[code.toLowerCase()] ?? m);
  out = out.replace(/<#[A-Z0-9]+\|([^>]+)>/g, '#$1');
  out = out.replace(/<#[A-Z0-9]+>/g, '');
  out = out.replace(/<(https?:\/\/[^|>]+)\|([^>]+)>/g, '[$2]($1)');
  // Convert Slack *bold* to Reddit **bold** outside backtick spans
  const parts = out.split(/(`[^`]*`)/g);
  out = parts
    .map((part) => {
      if (part.startsWith('`') && part.endsWith('`')) return part;
      return part.replace(/(^|[\s(>])\*([^*\n]+)\*(?=[\s.,;:!?)]|$)/g, '$1**$2**');
    })
    .join('');
  return out.trim();
}

export function slackToPlain(input: string): string {
  if (!input) return '';
  return slackToMarkdown(input)
    .replace(/\*\*/g, '')
    .replace(/(^|\s)_([^_\n]+)_(?=[\s.,;:!?)]|$)/g, '$1$2')
    .replace(/`/g, '')
    .replace(/^>\s*/gm, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

export interface SlackBlock {
  type?: string;
  text?: { type?: string; text?: string };
  elements?: { type?: string; text?: string | { text?: string }; action_id?: string; value?: string }[];
  fields?: { type?: string; text?: string }[];
}

export interface SlackRawMessage {
  ts: string;
  thread_ts?: string;
  subtype?: string;
  text?: string;
  reply_count?: number;
  blocks?: SlackBlock[];
  attachments?: { color?: string; fallback?: string; blocks?: SlackBlock[] }[];
}

export interface ParsedSignalRef {
  id: string;
  sym: string;
  name: string;
  dir: 'LONG' | 'SHORT';
  e: number;
  sl: number;
  t1: number;
  t2: number;
  t3: number;
  sm: number;
  g: string;
  rc: string;
  dec: number;
  pv: number;
  pu: string;
}

export interface ParsedSlackSignal {
  kind: 'signal';
  ts: string;
  channelId: string;
  channelName: string;
  signalId: string;
  symbol: string;
  name: string;
  direction: 'LONG' | 'SHORT';
  grade: string;
  score: number;
  entry: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  sizeMult: number;
  regimeCode: string;
  decimals: number;
  pipValue: number;
  pipUnit: string;
  title: string;
  body: string;
  publishedEpoch: number;
  card: SignalCardPostData;
  detail: SignalCardDetail;
}

export interface ParsedSlackLifecycle {
  kind: 'lifecycle';
  ts: string;
  parentTs: string;
  channelId: string;
  channelName: string;
  title: string;
  body: string;
  isFinal: boolean;
  rLabel: string;
  kindTag: string;
  publishedEpoch: number;
}

export interface ParsedSlackPulse {
  kind: 'pulse';
  ts: string;
  channelId: string;
  channelName: string;
  isEod: boolean;
  title: string;
  body: string;
  publishedEpoch: number;
}

export type ParsedSlackItem = ParsedSlackSignal | ParsedSlackLifecycle | ParsedSlackPulse;

function extractBlocks(msg: SlackRawMessage): SlackBlock[] {
  if (msg.attachments && msg.attachments.length > 0 && Array.isArray(msg.attachments[0].blocks)) {
    return msg.attachments[0].blocks;
  }
  return Array.isArray(msg.blocks) ? msg.blocks : [];
}

function blocksToRedditBody(blocks: SlackBlock[], skipHeader = true): string {
  const out: string[] = [];
  for (const b of blocks) {
    if (b.type === 'header') {
      if (!skipHeader && b.text?.text) {
        out.push(`### ${slackToMarkdown(b.text.text)}`, '');
      }
      continue;
    }
    if (b.type === 'divider') {
      out.push('---', '');
      continue;
    }
    if (b.type === 'actions') {
      continue;
    }
    if (b.type === 'context' && Array.isArray(b.elements)) {
      const ctx = b.elements
        .map((el) => (typeof el.text === 'string' ? slackToMarkdown(el.text) : ''))
        .filter(Boolean)
        .join(' · ');
      if (ctx) out.push(`*${ctx}*`, '');
      continue;
    }
    if (b.type === 'section') {
      if (b.text?.text) {
        out.push(slackToMarkdown(b.text.text), '');
      }
      if (Array.isArray(b.fields) && b.fields.length > 0) {
        const rows = b.fields
          .map((f) => `- ${slackToMarkdown((f.text ?? '').replace(/\s*\r?\n\s*/g, ': '))}`)
          .filter((s) => s.length > 2);
        if (rows.length) {
          out.push(rows.join('\n'), '');
        }
      }
    }
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function extractCompactRef(blocks: SlackBlock[]): ParsedSignalRef | null {
  for (const b of blocks) {
    if (b.type !== 'actions' || !Array.isArray(b.elements)) continue;
    for (const el of b.elements) {
      if ((el.action_id === 'signal_track_entry' || el.action_id === 'signal_size_calc') && el.value) {
        try {
          const parsed = JSON.parse(el.value) as ParsedSignalRef;
          if (parsed && parsed.sym && parsed.dir) return parsed;
        } catch {
          // ignore
        }
      }
    }
  }
  return null;
}

function parseFieldPair(raw: string): { label: string; value: string } | null {
  const decoded = decodeSlackEntities(raw);
  const m = decoded.match(/^\*([^*]+)\*\s*([\s\S]+)$/);
  if (m) {
    return { label: slackToPlain(m[1]), value: slackToPlain(m[2].replace(/\s*\r?\n\s*/g, ' ')) };
  }
  const plain = slackToPlain(decoded);
  if (!plain) return null;
  const idx = plain.indexOf(':');
  if (idx > 0 && idx < 28) {
    return { label: plain.slice(0, idx).trim(), value: plain.slice(idx + 1).trim() };
  }
  return { label: 'Info', value: plain };
}

function buildStructuredSignalCard(
  channelName: string,
  ref: ParsedSignalRef,
  score: number,
  publishedEpoch: number,
  blocks: SlackBlock[]
): { card: SignalCardPostData; detail: SignalCardDetail } {
  const ch = getChannelDef(channelName);
  const risk = Math.abs(ref.e - ref.sl);
  const r1 = risk > 0 ? Number((Math.abs(ref.t1 - ref.e) / risk).toFixed(2)) : 1.2;
  const r2 = risk > 0 ? Number((Math.abs(ref.t2 - ref.e) / risk).toFixed(2)) : 2.2;
  const r3 = risk > 0 ? Number((Math.abs(ref.t3 - ref.e) / risk).toFixed(2)) : 3.5;
  const riskPct = ref.e > 0 ? Number(((risk / ref.e) * 100).toFixed(2)) : 0;

  let tf = 'v2 · 15m';
  let edge = '⚠️ Unvalidated Edge';
  let session = '24/7 synthetic';
  let timeUtc = new Date(publishedEpoch * 1000).toISOString().slice(11, 16) + ' UTC';
  const regimeMap: Record<string, string> = {
    STRONG_TREND: '🔥 Strong Trend',
    MODERATE_TREND: '🌊 Moderate Trend',
    BREAKOUT: '🚀 Squeeze Breakout',
    RANGE: '⚖️ Range Reversal',
    HIGH_VOL_SPIKE: '⚡ High-Vol Spike',
    FULL_TREND: '🔥 Full 1H + 4H Trend',
  };
  let regime = regimeMap[ref.rc] ?? '🌊 HTF Structure';
  let regimeSummary = '';
  let trigger = 'Strategy v3 Regime';
  let limitPx = ref.e;
  let validUntil = '';
  let atrMult = '';
  let tp1Bank = 40;
  let tp2Bank = 30;
  let tp3Bank = 30;
  let horizon = '480m';
  let confluence = '';
  let confluenceSummary = '';
  const confluencePassed: string[] = [];
  const confluenceMissed: string[] = [];
  const riskFields: { label: string; value: string }[] = [];
  const mtfRows: { label: string; detail: string; aligned: boolean }[] = [];
  const marketData: { label: string; value: string }[] = [];
  const whySetup: string[] = [];
  const warnings: string[] = [];
  let trackRecordTitle = 'Walk-Forward Track Record';
  let trackRecordSummary = '';

  for (const b of blocks) {
    if (b.type === 'context' && Array.isArray(b.elements)) {
      const ctxPlain = b.elements
        .map((el) => (typeof el.text === 'string' ? slackToPlain(el.text) : ''))
        .filter(Boolean)
        .join(' · ');
      if (/Strategy v3|Strategy v2|Strategy v1|1h|30m|15m|5m/i.test(ctxPlain)) {
        const parts = ctxPlain.split('·').map((p) => p.trim());
        for (const p of parts) {
          if (/Strategy v3\s*\(1h\)/i.test(p)) tf = 'v3 · 1h';
          else if (/Strategy v3\s*\(30m\)/i.test(p)) tf = 'v3 · 30m';
          else if (/Strategy v3/i.test(p)) tf = 'v3 · 1h';
          else if (/Strategy v2/i.test(p)) tf = 'v2 · 15m';
          else if (/Strategy v1/i.test(p)) tf = 'v1 · 5m';
          else if (/Unvalidated/i.test(p)) edge = '⚠️ Unvalidated Edge';
          else if (/Negative/i.test(p)) edge = '🚫 Negative Edge';
          else if (/Proven Edge|\bValidated\b/i.test(p)) edge = '✅ Proven Edge';
          else if (/synthetic|London|NY|session|UTC|24\/7/i.test(p)) session = p;
          else if (/^\d{2}:\d{2}(?:\s*UTC)?$/i.test(p)) timeUtc = p.includes('UTC') ? p : `${p} UTC`;
        }
      }
      continue;
    }

    if (b.type !== 'section') continue;
    const rawText = b.text?.text ?? '';
    const decoded = decodeSlackEntities(rawText);
    const plain = slackToPlain(rawText);

    // Regime quote block
    if (/^\s*>/m.test(decoded) && !regimeSummary) {
      const firstSentence = decoded.match(/\*([^*]+)\*/);
      if (firstSentence) {
        regime = slackToPlain(firstSentence[1]).replace(/\.$/, '').slice(0, 48);
      }
      regimeSummary = plain;
      continue;
    }

    // Trade plan block
    if (/^\*Trade plan\*/i.test(decoded)) {
      const trigM = decoded.match(/_([^_]+)_/);
      if (trigM) trigger = slackToPlain(trigM[1]).slice(0, 72);
      const limM = decoded.match(/\*Limit:\*\s*`([0-9,.\-]+)`/i);
      if (limM) {
        const parsedLim = Number(limM[1].replace(/,/g, ''));
        if (Number.isFinite(parsedLim)) limitPx = parsedLim;
      }
      const valM = decoded.match(/valid until\s+([0-9:]+\s*UTC)/i);
      if (valM) validUntil = valM[1];
      const atrM = decoded.match(/`([0-9.]+×\s*ATR)`/i);
      if (atrM) atrMult = atrM[1];
      const b1 = decoded.match(/TP1:.*?bank\s+(\d+)%/i);
      if (b1) tp1Bank = Number(b1[1]);
      const b2 = decoded.match(/TP2:.*?bank\s+(\d+)%/i);
      if (b2) tp2Bank = Number(b2[1]);
      tp3Bank = Math.max(0, 100 - tp1Bank - tp2Bank);
      continue;
    }

    // Risk & sizing fields
    if (/^\*Risk\s*&\s*sizing\*/i.test(decoded) && Array.isArray(b.fields)) {
      for (const f of b.fields) {
        const pair = parseFieldPair(f.text ?? '');
        if (pair) {
          riskFields.push(pair);
          if (/horizon/i.test(pair.label)) horizon = pair.value.replace(/\s*min/i, 'm');
        }
      }
      continue;
    }

    // Timeframe alignment
    if (/^\*Timeframe alignment\*/i.test(decoded)) {
      const lines = decoded.split(/\r?\n/).slice(1);
      for (const ln of lines) {
        const cleanLn = slackToPlain(ln);
        if (!cleanLn) continue;
        const m = ln.match(/\*([^*]+)\*\s*·\s*(.+)$/);
        const label = m ? slackToPlain(m[1]) : cleanLn.split('·')[0].trim();
        const rawDet = m ? slackToPlain(m[2]) : cleanLn;
        const det = rawDet.replace(/\s*·\s*(?:✅\s*Aligned|⚠️\s*Opposing)\s*$/i, '').trim();
        mtfRows.push({
          label,
          detail: det,
          aligned: /white_check_mark|✅|Aligned/i.test(ln) && !/Opposing|⚠️/i.test(ln),
        });
      }
      continue;
    }

    // Confluence block
    if (/^\*Confluence/i.test(decoded)) {
      const lines = decoded.split(/\r?\n/);
      const hdr = slackToPlain(lines[0] ?? '');
      confluenceSummary = hdr;
      const cm = hdr.match(/(\d+)\s*of\s*(\d+)/i);
      if (cm) confluence = `${cm[1]}/${cm[2]} Passed`;
      for (const ln of lines.slice(1)) {
        const isMiss = /warning|no_entry_sign|x:|Missed|⚠️|❌|white_small_square|▫️/i.test(ln);
        const stripped = slackToPlain(ln)
          .replace(/^[✅⚠️❌🚫•\-▫️\s]+/, '')
          .replace(/^(?:Passed|Missed)\s*:\s*/i, '');
        const items = stripped
          .split('·')
          .map((s) => s.replace(/^[✅⚠️❌🚫•\-▫️\s]+/, '').trim())
          .filter(Boolean);
        if (isMiss) confluenceMissed.push(...items);
        else confluencePassed.push(...items);
      }
      continue;
    }

    // Market data fields
    if (/^\*Market data\*/i.test(decoded) && Array.isArray(b.fields)) {
      for (const f of b.fields) {
        const pair = parseFieldPair(f.text ?? '');
        if (pair) marketData.push(pair);
      }
      continue;
    }

    // Why this setup
    if (/^\*Why this setup\*/i.test(decoded)) {
      const lines = decoded.split(/\r?\n/).slice(1);
      for (const ln of lines) {
        const cleanLn = slackToPlain(ln).replace(/^[•\-*]\s*/, '');
        if (cleanLn) whySetup.push(cleanLn);
      }
      continue;
    }

    // Watch-outs / warnings
    if (/^\*(?:Watch-outs|Warnings|Risk notes)\*/i.test(decoded)) {
      const lines = decoded.split(/\r?\n/).slice(1);
      for (const ln of lines) {
        const cleanLn = slackToPlain(ln).replace(/^[•\-*⚠️]\s*/, '');
        if (cleanLn) warnings.push(cleanLn);
      }
      continue;
    }

    // Track record
    if (/^\*Track record/i.test(decoded)) {
      const lines = decoded.split(/\r?\n/);
      trackRecordTitle = slackToPlain(lines[0] ?? trackRecordTitle);
      trackRecordSummary = lines
        .slice(1)
        .map((l) => slackToPlain(l))
        .filter(Boolean)
        .join(' · ');
      continue;
    }
  }

  const card: SignalCardPostData = {
    kind: 'signal',
    signalId: (ref.id || `${ref.sym}-${publishedEpoch}-${ref.dir}`).slice(0, 64),
    sym: ref.sym.slice(0, 24),
    name: ref.name.slice(0, 48),
    dir: ref.dir,
    grade: ref.g.slice(0, 8),
    score: Math.round(score),
    ch: ch.id,
    chLabel: ch.flairText.slice(0, 32),
    chColor: ch.bgColor,
    entry: ref.e,
    limit: limitPx,
    sl: ref.sl,
    tp1: ref.t1,
    tp2: ref.t2,
    tp3: ref.t3,
    dec: ref.dec,
    r1,
    r2,
    r3,
    tp1Bank,
    tp2Bank,
    tp3Bank,
    atrMult: atrMult.slice(0, 16),
    riskPct,
    sizeMult: ref.sm || 1.0,
    pipValue: ref.pv || 0.01,
    pipUnit: (ref.pu || 'pts').slice(0, 12),
    regime: regime.slice(0, 48),
    trigger: trigger.slice(0, 72),
    edge: edge.slice(0, 24),
    tf: tf.slice(0, 16),
    session: session.slice(0, 24),
    timeUtc: timeUtc.slice(0, 16),
    horizon: horizon.slice(0, 16),
    confluence: confluence.slice(0, 20),
    status: 'ACTIVE',
    rLabel: '',
    closed: false,
  };

  const detail: SignalCardDetail = {
    card,
    regimeSummary: regimeSummary || `${regime} · ${trigger}`,
    validUntil,
    riskFields,
    mtfRows,
    confluenceSummary,
    confluencePassed,
    confluenceMissed,
    marketData,
    whySetup,
    warnings,
    trackRecordTitle,
    trackRecordSummary,
    events: [],
  };

  return { card, detail };
}

function fmtNum(value: number, dec: number): string {
  const d = Math.max(0, Math.min(6, Number.isFinite(dec) ? dec : 2));
  return value.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtDelta(value: number, dec: number): string {
  return `${value >= 0 ? '+' : ''}${fmtNum(value, dec)}`;
}

/**
 * Option B: Clean Native Reddit Text Post — 15m Scalping + Plain-Text Profit & Protection Ladder.
 * No multi-timeframe alignment clutter, no table cells or code blocks for the profit ladder.
 */
export function formatOptionBSignalBody(card: SignalCardPostData, detail: SignalCardDetail): string {
  const dec = card.dec;
  const dirSign = card.dir === 'LONG' ? 1 : -1;
  const risk = Math.abs(card.entry - card.sl);
  const hasLimit = Math.abs(card.limit - card.entry) > 1e-9;
  const bePx = card.entry + dirSign * risk * 0.5;
  const slPctStr = `${card.riskPct.toFixed(2)}%`;

  const limitSummary = hasLimit
    ? `  ·  Pullback Limit: \`${fmtNum(card.limit, dec)}\`${detail.validUntil ? ` (until ${detail.validUntil})` : ''}`
    : '';

  const lines: string[] = [
    `- 📍 **Entry:** \`${fmtNum(card.entry, dec)}\`${limitSummary}`,
    `- 🛡️ **Stop Loss:** \`${fmtNum(card.sl, dec)}\` (${slPctStr}${card.atrMult ? ` · ${card.atrMult}` : ''})`,
    `- ⚖️ **Position Risk:** \`${card.sizeMult.toFixed(2)}%\` equity`,
    `- ⏱️ **Timeframe & Monitor:** \`${card.tf || '15m'}\` pure squeeze · Monitored live until close`,
    '',
    '---',
    '',
    '### 🪜 Profit & Protection Ladder',
    '',
  ];

  if (card.tp1Bank === 0) {
    lines.push(
      `🔐 **Breakeven** (+0.50R) ➜ \`${fmtNum(bePx, dec)}\` · Move stop to entry (+0.05R)`,
      `🔒 **Profit Lock** (+${card.r1.toFixed(2)}R) ➜ \`${fmtNum(card.tp1, dec)}\` · Lock +0.45R floor`,
      `📐 **Dynamic Trail** (+${card.r2.toFixed(2)}R) ➜ \`${fmtNum(card.tp2, dec)}\` · Trail momentum wave`,
      `🚀 **Runner Mode** (+${card.r3.toFixed(2)}R) ➜ \`${fmtNum(card.tp3, dec)}\` · Let trend run free`,
      ''
    );
  } else {
    lines.push(
      `🔐 **Breakeven** (+0.50R) ➜ \`${fmtNum(bePx, dec)}\` · Move stop toward entry`,
      `🔒 **TP1 / Lock** (+${card.r1.toFixed(2)}R) ➜ \`${fmtNum(card.tp1, dec)}\` · Bank **${card.tp1Bank}%**, move SL → Breakeven`,
      `📐 **TP2 / Trail** (+${card.r2.toFixed(2)}R) ➜ \`${fmtNum(card.tp2, dec)}\` · Bank **${card.tp2Bank}%**, move SL → TP1`,
      `🚀 **Runner Mode** (+${card.r3.toFixed(2)}R) ➜ \`${fmtNum(card.tp3, dec)}\` · Trail final **${card.tp3Bank}%** runner`,
      ''
    );
  }

  const readoutTitle = detail.confluenceSummary
    ? `### ⚡ 15m Scalp Readout (${detail.confluenceSummary})`
    : '### ⚡ 15m Scalp Readout';
  lines.push(readoutTitle, '');
  if (detail.regimeSummary) {
    lines.push(detail.regimeSummary, '');
  }
  if (detail.confluencePassed.length > 0) {
    for (const item of detail.confluencePassed.slice(0, 4)) {
      lines.push(`- ✅ ${item}`);
    }
    lines.push('');
  }
  if (detail.whySetup.length > 0) {
    for (const item of detail.whySetup.slice(0, 4)) {
      lines.push(`- 💡 ${item}`);
    }
    lines.push('');
  }

  if (detail.trackRecordSummary) {
    lines.push(`📒 **Track Record:** ${detail.trackRecordSummary}`, '');
  }

  lines.push(
    '---',
    `💬 *Follow this post: I monitor this scalp continuously until close and broadcast updates on key moves (🛡️ BE · 🔒 Lock · 📐 Trail · ⚡ Tighten · ⚠️ Risk · ✂️ Trim · 🏁 Exit).* Comment \`!follow\` to receive every update on this position or \`!calc ${card.sym} 1000 1\` for sizing. ⚠️ *Not financial advice.*`
  );

  return lines.join('\n').slice(0, 39000);
}

function parseNumberToken(raw: string | undefined): number {
  if (!raw) return NaN;
  const cleaned = raw.replace(/,/g, '').trim();
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : NaN;
}

function parsePythonScalpSignal(
  channelName: string,
  channelId: string,
  msg: SlackRawMessage,
  blocks: SlackBlock[],
  header: string,
  publishedEpoch: number
): ParsedSlackSignal | null {
  const dirMatch = header.match(/\b(LONG|SHORT)\b/i);
  if (!dirMatch) return null;
  const combined = blocks
    .map((b) => [b.text?.text ?? '', ...(b.fields ?? []).map((f) => f.text ?? ''), ...(b.elements ?? []).map((e) => (typeof e.text === 'string' ? e.text : ''))].join('\n'))
    .join('\n');
  if (/DESIGN PREVIEW/i.test(combined)) return null;
  if (!/SCALP EXECUTION PLAN|TRADE PLAN|PROFIT & PROTECTION LADDER|PROFIT LADDER/i.test(combined)) {
    return null;
  }

  const direction = dirMatch[1].toUpperCase() as 'LONG' | 'SHORT';
  const ch = getChannelDef(channelName);
  const plainHeader = slackToPlain(header);
  const afterDir = plainHeader.split('·')[1] ?? plainHeader;
  const name = afterDir.replace(/\([^)]*\)/g, '').trim() || channelName;

  const ctxBlock = blocks.find((b) => b.type === 'context' && (b.elements ?? []).some((e) => typeof e.text === 'string' && /🏷️|🆔/.test(e.text)));
  const ctxText = (ctxBlock?.elements ?? []).map((e) => (typeof e.text === 'string' ? slackToPlain(e.text) : '')).join(' · ');
  const symMatch = ctxText.match(/🏷️\s*([A-Za-z0-9_.-]+)/);
  const symbol = symMatch?.[1] ?? channelName.toUpperCase();
  const timeMatch = ctxText.match(/(\d{2}:\d{2}\s*UTC)/i);
  const timeUtc = timeMatch?.[1] ?? `${new Date(publishedEpoch * 1000).toISOString().slice(11, 16)} UTC`;
  const idMatch = ctxText.match(/🆔\s*([A-Za-z0-9_-]+)/);
  const signalId = idMatch?.[1] ?? `${symbol}-${publishedEpoch}-${direction}`;

  const gradeSection = slackToPlain(blocks.find((b) => b.type === 'section' && /\/100/.test(b.text?.text ?? ''))?.text?.text ?? '');
  const scoreMatch = gradeSection.match(/(\d+(?:\.\d+)?)\/100/);
  const score = scoreMatch ? Math.round(Number(scoreMatch[1])) : 75;
  const gradeMatch = gradeSection.match(/(?:^|\s)(A\+|A|B\+|B|C\+|C)(?=\s|$|·)/);
  const grade = gradeMatch?.[1] ?? 'B+';

  const planBlock = blocks.find((b) => b.type === 'section' && /SCALP EXECUTION PLAN|TRADE PLAN/i.test(b.text?.text ?? ''));
  let entry = NaN;
  let sl = NaN;
  let riskPct = 1.0;
  let sizeMult = 1.0;
  let decimals = 2;
  for (const f of planBlock?.fields ?? []) {
    const raw = slackToPlain(f.text ?? '');
    if (/Entry/i.test(raw)) {
      const m = raw.match(/([-+]?\d[\d,]*(?:\.\d+)?)/);
      entry = parseNumberToken(m?.[1]);
      const dp = m?.[1]?.split('.')[1]?.length ?? 2;
      decimals = Math.max(decimals, Math.min(6, dp));
    } else if (/Stop Loss/i.test(raw)) {
      const m = raw.match(/([-+]?\d[\d,]*(?:\.\d+)?)/);
      sl = parseNumberToken(m?.[1]);
      const pctM = raw.match(/\(([\d.]+)%\)/);
      if (pctM) riskPct = Number(pctM[1]) || 1.0;
    } else if (/Risk/i.test(raw)) {
      const pctM = raw.match(/([\d.]+)%/);
      if (pctM) sizeMult = Number(pctM[1]) || 1.0;
    }
  }
  if (!Number.isFinite(entry) || !Number.isFinite(sl)) return null;

  const riskDist = Math.abs(entry - sl);
  const dirSign = direction === 'LONG' ? 1 : -1;
  const ladderBlock = blocks.find((b) => b.type === 'section' && /PROFIT & PROTECTION LADDER|PROFIT LADDER/i.test(b.text?.text ?? ''));
  const ladderLines = (ladderBlock?.text?.text ?? '').split(/\r?\n/);
  const pickLevel = (kw: RegExp, fallbackR: number): number => {
    const line = ladderLines.find((l) => kw.test(l));
    const m = line?.match(/(?:➜|→)\s*`?([\d,]+(?:\.\d+)?)`?/);
    const parsed = parseNumberToken(m?.[1]);
    return Number.isFinite(parsed) ? parsed : entry + dirSign * riskDist * fallbackR;
  };
  const tp1 = pickLevel(/Profit Lock/i, 1.0);
  const tp2 = pickLevel(/Dynamic Trail/i, 1.5);
  const tp3 = pickLevel(/Runner/i, 2.0);
  const r1 = riskDist > 0 ? Number((Math.abs(tp1 - entry) / riskDist).toFixed(2)) : 1.0;
  const r2 = riskDist > 0 ? Number((Math.abs(tp2 - entry) / riskDist).toFixed(2)) : 1.5;
  const r3 = riskDist > 0 ? Number((Math.abs(tp3 - entry) / riskDist).toFixed(2)) : 2.0;

  const readoutBlock = blocks.find((b) => b.type === 'section' && /SCALP READOUT|WHY THIS TRADE/i.test(b.text?.text ?? ''));
  const readoutLines = (readoutBlock?.text?.text ?? '').split(/\r?\n/).map((l) => slackToPlain(l)).filter(Boolean);
  const checksHdr = readoutLines[0]?.match(/\(([^)]+)\)/)?.[1] ?? '';
  const summaryLine = readoutLines.find((l) => /Strength|val|Squeeze/i.test(l) && !l.startsWith('✅') && !l.startsWith('➖')) ?? '';
  const passedChecks = readoutLines.filter((l) => l.startsWith('✅')).map((l) => l.replace(/^✅\s*/, '').trim());

  const footerCtx = blocks[blocks.length - 1];
  const footerTexts = (footerCtx?.elements ?? []).map((e) => (typeof e.text === 'string' ? slackToPlain(e.text) : ''));
  const trackSummary = footerTexts.find((t) => t.includes('📒'))?.replace(/^📒\s*/, '').replace(/\s*·\s*⚠️\s*Not financial advice.*$/i, '').trim() ?? '';

  const gradeBadgeMap: Record<string, string> = {
    'A+': '💎 A+',
    A: '🏆 A',
    'B+': '🔥 B+',
    B: '✨ B',
    'C+': '⚡ C+',
    C: '📌 C',
  };
  const dot = direction === 'LONG' ? '🟢' : '🔴';
  const badge = gradeBadgeMap[grade] ?? grade;
  const title = `${dot} ${direction} · ${name} (${symbol}) · ${badge} · ${score}/100`.slice(0, 295);

  const card: SignalCardPostData = {
    kind: 'signal',
    signalId: signalId.slice(0, 64),
    sym: symbol.slice(0, 24),
    name: name.slice(0, 48),
    dir: direction,
    grade,
    score,
    ch: ch.id,
    chLabel: ch.flairText.slice(0, 32),
    chColor: ch.bgColor,
    entry,
    limit: entry,
    sl,
    tp1,
    tp2,
    tp3,
    dec: decimals,
    r1,
    r2,
    r3,
    tp1Bank: 0,
    tp2Bank: 0,
    tp3Bank: 0,
    atrMult: '',
    riskPct: riskDist > 0 && entry > 0 ? Number(((riskDist / entry) * 100).toFixed(2)) : riskPct,
    sizeMult,
    pipValue: 0.01,
    pipUnit: 'pts',
    regime: '⚡ 15m Scalp',
    trigger: '15m Squeeze Breakout',
    edge: trackSummary ? trackSummary.slice(0, 24) : '15m Live Track',
    tf: '15m',
    session: 'Live Deriv',
    timeUtc: timeUtc.slice(0, 16),
    horizon: 'Live until close',
    confluence: checksHdr,
    status: 'ACTIVE',
    rLabel: '',
    closed: false,
  };
  const detail: SignalCardDetail = {
    card,
    regimeSummary: summaryLine,
    validUntil: '',
    riskFields: [],
    mtfRows: [],
    confluenceSummary: checksHdr,
    confluencePassed: passedChecks,
    confluenceMissed: [],
    marketData: [],
    whySetup: [],
    warnings: [],
    trackRecordTitle: 'Track Record',
    trackRecordSummary: trackSummary,
    events: [],
  };
  const body = formatOptionBSignalBody(card, detail);
  return {
    kind: 'signal',
    ts: msg.ts,
    channelId,
    channelName,
    signalId: card.signalId,
    symbol,
    name,
    direction,
    grade,
    score,
    entry,
    sl,
    tp1,
    tp2,
    tp3,
    sizeMult,
    regimeCode: 'SCALP_15M',
    decimals,
    pipValue: 0.01,
    pipUnit: 'pts',
    title,
    body,
    publishedEpoch,
    card,
    detail,
  };
}

export function parseSlackMessage(
  channelName: string,
  channelId: string,
  msg: SlackRawMessage
): ParsedSlackItem | null {
  const blocks = extractBlocks(msg);
  if (!blocks.length) return null;
  const rawHeader = blocks[0]?.type === 'header' ? blocks[0].text?.text ?? '' : '';
  const header = slackToMarkdown(rawHeader);
  const firstSectionLine = slackToPlain((blocks[0]?.text?.text ?? '').split(/\r?\n/)[0] ?? '');
  const effHeader = header || firstSectionLine;
  const publishedEpoch = Math.floor(Number(msg.ts) || Date.now() / 1000);

  // 1. Signal Card (compact JSON ref OR native 15m Scalping Block Kit card)
  const ref = extractCompactRef(blocks);
  if (ref) {
    const scoreMatch = header.match(/Score\s+(\d+(?:\.\d+)?)\/100/i);
    const score = scoreMatch ? Number(scoreMatch[1]) : 75;
    const dot = ref.dir === 'LONG' ? '🟢' : '🔴';
    const title = `${dot} ${ref.dir} · ${ref.name} (${ref.sym}) · Grade ${ref.g} · Score ${Math.round(score)}/100`.slice(
      0,
      295
    );
    const { card, detail } = buildStructuredSignalCard(channelName, ref, score, publishedEpoch, blocks);
    const body = formatOptionBSignalBody(card, detail);
    return {
      kind: 'signal',
      ts: msg.ts,
      channelId,
      channelName,
      signalId: card.signalId,
      symbol: ref.sym,
      name: ref.name,
      direction: ref.dir,
      grade: ref.g,
      score,
      entry: ref.e,
      sl: ref.sl,
      tp1: ref.t1,
      tp2: ref.t2,
      tp3: ref.t3,
      sizeMult: ref.sm,
      regimeCode: ref.rc,
      decimals: ref.dec,
      pipValue: ref.pv,
      pipUnit: ref.pu,
      title,
      body,
      publishedEpoch,
      card,
      detail,
    };
  }

  const pythonScalp = parsePythonScalpSignal(channelName, channelId, msg, blocks, header, publishedEpoch);
  if (pythonScalp) {
    return pythonScalp;
  }

  // 2. Trade Lifecycle Update (reply or broadcast on a signal thread)
  const isLifecycleHeader =
    /BREAKEVEN SECURED|PROFIT LOCKED|TRAILING STOP MOVED|STOP TIGHTENED|PARTIAL TAKEN|RISK WARNING|DANGER ZONE|EXIT WARNING|REVERSAL WARNING|MOMENTUM WARNING|WIN ·|SCRATCH ·|LOSS CAPPED|PARAMETER|TP1 HIT|TP2 HIT|TP3 HIT|STOP LOSS|BREAKEVEN|TRAILING STOP|EARLY EXIT|MANUAL CLOSE|TIME EXPIRY/i.test(
      effHeader
    );
  if (isLifecycleHeader || (msg.thread_ts && msg.thread_ts !== msg.ts)) {
    const parentTs = msg.thread_ts || msg.ts;
    const body = blocksToRedditBody(blocks, false);
    const isFinal =
      /Trade complete|WIN ·|SCRATCH ·|LOSS CAPPED|stop hit|exited early|Thesis invalidated|capital freed|Emergency exit|Closed|TP3 HIT|STOP LOSS|BREAKEVEN EXIT|TRAILING STOP HIT|EARLY EXIT|MANUAL CLOSE|TIME EXPIRY/i.test(
        effHeader + ' ' + body
      );
    const rMatch = (msg.text || body).match(/([+-]\d+\.\d+R)/);
    const rLabel = rMatch ? rMatch[1] : '';
    const kindMatch = effHeader.match(
      /(PROFIT LOCKED|TRAILING STOP|BREAKEVEN|STOP TIGHTENED|PARTIAL|RISK WARNING|DANGER ZONE|EXIT WARNING|REVERSAL WARNING|MOMENTUM WARNING|WIN|SCRATCH|LOSS CAPPED|TP3|TP2|TP1|STOP LOSS|EARLY EXIT|TIME EXPIRY|MANUAL CLOSE|PARAMETER)/i
    );
    const kindTag = kindMatch ? kindMatch[1].toUpperCase() : 'UPDATE';
    return {
      kind: 'lifecycle',
      ts: msg.ts,
      parentTs,
      channelId,
      channelName,
      title: effHeader || 'Trade Lifecycle Update',
      body: body.slice(0, 9500),
      isFinal,
      rLabel,
      kindTag,
      publishedEpoch,
    };
  }

  // 3. 12-Hour Market Pulse or End-of-Day Desk Recap
  const isEod = /END-OF-DAY|EOD Recap/i.test(header);
  const isPulse = isEod || /MARKET PULSE|12-hour pulse|12-hour summary|12-hour desk|3-hour pulse|3-hour summary/i.test(header);
  if (isPulse) {
    const body = blocksToRedditBody(blocks, true);
    return {
      kind: 'pulse',
      ts: msg.ts,
      channelId,
      channelName,
      isEod,
      title: header.slice(0, 295),
      body: body.slice(0, 39000),
      publishedEpoch,
    };
  }

  return null;
}

export class SlackRelayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SlackRelayError';
  }
}

export class SlackRelayClient {
  private readonly token: string;

  constructor(token: string) {
    this.token = token.trim();
  }

  private async api<T>(endpoint: string): Promise<T> {
    if (!this.token) {
      throw new SlackRelayError(
        'Slack relay token is not configured yet. Run `npx devvit settings set slackBotToken`.'
      );
    }
    const resp = await fetch(`https://slack.com/api/${endpoint}`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${this.token}`,
        Accept: 'application/json',
      },
    });
    const data = (await resp.json()) as { ok?: boolean; error?: string } & T;
    if (!resp.ok || !data.ok) {
      throw new SlackRelayError(`Slack API ${endpoint.split('?')[0]} failed: ${data.error ?? resp.status}`);
    }
    return data;
  }

  async discoverChannels(): Promise<Record<string, string>> {
    const allowed = new Set(MARKET_CHANNELS.map((c) => c.id));
    const aliases: Record<string, string> = {
      'metals-commodities': 'metals',
    };
    const map: Record<string, string> = {};
    let cursor = '';
    for (let page = 0; page < 5; page++) {
      const qs = `conversations.list?types=public_channel&exclude_archived=true&limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`;
      const res = await this.api<{
        channels?: { id: string; name: string }[];
        response_metadata?: { next_cursor?: string };
      }>(qs);
      for (const ch of res.channels ?? []) {
        const rawName = ch.name.toLowerCase();
        const canonical = aliases[rawName] ?? rawName;
        if (allowed.has(canonical)) {
          map[canonical] = ch.id;
        }
      }
      cursor = res.response_metadata?.next_cursor ?? '';
      if (!cursor) break;
    }
    return map;
  }

  async fetchChannelHistory(channelId: string, limit = 12): Promise<SlackRawMessage[]> {
    const res = await this.api<{ messages?: SlackRawMessage[] }>(
      `conversations.history?channel=${encodeURIComponent(channelId)}&limit=${limit}`
    );
    return res.messages ?? [];
  }

  async fetchThreadReplies(channelId: string, parentTs: string, limit = 25): Promise<SlackRawMessage[]> {
    const res = await this.api<{ messages?: SlackRawMessage[] }>(
      `conversations.replies?channel=${encodeURIComponent(channelId)}&ts=${encodeURIComponent(parentTs)}&limit=${limit}`
    );
    return (res.messages ?? []).filter((m) => m.ts !== parentTs);
  }
}

export function computeLocalPositionSize(params: {
  entry: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  equity: number;
  riskPct: number;
  sizeMult?: number;
  pipValue?: number;
  pipUnit?: string;
}) {
  const equity = Math.max(1, params.equity);
  const baseRiskPct = Math.max(0.05, Math.min(25, params.riskPct));
  const sizeMult = Math.max(0.1, Math.min(2, params.sizeMult ?? 1));
  const effectiveRiskPct = baseRiskPct * sizeMult;
  const riskAmount = equity * (effectiveRiskPct / 100);
  const stopDistance = Math.abs(params.entry - params.sl);
  if (stopDistance <= 1e-12) {
    throw new Error('Entry and Stop Loss cannot be identical');
  }
  const units = riskAmount / stopDistance;
  const pipValue = params.pipValue && params.pipValue > 0 ? params.pipValue : 0.01;
  return {
    entry: params.entry,
    sl: params.sl,
    tp1: params.tp1,
    tp2: params.tp2,
    tp3: params.tp3,
    equity,
    base_risk_pct: baseRiskPct,
    size_mult: sizeMult,
    effective_risk_pct: effectiveRiskPct,
    risk_amount: riskAmount,
    stop_distance: stopDistance,
    units,
    stop_pips: stopDistance / pipValue,
    pip_unit: params.pipUnit || 'pts',
    tp1_profit: Math.abs(params.tp1 - params.entry) * units,
    tp2_profit: Math.abs(params.tp2 - params.entry) * units,
    tp3_profit: Math.abs(params.tp3 - params.entry) * units,
  };
}
