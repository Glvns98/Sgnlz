import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { MARKET_CHANNELS, getChannelDef } from '../src/shared/channels.js';
import type { PulseItemDto, SignalDto, TradeEventDto } from '../src/shared/types.js';
import { BridgeClient, BridgeClientError } from '../src/server/bridgeClient.js';
import {
  SlackRelayClient,
  SlackRelayError,
  computeLocalPositionSize,
  parseSlackMessage,
  slackToMarkdown,
} from '../src/server/slackRelay.js';
import {
  appendRedditMentions,
  buildLiveFlairText,
  formatChannelDirectoryBody,
  formatLifecycleComment,
  formatLifecycleDm,
  formatPulsePost,
  formatPushAlert,
  formatSignalPostBody,
  formatSignalPostTitle,
  isImportantLifecycleEvent,
  parseAlertUsernames,
} from '../src/server/formatters.js';

// Test pure helpers without importing Devvit runtime globals directly
const GRADE_RANK: Record<string, number> = { ALL: 0, C: 1, 'C+': 2, B: 3, 'B+': 4, A: 5, 'A+': 6 };
function gradeMeetsMin(grade: string, minGrade: string): boolean {
  const floor = GRADE_RANK[minGrade.toUpperCase()] ?? 0;
  return floor === 0 || (GRADE_RANK[grade] ?? 0) >= floor;
}
function parseCommentCommand(rawBody: string): { cmd: string; args: string[] } | null {
  const firstLine = rawBody.trim().split(/\r?\n/)[0]?.trim() ?? '';
  if (!firstLine.startsWith('!')) return null;
  const parts = firstLine.slice(1).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  const cmd = parts[0].toLowerCase();
  return [
    'scan',
    'calc',
    'status',
    'open',
    'stats',
    'edges',
    'help',
    'follow',
    'entered',
    'track',
    'notify',
    'unfollow',
    'exited',
  ].includes(cmd)
    ? { cmd, args: parts.slice(1) }
    : null;
}

const sampleSignal: SignalDto = {
  id: 'CRASH500-1700001000-LONG',
  published_epoch: 1700001900,
  symbol: 'CRASH500',
  name: 'Crash 500 Index',
  class_key: 'crash_boom',
  class_label: 'Crash / Boom',
  emoji: '💥',
  channel_name: 'crash-boom',
  decimals: 2,
  pip_value: 0.01,
  pip_unit: 'pts',
  is_forex: false,
  direction: 'LONG',
  score: 88.4,
  grade: 'A',
  triggers: ['Liquidity Sweep', 'IFVG Reclaim'],
  candle_epoch: 1700001000,
  close_epoch: 1700001900,
  bar_seconds: 900,
  timeframe: '15m',
  strategy_mode: 'v2_structure',
  edge_status: 'validated',
  net_of_costs: true,
  valid_until: 1700003700,
  session: '24/7 synthetic',
  plan: {
    entry: 4250.5,
    entry_limit: 4247.2,
    sl: 4238.0,
    tp1: 4265.5,
    tp2: 4278.0,
    tp3: 4294.25,
    risk: 12.5,
    atr: 8.2,
    tp_r: [1.2, 2.2, 3.5],
  },
  adaptation: {
    code: 'FULL_TREND',
    title: 'Full 1H + 4H Institutional Trend',
    badge: '🔥 FULL MTF TREND (v2)',
    sl_mult: 1.0,
    tp_mult: 1.2,
    size_mult: 1.0,
    partial_tp1: 0.4,
    early_be_r: null,
    trail_atr_mult: 1.5,
    horizon_bars: 96,
    summary: '15m structural trigger aligned with 1H & 4H Bullish trend.',
  },
  views: [
    { label: '15m', bias: 'Bullish', momentum: 'accelerating', squeeze: 'OFF', aligned: true },
    { label: '1h', bias: 'Bullish', momentum: 'accelerating, EMA up', squeeze: 'OFF', aligned: true },
    { label: '4h', bias: 'Bullish', momentum: 'accelerating, EMA up', squeeze: 'OFF', aligned: true },
  ],
  checks: [
    { label: 'Liquidity Sweep / Displacement', passed: true },
    { label: 'FVG / IFVG Imbalance', passed: true },
    { label: 'S/R Clean Room (>= 1.0 ATR)', passed: true },
  ],
  reasons: ['Swept 20-bar liquidity extreme and reclaimed with institutional displacement'],
  warnings: ['Spike-prone synthetic — stop-loss can experience gap slippage on spikes'],
  metrics: {
    val: 1.42,
    prev_val: 0.91,
    squeeze_state: 'OFF',
    squeeze_bars: 0,
    compression_pct: 0.35,
    atr_pct: 0.48,
    adx: 24.5,
    rsi: 58.2,
    ema_stack: 'Bullish stack (21>50>200)',
    resistance: [4290.0],
    support: [4230.0],
    range_24h_pos: 0.52,
  },
  edge_dir: {
    n: 12,
    tp1_rate: 0.67,
    win_rate: 0.67,
    avg_r: 0.34,
    shrunk_avg_r: 0.21,
    days: 9.5,
    consistent: true,
    validated: true,
  },
  explain: {
    title: 'Plain-English Breakdown',
    summary: 'Bullish setup',
    mtf_explain: 'All timeframes aligned',
    edge_explain: 'Proven edge',
    checklist: '1. Enter at market',
    warnings_text: '',
  },
};

describe('Cerynaean Hind Reddit App (r/GlvnsOrg)', () => {
  it('defines all 13 Slack-mirrored market channels with valid flair metadata', () => {
    expect(MARKET_CHANNELS).toHaveLength(13);
    expect(getChannelDef('crash-boom').flairText).toContain('#crash-boom');
    expect(getChannelDef('forex-majors').flairText).toContain('#forex-majors');
    expect(getChannelDef('unknown').id).toBe('trading');
    for (const ch of MARKET_CHANNELS) {
      expect(ch.flairText.length).toBeLessThanOrEqual(64);
      expect(ch.bgColor).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }
  });

  it('validates devvit.json targets r/GlvnsOrg, slack.com allowlist, and Option A signal card entrypoints', () => {
    const raw = readFileSync(path.resolve(__dirname, '../devvit.json'), 'utf-8');
    const cfg = JSON.parse(raw);
    expect(cfg.name).toBe('cerynaean-hind');
    expect(cfg.dev.subreddit).toBe('GlvnsOrg');
    expect(cfg.permissions.http.domains).toContain('slack.com');
    expect(cfg.post.entrypoints.signal.height).toBe('regular');
    expect(cfg.post.entrypoints['signal-detail'].entry).toBe('signal-detail.html');
    expect(cfg.post.entrypoints['signal-calc'].entry).toBe('signal-calc.html');
    expect(cfg.scheduler.tasks['sync-signals'].endpoint).toBe('/internal/scheduler/sync-signals');
    expect(cfg.scheduler.tasks['sync-signals'].cron).toBe('*/2 * * * *');
    expect(cfg.settings.global.slackBotToken.isSecret).toBe(true);
    expect(cfg.settings.global.slackBotToken.defaultValue).toBeUndefined();
    expect(cfg.settings.subreddit.redditMinGrade.defaultValue).toBe('B+');
    expect(cfg.settings.subreddit.alertUsernames.defaultValue).toBe('');
    expect(cfg.settings.subreddit.dmPositionUpdates.defaultValue).toBe(true);
    expect(cfg.settings.subreddit.mentionOnUpdates.defaultValue).toBe(true);
    const menuEndpoints = (cfg.menu?.items ?? []).map((m: { endpoint: string }) => m.endpoint);
    expect(menuEndpoints).toContain('/internal/menu/enable-alerts');
    expect(menuEndpoints).toContain('/internal/menu/disable-alerts');
  });

  it('formats Strategy v2 Signal Card post title & body within Reddit limits', () => {
    const title = formatSignalPostTitle(sampleSignal);
    const body = formatSignalPostBody(sampleSignal);
    expect(title.length).toBeLessThanOrEqual(300);
    expect(title).toContain('LONG');
    expect(title).toContain('CRASH500');
    expect(title).toContain('v2 15m');
    expect(body).toContain('TP1 (1.20R)');
    expect(body).toContain('TP2 (2.20R)');
    expect(body).toContain('TP3 (3.50R)');
    expect(body).toContain('net of spread & gap costs');
    expect(body).toContain('!calc CRASH500 1000 1');

    const v3Title = formatSignalPostTitle({
      ...sampleSignal,
      symbol: 'frxEURUSD',
      name: 'EUR/USD',
      timeframe: '1h',
      bar_seconds: 3600,
      strategy_mode: 'v3_regime',
    });
    const v3Body = formatSignalPostBody({
      ...sampleSignal,
      symbol: 'frxEURUSD',
      name: 'EUR/USD',
      timeframe: '1h',
      bar_seconds: 3600,
      strategy_mode: 'v3_regime',
    });
    expect(v3Title).toContain('v3 1h');
    expect(v3Body).toContain('Strategy v3 (1H Regime)');
  });

  it('formats every lifecycle comment cleanly', () => {
    const baseEv: TradeEventDto = {
      event_id: 'ev1',
      signal_id: sampleSignal.id,
      timestamp: 1700002500,
      channel_name: 'crash-boom',
      symbol: 'CRASH500',
      name: 'Crash 500 Index',
      direction: 'LONG',
      kind: 'TP1',
      r_total: 0.48,
      price: 4265.5,
      final: false,
      old_sl: 4238.0,
      new_sl: 4250.5,
      old_tp: null,
      new_tp: null,
      reason: 'TP1 reached',
      entry: 4250.5,
      active_sl: 4250.5,
      tp1: 4265.5,
      tp2: 4278.0,
      tp3: 4294.25,
      stage: 1,
      bars: 3,
      horizon: 96,
      decimals: 2,
      regime_code: 'FULL_TREND',
      partial_tp1: 0.4,
      partial_tp2: 0.3,
      runner_fraction: 0.3,
    };
    for (const kind of ['TP1', 'TP2', 'TP3', 'PARAM_BE', 'PARAM_TRAIL', 'SL', 'BE', 'INVALIDATED', 'TIME']) {
      const md = formatLifecycleComment({ ...baseEv, kind, final: !['TP1', 'TP2', 'PARAM_BE', 'PARAM_TRAIL'].includes(kind) });
      expect(md).toContain('Do now:');
      expect(md).toContain('4,250.50');
    }
  });

  it('formats 12-hour Market Pulse and 00:00 UTC EOD Desk Recap posts', () => {
    const eodPulse: PulseItemDto = {
      id: 'eod-2026-11-19',
      kind: 'eod_recap',
      timestamp: 1700000000,
      trading_day: '2026-11-19',
      data: {
        trading_day: '2026-11-19',
        stats: {
          n: 4,
          wins: 3,
          losses: 1,
          breakevens: 0,
          tp1_hits: 3,
          win_rate: 0.75,
          tp1_rate: 0.75,
          total_r: 2.4,
          avg_r: 0.6,
          profit_factor: 3.4,
          profit_factor_inf: false,
          max_drawdown_r: 1.0,
          best_r: 1.5,
          worst_r: -1.0,
          gross_win_r: 3.4,
          gross_loss_r: 1.0,
          current_streak: 2,
          avg_bars: 8,
          by_regime: [{ label: 'FULL_TREND', n: 4, win_rate: 0.75, total_r: 2.4, avg_r: 0.6 }],
          by_grade: [],
          by_class: [],
          top_symbols: [],
        },
        open_trades: [],
        signals_sent: 10,
        signals_filtered: 22,
        pulses_sent: 8,
      },
    };
    const eod = formatPulsePost(eodPulse);
    expect(eod.title).toContain('END-OF-DAY DESK RECAP · 2026-11-19 UTC');
    expect(eod.body).toContain('+2.40R');

    const dirBody = formatChannelDirectoryBody('GlvnsOrg');
    expect(dirBody).toContain('r/GlvnsOrg');
    expect(dirBody).toContain('#crash-boom');
    expect(dirBody).toContain('#forex-majors');
    expect(dirBody).toContain('!scan <symbol>');
  });

  it('parses comment commands and enforces grade floors accurately', () => {
    expect(parseCommentCommand('!scan CRASH500')).toEqual({ cmd: 'scan', args: ['CRASH500'] });
    expect(parseCommentCommand('!calc R_100 1000 1.5')).toEqual({ cmd: 'calc', args: ['R_100', '1000', '1.5'] });
    expect(parseCommentCommand('!stats')).toEqual({ cmd: 'stats', args: [] });
    expect(parseCommentCommand('!follow')).toEqual({ cmd: 'follow', args: [] });
    expect(parseCommentCommand('!entered')).toEqual({ cmd: 'entered', args: [] });
    expect(parseCommentCommand('!unfollow')).toEqual({ cmd: 'unfollow', args: [] });
    expect(parseCommentCommand('Great trade!')).toBeNull();
    expect(parseCommentCommand('!unknown')).toBeNull();

    expect(gradeMeetsMin('A+', 'A')).toBe(true);
    expect(gradeMeetsMin('B+', 'A')).toBe(false);
    expect(gradeMeetsMin('B', 'B')).toBe(true);
    expect(gradeMeetsMin('C', 'ALL')).toBe(true);
  });

  it('BridgeClient fails cleanly when token is missing and passes Bearer header when configured', async () => {
    const noTok = new BridgeClient({ baseUrl: 'https://example.com', token: '' });
    await expect(noTok.getOverview()).rejects.toThrow(BridgeClientError);

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ signals: [], events: [], pulses: [], server_epoch: 123 }),
    });
    vi.stubGlobal('fetch', mockFetch);
    const client = new BridgeClient({ baseUrl: 'https://deriv-squeeze-bot-production.up.railway.app', token: 'sec123' });
    const feed = await client.getFeed({ sinceEpoch: 10, minGrade: 'A' });
    expect(feed.server_epoch).toBe(123);
    expect(mockFetch).toHaveBeenCalledWith(
      'https://deriv-squeeze-bot-production.up.railway.app/api/feed?since_epoch=10&min_grade=A',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sec123' }),
      })
    );
    vi.unstubAllGlobals();
  });

  it('converts Slack markdown/entities, builds compact Option A Signal Card postData (<2KB), and sizes positions locally', async () => {
    expect(slackToMarkdown(':large_green_circle: *LONG* `CRASH200` &amp; 1H &gt; 4H in <#C123|crash-boom>')).toBe(
      '🟢 **LONG** `CRASH200` & 1H > 4H in #crash-boom'
    );

    const parsedSig = parseSlackMessage('crash-boom', 'C0CJWBE5E2Z', {
      ts: '1795131002.542149',
      attachments: [
        {
          blocks: [
            {
              type: 'header',
              text: { text: ':large_green_circle: LONG · Crash 200 Index  ·  Grade B+ High-Confluence  ·  Score 78/100' },
            },
            {
              type: 'context',
              elements: [
                {
                  type: 'mrkdwn',
                  text: 'Crash/Boom Index · `CRASH200` · Strategy v2 (15m) · :white_check_mark: Proven Edge · 24/7 synthetic · 00:45',
                },
              ],
            },
            {
              type: 'section',
              text: {
                text: '&gt; *:fire: Full 1H + 4H institutional trend (v2).* 15m structural trigger aligned with 1H &amp; 4H bullish flow.',
              },
            },
            {
              type: 'section',
              text: {
                text: '*Trade plan*  ·  _Liquidity Sweep + IFVG Reclaim_\n*Entry:* `5,000.00`  ·  market\n*Limit:* `4,995.50`  ·  pullback (`-4.50` · valid until 01:15 UTC)\n*SL:* `4,980.00`  ·  `-20.00` · `1.8× ATR`\n*TP1:* `5,024.00`  ·  `1.20R` (`+24.00` · bank 40%, SL → BE)\n*TP2:* `5,044.00`  ·  `2.20R` (`+44.00` · bank 30%, SL → TP1)',
              },
            },
            {
              type: 'section',
              text: { text: '*Risk & sizing*' },
              fields: [
                { type: 'mrkdwn', text: '*Risk per unit*\n`20.00` (0.40%)' },
                { type: 'mrkdwn', text: '*Horizon*\n`24h`' },
              ],
            },
            {
              type: 'section',
              text: { text: '*Confluence · 8 of 9 passed*\n:white_check_mark: Liquidity Sweep · IFVG Imbalance' },
            },
            {
              type: 'section',
              text: { text: '*Why this setup*\n• Swept 20-bar liquidity extreme\n• Reclaimed opposing FVG' },
            },
            {
              type: 'actions',
              elements: [
                {
                  action_id: 'signal_track_entry',
                  value: JSON.stringify({
                    id: 'CRASH200-1795130100-LONG',
                    sym: 'CRASH200',
                    name: 'Crash 200 Index',
                    dir: 'LONG',
                    e: 5000,
                    sl: 4980,
                    t1: 5024,
                    t2: 5044,
                    t3: 5070,
                    sm: 1.0,
                    g: 'B+',
                    rc: 'FULL_TREND',
                    dec: 2,
                    pv: 0.01,
                    pu: 'pts',
                  }),
                },
              ],
            },
          ],
        },
      ],
    });
    expect(parsedSig?.kind).toBe('signal');
    if (parsedSig?.kind === 'signal') {
      expect(parsedSig.symbol).toBe('CRASH200');
      expect(parsedSig.grade).toBe('B+');
      expect(parsedSig.score).toBe(78);
      expect(parsedSig.title).toContain('🟢 LONG · Crash 200 Index (CRASH200)');
      expect(parsedSig.card.kind).toBe('signal');
      expect(parsedSig.card.trigger).toBe('Liquidity Sweep + IFVG Reclaim');
      expect(parsedSig.card.limit).toBe(4995.5);
      expect(parsedSig.card.atrMult).toBe('1.8× ATR');
      expect(parsedSig.card.confluence).toBe('8/9 Passed');
      expect(parsedSig.card.edge).toBe('✅ Proven Edge');
      expect(parsedSig.card.horizon).toBe('24h');
      expect(parsedSig.detail.whySetup).toHaveLength(2);
      // Must fit comfortably under Devvit's 2 KB postData limit
      expect(JSON.stringify(parsedSig.card).length).toBeLessThan(1800);
    }

    // Regression check: "Unvalidated Edge" must NOT falsely match "Validated" as Proven Edge,
    // MTF details must not duplicate status pills, missed confluence must not repeat "Missed:", and long triggers must not clip
    const unvalSig = parseSlackMessage('jump-indices', 'C0CK3GTA76V', {
      ts: '1795131500.000001',
      attachments: [
        {
          blocks: [
            { type: 'header', text: { text: ':red_circle: SHORT · Jump 100 Index · Grade A · Score 82/100' } },
            {
              type: 'context',
              elements: [
                {
                  type: 'mrkdwn',
                  text: 'Jump Index · `JD100` · Strategy v2 (15m) · :warning: Unvalidated Edge · 24/7 synthetic · 00:45',
                },
              ],
            },
            {
              type: 'section',
              text: { text: '*Trade plan*  ·  _Liquidity Sweep + FVG Displacement + IFVG Reclaim_' },
            },
            {
              type: 'section',
              text: {
                text: '*Timeframe alignment*\n:red_circle: *15m Trigger* · Bearish, accelerating · :warning: *Opposing*\n:large_green_circle: *1H Trend* · Bullish, EMA up · :white_check_mark: Aligned',
              },
            },
            {
              type: 'section',
              text: {
                text: '*Confluence · 8 of 9 passed*\n:white_check_mark: Liquidity Sweep\n:white_small_square: Missed: 15m Momentum Accelerating',
              },
            },
            {
              type: 'actions',
              elements: [
                {
                  action_id: 'signal_track_entry',
                  value: JSON.stringify({
                    id: 'JD100-1795131500-SHORT',
                    sym: 'JD100',
                    name: 'Jump 100 Index',
                    dir: 'SHORT',
                    e: 50000,
                    sl: 50200,
                    t1: 49760,
                    t2: 49560,
                    t3: 49300,
                    sm: 0.85,
                    g: 'A',
                    rc: 'HTF_PULLBACK',
                    dec: 2,
                    pv: 0.01,
                    pu: 'pts',
                  }),
                },
              ],
            },
          ],
        },
      ],
    });
    expect(unvalSig?.kind).toBe('signal');
    if (unvalSig?.kind === 'signal') {
      expect(unvalSig.card.edge).toBe('⚠️ Unvalidated Edge');
      expect(unvalSig.card.trigger).toBe('Liquidity Sweep + FVG Displacement + IFVG Reclaim');
      expect(unvalSig.detail.mtfRows[0].detail).toBe('Bearish, accelerating');
      expect(unvalSig.detail.mtfRows[1].detail).toBe('Bullish, EMA up');
      expect(unvalSig.detail.confluenceMissed).toEqual(['15m Momentum Accelerating']);
      // Verify Option B Clean Native Text Post formatting (15m Scalping + Plain-Text Ladder, no MTF, no tables/cells)
      expect(unvalSig.body.startsWith('- 📍 **Entry:**')).toBe(true);
      expect(unvalSig.body).toContain('- 🛡️ **Stop Loss:**');
      expect(unvalSig.body).toContain('- ⚖️ **Position Risk:**');
      expect(unvalSig.body).not.toContain('**🦘 #jump-indices**');
      expect(unvalSig.body).not.toMatch(/^>/m);
      expect(unvalSig.body).toContain('### 🪜 Profit & Protection Ladder');
      expect(unvalSig.body).toContain('### ⚡ 15m Scalp Readout');
      expect(unvalSig.body).not.toContain('### 🧭 Multi-Timeframe Alignment');
      expect(unvalSig.body).not.toContain('| Level |');
      expect(unvalSig.body).not.toContain('```');
      expect(unvalSig.body).toContain('🔐 **Breakeven**');
      expect(unvalSig.body).toContain('Monitored live until close');
    }

    // Verify native Python 15m Scalping Slack card + plain-text ladder + lifecycle reply parsing
    const pyScalp = parseSlackMessage('volatility-indices', 'C0CJWBD99QD', {
      ts: '1795565727.906759',
      blocks: [
        { type: 'header', text: { type: 'plain_text', text: '🟢 LONG  ·  Volatility 100 Index (15m Scalp)' } },
        {
          type: 'context',
          elements: [{ type: 'mrkdwn', text: '🏷️ `R_100`  ·  ⚡ `15m Scalp`  ·  📈 #volatility-indices  ·  ⏰ 02:25 UTC  ·  🆔 `TRD-SCALP1`' }],
        },
        { type: 'section', text: { type: 'mrkdwn', text: '🔥 B+  ⭐⭐⭐⭐☆  ·  *78/100*\n🚀 Expansion Trend  ·  💥 15m Squeeze Breakout' } },
        {
          type: 'section',
          text: { type: 'mrkdwn', text: '*🎯 SCALP EXECUTION PLAN*' },
          fields: [
            { type: 'mrkdwn', text: '*📍 Entry*\n`1,653.50`' },
            { type: 'mrkdwn', text: '*🛡️ Stop Loss*\n`1,622.00` (1.90%)' },
            { type: 'mrkdwn', text: '*⚖️ Risk*\n`1.00%` equity' },
            { type: 'mrkdwn', text: '*⏱️ Monitor*\n`15m` · Live until close' },
          ],
        },
        {
          type: 'section',
          text: {
            type: 'mrkdwn',
            text: '*🪜 PROFIT & PROTECTION LADDER*\n🔐 *Breakeven* (+0.50R) ➜ `1,669.25` · Move stop to entry (+0.05R)\n🔒 *Profit Lock* (+1.00R) ➜ `1,685.00` · Lock +0.45R floor\n📐 *Dynamic Trail* (+1.50R) ➜ `1,700.75` · Trail momentum wave\n🚀 *Runner Mode* (+2.00R) ➜ `1,716.50` · Let trend run free',
          },
        },
        {
          type: 'section',
          text: {
            type: 'mrkdwn',
            text: '*⚡ 15m SCALP READOUT*  (4/6 checks)\n⚪ Squeeze Released  ·  🟢 Lime (Surging Up)  ·  `val` `+7.1658`  ·  Strength `0.84`  ·  💥 Fired\n✅ Squeeze Release / Expansion Active\n✅ Strong 15m Squeeze Momentum',
          },
        },
        {
          type: 'context',
          elements: [{ type: 'mrkdwn', text: '📒 First recorded 15m setup  ·  ⚠️ Not financial advice' }],
        },
      ],
    });
    expect(pyScalp?.kind).toBe('signal');
    if (pyScalp?.kind === 'signal') {
      expect(pyScalp.symbol).toBe('R_100');
      expect(pyScalp.direction).toBe('LONG');
      expect(pyScalp.grade).toBe('B+');
      expect(pyScalp.score).toBe(78);
      expect(pyScalp.entry).toBeCloseTo(1653.5, 2);
      expect(pyScalp.sl).toBeCloseTo(1622.0, 2);
      expect(pyScalp.body).toContain('### 🪜 Profit & Protection Ladder');
      expect(pyScalp.body).toContain('🔐 **Breakeven** (+0.50R) ➜ `1,669.25`');
      expect(pyScalp.body).not.toContain('```');
      expect(pyScalp.body).not.toContain('| Level |');
    }

    const pyWin = parseSlackMessage('volatility-indices', 'C0CJWBD99QD', {
      ts: '1795566637.788739',
      thread_ts: '1795565727.906759',
      blocks: [
        { type: 'section', text: { type: 'mrkdwn', text: '*🎉 WIN · +1.71R*  ·  🟢 LONG `R_100`\n📐 Trailing stop hit' } },
        { type: 'section', text: { type: 'mrkdwn', text: '> 💡 Clean execution: profits banked. 🥂' } },
      ],
    });
    expect(pyWin?.kind).toBe('lifecycle');
    if (pyWin?.kind === 'lifecycle') {
      expect(pyWin.isFinal).toBe(true);
      expect(pyWin.rLabel).toBe('+1.71R');
    }

    const parsedEv = parseSlackMessage('crash-boom', 'C0CJWBE5E2Z', {
      ts: '1795131901.276179',
      thread_ts: '1795131002.542149',
      subtype: 'thread_broadcast',
      attachments: [
        {
          blocks: [
            { type: 'header', text: { text: ':dart: TP1 HIT · Bank 40% and move stop to breakeven' } },
            { type: 'section', text: { text: 'Realized +0.48R so far.' } },
          ],
        },
      ],
    });
    expect(parsedEv?.kind).toBe('lifecycle');
    if (parsedEv?.kind === 'lifecycle') {
      expect(parsedEv.parentTs).toBe('1795131002.542149');
      expect(parsedEv.kindTag).toBe('TP1');
      expect(parsedEv.rLabel).toBe('+0.48R');
      expect(parsedEv.isFinal).toBe(false);
    }

    const parsedPulse = parseSlackMessage('trading', 'C09C9FGM8QY', {
      ts: '1795122006.861489',
      blocks: [
        { type: 'header', text: { text: ':globe_with_meridians: DERIV MARKET PULSE  ·  12-Hour Desk Brief (1/2)' } },
        { type: 'section', text: { text: '*Breadth:* 42 bull / 43 bear' } },
      ],
    });
    expect(parsedPulse?.kind).toBe('pulse');
    if (parsedPulse?.kind === 'pulse') {
      expect(parsedPulse.isEod).toBe(false);
      expect(parsedPulse.title).toContain('🌐 DERIV MARKET PULSE');
    }

    const sized = computeLocalPositionSize({
      entry: 5000,
      sl: 4980,
      tp1: 5024,
      tp2: 5044,
      tp3: 5070,
      equity: 1000,
      riskPct: 1.0,
      sizeMult: 1.0,
    });
    expect(sized.risk_amount).toBeCloseTo(10, 4);
    expect(sized.units).toBeCloseTo(0.5, 4);
    expect(sized.tp1_profit).toBeCloseTo(12, 4);

    const noTok = new SlackRelayClient('');
    await expect(noTok.discoverChannels()).rejects.toThrow(SlackRelayError);
  });

  it('parses broadcasted management and early-warning thread replies as non-final lifecycle updates', () => {
    const cases = [
      {
        header: '*🛡️ BREAKEVEN SECURED*  ·  🟢 LONG `R_100`\n`▰▰▱▱▱▱▱▱▱▱`  *+0.55R*',
        tag: 'BREAKEVEN',
      },
      {
        header: '*⚠️ RISK WARNING · DRAWDOWN*  ·  🟢 LONG `R_100`\n📉 Live PnL *-0.55R*  ·  📏 Buffer to Stop `0.45R`',
        tag: 'RISK WARNING',
      },
      {
        header: '*🚨 DANGER ZONE · NEAR STOP*  ·  🟢 LONG `R_100`\n📉 Live PnL *-0.80R*  ·  📏 Buffer to Stop `0.20R`',
        tag: 'DANGER ZONE',
      },
      {
        header: '*⚠️ REVERSAL WARNING · MOMENTUM SHIFT*  ·  🟢 LONG `R_100`\n📉 Live PnL *+0.10R*  ·  📏 Buffer to Stop `1.10R`',
        tag: 'REVERSAL WARNING',
      },
      {
        header: '*🚨 EXIT WARNING · STRONG REVERSAL*  ·  🟢 LONG `R_100`\n📉 Live PnL *-0.15R*  ·  📏 Buffer to Stop `0.85R`',
        tag: 'EXIT WARNING',
      },
      {
        header: '*⚠️ MOMENTUM WARNING · FADING*  ·  🟢 LONG `R_100`\n📉 Live PnL *+0.20R*  ·  📏 Buffer to Stop `1.20R`',
        tag: 'MOMENTUM WARNING',
      },
    ];

    for (let i = 0; i < cases.length; i++) {
      const c = cases[i];
      const parsed = parseSlackMessage('volatility-indices', 'C0CJWBD99QD', {
        ts: `179557000${i}.000100`,
        thread_ts: '1795565727.906759',
        subtype: 'thread_broadcast',
        blocks: [
          { type: 'section', text: { type: 'mrkdwn', text: c.header } },
          { type: 'section', text: { type: 'mrkdwn', text: '> 💡 Watch protective stop closely.' } },
        ],
      });
      expect(parsed?.kind).toBe('lifecycle');
      if (parsed?.kind === 'lifecycle') {
        expect(parsed.isFinal).toBe(false);
        expect(parsed.parentTs).toBe('1795565727.906759');
        expect(parsed.kindTag).toBe(c.tag);
      }
    }
  });

  it('formats Reddit mentions (max 3), live post flairs, DM alerts, and push alerts cleanly', () => {
    expect(parseAlertUsernames('u/Gelvinus, /u/trader_one, gelvinus; cerynaean-hind, bad!name, TraderTwo')).toEqual([
      'Gelvinus',
      'trader_one',
      'TraderTwo',
    ]);

    expect(buildLiveFlairText('📈 #volatility-indices', 'RISK WARNING', '-0.55R')).toBe(
      '📈 #volatility-indices · RISK WARNING (-0.55R)'
    );
    expect(buildLiveFlairText('📈 #volatility-indices', 'TRAILING STOP MOVED', '+1.50R').length).toBeLessThanOrEqual(64);

    const mentioned = appendRedditMentions('### ⚠️ RISK WARNING', ['u/Gelvinus', 'gelvinus', 'u2', 'u3', 'u4']);
    expect(mentioned).toContain('u/Gelvinus · u/u2 · u/u3');
    expect(mentioned).not.toContain('u/u4');
    expect(appendRedditMentions('### ⚠️ RISK WARNING', [])).toBe('### ⚠️ RISK WARNING');

    const dm = formatLifecycleDm({
      subredditName: 'GlvnsOrg',
      postId: 't3_abc123',
      commentId: 't1_xyz789',
      title: 'LONG Volatility 100 Index (R_100)',
      kindTag: 'RISK WARNING',
      rLabel: '-0.55R',
      body: '### ⚠️ RISK WARNING\nWatch stop closely.',
    });
    expect(dm.subject.length).toBeLessThanOrEqual(100);
    expect(dm.subject).toContain('[r/GlvnsOrg] RISK WARNING (-0.55R)');
    expect(dm.text).toContain('https://www.reddit.com/r/GlvnsOrg/comments/abc123/_/xyz789/');
    expect(dm.text).toContain('!unfollow');

    const push = formatPushAlert({
      title: 'LONG Volatility 100 Index (R_100)',
      kindTag: 'DANGER ZONE',
      rLabel: '-0.80R',
      body: '### 🚨 DANGER ZONE\n> 💡 **Watch stop**\n`1,622.00`',
    });
    expect(push.title.length).toBeLessThanOrEqual(100);
    expect(push.body.length).toBeLessThanOrEqual(200);
    expect(push.body).not.toContain('###');
    expect(push.body).not.toContain('`');

    expect(isImportantLifecycleEvent('BREAKEVEN SECURED', false)).toBe(true);
    expect(isImportantLifecycleEvent('RISK WARNING', false)).toBe(true);
    expect(isImportantLifecycleEvent('DANGER ZONE', false)).toBe(true);
    expect(isImportantLifecycleEvent('REVERSAL WARNING', false)).toBe(true);
    expect(isImportantLifecycleEvent('EXIT WARNING', false)).toBe(true);
    expect(isImportantLifecycleEvent('UPDATE', true)).toBe(true);
    expect(isImportantLifecycleEvent('UPDATE', false)).toBe(false);
    expect(isImportantLifecycleEvent('HOLD', false)).toBe(false);
    expect(isImportantLifecycleEvent('MOMENTUM WARNING', false)).toBe(false);
  });

  it('preserves (1s) volatility index naming on Slack relay and parses TREND WEAKENING updates', () => {
    const headers = [
      { hdr: '🟢 LONG  ·  Volatility 15 (1s) Index (1HZ15V)  ·  5m Scalp', tf: '5m' },
      { hdr: '🟢 LONG  ·  Volatility 15 (1s) Index (15m Scalp)', tf: '15m' },
    ];
    for (const { hdr, tf } of headers) {
      const parsed = parseSlackMessage('volatility-indices', 'C0CJWBD99QD', {
        ts: '1795571000.000100',
        blocks: [
          { type: 'header', text: { type: 'plain_text', text: hdr } },
          {
            type: 'context',
            elements: [{ type: 'mrkdwn', text: `🏷️ \`1HZ15V\`  ·  ⚡ \`${tf} Scalp\`  ·  📈 #volatility-indices  ·  ⏰ 12:00 UTC  ·  🆔 \`TRD-1S15\`` }],
          },
          { type: 'section', text: { type: 'mrkdwn', text: '🔥 B+  ⭐⭐⭐⭐☆  ·  *78/100*\n🚀 Expansion Trend' } },
          {
            type: 'section',
            text: { type: 'mrkdwn', text: '*🎯 SCALP EXECUTION PLAN*' },
            fields: [
              { type: 'mrkdwn', text: '*📍 Entry*\n`2,450.00`' },
              { type: 'mrkdwn', text: '*🛡️ Stop Loss*\n`2,430.00` (0.82%)' },
              { type: 'mrkdwn', text: '*⚖️ Risk*\n`1.00%` equity' },
            ],
          },
          {
            type: 'section',
            text: {
              type: 'mrkdwn',
              text: '*🪜 PROFIT & PROTECTION LADDER*\n🔐 *Breakeven* (+0.50R) ➜ `2,460.00`\n🔒 *Profit Lock* (+1.00R) ➜ `2,470.00`',
            },
          },
        ],
      });
      expect(parsed?.kind).toBe('signal');
      if (parsed?.kind === 'signal') {
        expect(parsed.symbol).toBe('1HZ15V');
        expect(parsed.name).toBe('Volatility 15 (1s) Index');
        expect(parsed.card.tf).toBe(tf);
        expect(parsed.body).toContain(`### ⚡ ${tf} Scalp Readout`);
        expect(parsed.title).toContain('Volatility 15 (1s) Index (1HZ15V)');
      }
    }

    const otherAssets = [
      { sym: 'stpRNG2', raw: 'Step 200', expected: 'Step Index 200', ch: 'step-indices' },
      { sym: 'BOOM150N', raw: 'Boom 150N', expected: 'Boom 150 Index', ch: 'crash-boom' },
      { sym: 'frxXAUUSD', raw: 'Gold / USD', expected: 'Gold/USD', ch: 'metals' },
      { sym: 'OTC_NDX', raw: 'US Tech 100', expected: 'US Tech 100', ch: 'stock-indices' },
    ];
    for (const item of otherAssets) {
      const parsed = parseSlackMessage(item.ch, 'C0TEST', {
        ts: '1795572000.000100',
        blocks: [
          { type: 'header', text: { type: 'plain_text', text: `🟢 LONG  ·  ${item.raw} (${item.sym})  ·  5m Scalp` } },
          {
            type: 'context',
            elements: [{ type: 'mrkdwn', text: `🏷️ \`${item.sym}\`  ·  ⚡ \`5m Scalp\`  ·  ⏰ 12:00 UTC  ·  🆔 \`TRD-${item.sym}\`` }],
          },
          {
            type: 'section',
            text: { type: 'mrkdwn', text: '*🎯 SCALP EXECUTION PLAN*' },
            fields: [
              { type: 'mrkdwn', text: '*📍 Entry*\n`100.00`' },
              { type: 'mrkdwn', text: '*🛡️ Stop Loss*\n`98.00` (2.00%)' },
            ],
          },
        ],
      });
      expect(parsed?.kind).toBe('signal');
      if (parsed?.kind === 'signal') {
        expect(parsed.name).toBe(item.expected);
        expect(parsed.card.tf).toBe('5m');
      }
    }

    const weakEv = parseSlackMessage('volatility-indices', 'C0CJWBD99QD', {
      ts: '1795571050.000100',
      thread_ts: '1795571000.000100',
      subtype: 'thread_broadcast',
      blocks: [
        {
          type: 'section',
          text: {
            type: 'mrkdwn',
            text: '*🟡 TREND WEAKENING · MOMENTUM FADING*  ·  🟢 LONG · Volatility 15 (1s) Index (`1HZ15V`)\n📉 Live PnL *+0.65R*  ·  📏 Buffer to Stop `0.60R`',
          },
        },
        { type: 'section', text: { type: 'mrkdwn', text: '> 💡 Histogram shifted from bright to dark.' } },
      ],
    });
    expect(weakEv?.kind).toBe('lifecycle');
    if (weakEv?.kind === 'lifecycle') {
      expect(weakEv.isFinal).toBe(false);
      expect(weakEv.kindTag).toBe('TREND WEAKENING');
    }
  });
});
