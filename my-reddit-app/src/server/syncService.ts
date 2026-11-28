import { notifications, reddit, redis, settings } from '@devvit/web/server';
import type { T1, T2, T3 } from '@devvit/shared-types/tid.js';
import { MARKET_CHANNELS, getChannelDef } from '../shared/channels.js';
import type {
  BridgeOverviewDto,
  CalcResultDto,
  ChannelViewDto,
  JournalRecord,
  ScanResultDto,
  SignalCardDetail,
  SignalCardPostData,
  SignalDto,
} from '../shared/types.js';
import {
  appendRedditMentions,
  buildLiveFlairText,
  formatCalcComment,
  formatChannelDirectoryBody,
  formatHelpComment,
  formatLifecycleDm,
  formatPushAlert,
  isImportantLifecycleEvent,
  parseAlertUsernames,
} from './formatters.js';
import {
  type ParsedSlackLifecycle,
  type ParsedSlackPulse,
  type ParsedSlackSignal,
  SlackRelayClient,
  SlackRelayError,
  computeLocalPositionSize,
  formatOptionBSignalBody,
  parseSlackMessage,
} from './slackRelay.js';

const GRADE_RANK: Record<string, number> = {
  ALL: 0,
  C: 1,
  'C+': 2,
  B: 3,
  'B+': 4,
  A: 5,
  'A+': 6,
};

function normalizeSelectValue(raw: unknown, fallback: string): string {
  if (Array.isArray(raw)) return String(raw[0] ?? fallback).trim() || fallback;
  if (typeof raw === 'string' && raw.trim()) return raw.trim();
  return fallback;
}

export function gradeMeetsMin(grade: string, minGrade: string): boolean {
  const floor = GRADE_RANK[minGrade.toUpperCase()] ?? 0;
  if (floor === 0) return true;
  return (GRADE_RANK[grade] ?? 0) >= floor;
}

export async function getSlackRelay(): Promise<SlackRelayClient> {
  const token = (await settings.get<string>('slackBotToken')) || '';
  return new SlackRelayClient(token);
}

async function getSlackChannelMap(relay: SlackRelayClient): Promise<Record<string, string>> {
  const cached = await redis.get('ch:slackChannels');
  if (cached) {
    try {
      const parsed = JSON.parse(cached) as { ts: number; map: Record<string, string> };
      if (parsed.map && Date.now() - parsed.ts < 30 * 60 * 1000 && Object.keys(parsed.map).length > 0) {
        return parsed.map;
      }
    } catch {
      // refresh below
    }
  }
  const map = await relay.discoverChannels();
  if (Object.keys(map).length > 0) {
    await redis.set('ch:slackChannels', JSON.stringify({ ts: Date.now(), map }));
  }
  return map;
}

export function slackSignalToDto(s: ParsedSlackSignal): SignalDto {
  const risk = Math.abs(s.entry - s.sl);
  const r1 = risk > 0 ? Math.abs(s.tp1 - s.entry) / risk : 1.2;
  const r2 = risk > 0 ? Math.abs(s.tp2 - s.entry) / risk : 2.2;
  const r3 = risk > 0 ? Math.abs(s.tp3 - s.entry) / risk : 3.5;
  const ch = getChannelDef(s.channelName);
  const isV3 = /^v3\b/i.test(s.card.tf) || /Strategy v3/i.test(s.body);
  const is5m = /^5m$/i.test(s.card.tf);
  const isV2 = !isV3 && !is5m && (/^v2\b/i.test(s.card.tf) || /Strategy v2|15m/i.test(s.body));
  const is30m = isV3 && /30m/i.test(s.card.tf);
  const barSeconds = isV3 ? (is30m ? 1800 : 3600) : isV2 ? 900 : 300;
  const timeframe = isV3 ? (is30m ? '30m' : '1h') : isV2 ? '15m' : '5m';
  const strategyMode = isV3 ? 'v3_regime' : isV2 ? 'v2_structure' : 'v1_squeeze';
  return {
    id: s.signalId,
    published_epoch: s.publishedEpoch,
    symbol: s.symbol,
    name: s.name,
    class_key: s.channelName,
    class_label: ch.label,
    emoji: ch.emoji,
    channel_name: s.channelName,
    decimals: s.decimals,
    pip_value: s.pipValue,
    pip_unit: s.pipUnit,
    is_forex: s.channelName.startsWith('forex'),
    direction: s.direction,
    score: s.score,
    grade: s.grade,
    triggers: s.card.trigger
      ? [s.card.trigger]
      : [isV3 ? `Strategy v3 (${timeframe} Regime)` : isV2 ? 'Strategy v2 (15m Structure)' : 'Strategy v1 (5m Squeeze)'],
    candle_epoch: s.publishedEpoch - barSeconds,
    close_epoch: s.publishedEpoch,
    bar_seconds: barSeconds,
    timeframe,
    strategy_mode: strategyMode,
    edge_status: /Proven Edge/i.test(s.body) ? 'validated' : /Negative/i.test(s.body) ? 'negative' : 'unvalidated',
    net_of_costs: true,
    valid_until: s.publishedEpoch + 1800,
    session: 'Live Deriv',
    plan: {
      entry: s.entry,
      entry_limit: s.entry,
      sl: s.sl,
      tp1: s.tp1,
      tp2: s.tp2,
      tp3: s.tp3,
      risk,
      atr: risk / 1.4,
      tp_r: [Number(r1.toFixed(2)), Number(r2.toFixed(2)), Number(r3.toFixed(2))],
    },
    adaptation: {
      code: s.regimeCode || 'FULL_TREND',
      title: s.regimeCode || 'MTF Structure',
      badge: s.regimeCode === 'FULL_TREND' ? '🔥 FULL MTF TREND' : '🌊 HTF STRUCTURE',
      sl_mult: 1.0,
      tp_mult: 1.15,
      size_mult: s.sizeMult || 1.0,
      partial_tp1: 0.4,
      early_be_r: null,
      trail_atr_mult: 1.5,
      horizon_bars: 96,
      summary: s.title,
    },
    views: [],
    checks: [],
    reasons: [],
    warnings: [],
    metrics: {
      val: null,
      prev_val: null,
      squeeze_state: 'OFF',
      squeeze_bars: 0,
      compression_pct: null,
      atr_pct: null,
      adx: null,
      rsi: null,
      ema_stack: 'Live MTF',
      resistance: [],
      support: [],
      range_24h_pos: null,
    },
    edge_dir: {
      n: 0,
      tp1_rate: 0,
      win_rate: 0,
      avg_r: 0,
      shrunk_avg_r: 0,
      days: 0,
      consistent: false,
      validated: /Proven Edge/i.test(s.body),
    },
    explain: {
      title: s.title,
      summary: s.body.slice(0, 1200),
      mtf_explain: `Channel: ${ch.flairText}`,
      edge_explain: `Grade ${s.grade} · Score ${Math.round(s.score)}/100 · Risk Scale ×${(s.sizeMult || 1).toFixed(2)}`,
      checklist: `Entry ${s.entry} | SL ${s.sl} | TP1 ${s.tp1} | TP2 ${s.tp2} | TP3 ${s.tp3}`,
      warnings_text: '',
    },
  };
}

export async function ensureSubredditChannels(subredditName: string): Promise<{
  createdFlairs: number;
  totalFlairs: number;
  widgetCreated: boolean;
  directoryUrl?: string;
}> {
  const existing = await reddit.getPostFlairTemplates(subredditName);
  const byText = new Map<string, string>();
  for (const tpl of existing) {
    byText.set(tpl.text.trim().toLowerCase(), tpl.id);
  }

  let createdFlairs = 0;
  const flairMap: Record<string, string> = {};
  const orderedIds: string[] = [];

  for (const ch of MARKET_CHANNELS) {
    const key = ch.flairText.trim().toLowerCase();
    let flairId = byText.get(key);
    if (!flairId) {
      try {
        const created = await reddit.createPostFlairTemplate({
          subredditName,
          text: ch.flairText,
          backgroundColor: ch.bgColor,
          textColor: ch.textColor,
          allowableContent: 'all',
          allowUserEdits: false,
          modOnly: false,
        });
        flairId = created.id;
        createdFlairs += 1;
      } catch (err) {
        console.error(`Failed creating flair ${ch.flairText}:`, err);
      }
    }
    if (flairId) {
      flairMap[ch.id] = flairId;
      orderedIds.push(flairId);
    }
  }

  await redis.set('ch:flairs', JSON.stringify(flairMap));

  let widgetCreated = false;
  if (orderedIds.length > 0) {
    try {
      const widgets = await reddit.getWidgets(subredditName);
      const hasChannelWidget = widgets.some(
        (w) => w.name.toLowerCase().includes('market channel') || w.name.toLowerCase().includes('signal channel')
      );
      if (!hasChannelWidget) {
        await reddit.addWidget({
          type: 'post-flair',
          subreddit: subredditName,
          shortName: 'Market Channels',
          display: 'list',
          order: orderedIds,
          styles: {
            backgroundColor: '#121A2B',
            headerColor: '#2563EB',
          },
        });
        widgetCreated = true;
      }
    } catch (err) {
      console.error('Could not create Market Channels sidebar widget:', err);
    }
  }

  let directoryUrl: string | undefined;
  const existingDirId = await redis.get('ch:directoryPostId');
  if (!existingDirId) {
    try {
      const post = await reddit.submitPost({
        subredditName,
        title: `📡 r/${subredditName} Market Channels & Bot Commands Directory`,
        text: formatChannelDirectoryBody(subredditName),
        flairId: flairMap.trading,
        flairText: getChannelDef('trading').flairText,
      });
      await redis.set('ch:directoryPostId', post.id);
      directoryUrl = post.url;
    } catch (err) {
      console.error('Could not create Channel Directory post:', err);
    }
  }

  return { createdFlairs, totalFlairs: orderedIds.length, widgetCreated, directoryUrl };
}

async function safelyDeletePost(postId: string): Promise<boolean> {
  if (!postId || !postId.startsWith('t3_')) return false;
  try {
    const post = await reddit.getPostById(postId as T3);
    await post.unhighlight().catch(() => undefined);
    await post.unsticky().catch(() => undefined);
    await post.remove(false).catch(() => undefined);
    await post.delete().catch(() => undefined);
    return true;
  } catch (err) {
    console.warn(`Could not delete post ${postId}:`, err);
    return false;
  }
}

async function ensureFlairApplied(
  subredditName: string,
  postId: string,
  channelName: string,
  flairMap: Record<string, string>
): Promise<void> {
  const ch = getChannelDef(channelName);
  const flairTemplateId = flairMap[channelName] ?? flairMap.trading;
  if (!flairTemplateId) return;
  try {
    await reddit.setPostFlair({
      subredditName,
      postId: postId as T3,
      flairTemplateId,
      text: ch.flairText,
    });
  } catch (err) {
    console.warn(`Could not set flair ${ch.flairText} on ${postId}:`, err);
  }
}

interface ActiveTrackedPost {
  channelId: string;
  channelName: string;
  parentTs: string;
  postId: string;
  symbol: string;
  direction: string;
  title: string;
  createdEpoch: number;
}

interface PostFollower {
  username: string;
  commentId: string;
  userId?: string;
}

const ACTIVE_POST_MAX_AGE_SEC = 72 * 3600;

async function loadActiveTrackedPosts(): Promise<ActiveTrackedPost[]> {
  const raw = await redis.get('ch:activePosts');
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const cutoff = Math.floor(Date.now() / 1000) - ACTIVE_POST_MAX_AGE_SEC;
    return (parsed as ActiveTrackedPost[]).filter(
      (p) => p && p.channelId && p.parentTs && p.postId && (p.createdEpoch || 0) >= cutoff
    );
  } catch {
    return [];
  }
}

async function trackActivePost(entry: ActiveTrackedPost): Promise<void> {
  const existing = await loadActiveTrackedPosts();
  const filtered = existing.filter(
    (p) => !(p.channelId === entry.channelId && p.parentTs === entry.parentTs)
  );
  filtered.push(entry);
  await redis.set('ch:activePosts', JSON.stringify(filtered.slice(-50)));
}

async function untrackActivePost(channelId: string, parentTs: string): Promise<void> {
  const existing = await loadActiveTrackedPosts();
  const next = existing.filter((p) => !(p.channelId === channelId && p.parentTs === parentTs));
  if (next.length !== existing.length) {
    await redis.set('ch:activePosts', JSON.stringify(next));
  }
}

async function getPostFollowers(postId: string): Promise<PostFollower[]> {
  const raw = await redis.get(`ch:followers:${postId}`);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? (arr as PostFollower[]) : [];
  } catch {
    return [];
  }
}

async function addPostFollower(postId: string, follower: PostFollower): Promise<PostFollower[]> {
  const current = await getPostFollowers(postId);
  const lower = follower.username.toLowerCase();
  const next = current.filter((f) => f.username.toLowerCase() !== lower);
  next.push(follower);
  await redis.set(`ch:followers:${postId}`, JSON.stringify(next.slice(-50)));
  return next;
}

async function removePostFollower(postId: string, username: string): Promise<PostFollower[]> {
  const current = await getPostFollowers(postId);
  const lower = username.toLowerCase();
  const next = current.filter((f) => f.username.toLowerCase() !== lower);
  await redis.set(`ch:followers:${postId}`, JSON.stringify(next));
  return next;
}

async function getGlobalAlertUsers(): Promise<string[]> {
  const raw = await redis.get('ch:globalAlertUsers');
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? arr.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

async function setGlobalAlertUser(username: string, enabled: boolean): Promise<string[]> {
  const current = await getGlobalAlertUsers();
  const lower = username.toLowerCase();
  const next = current.filter((u) => u.toLowerCase() !== lower);
  if (enabled && lower !== 'cerynaean-hind') {
    next.push(username);
  }
  await redis.set('ch:globalAlertUsers', JSON.stringify(next.slice(-100)));
  return next;
}

async function collectAlertTargets(postId: string): Promise<{
  targetUsernames: string[];
  dmEnabled: boolean;
  mentionEnabled: boolean;
  postFollowers: PostFollower[];
}> {
  const [rawAlertUsers, dmSetting, mentionSetting, globalUsers, postFollowers] = await Promise.all([
    settings.get<string>('alertUsernames'),
    settings.get<boolean>('dmPositionUpdates'),
    settings.get<boolean>('mentionOnUpdates'),
    getGlobalAlertUsers(),
    getPostFollowers(postId),
  ]);
  const configured = parseAlertUsernames(rawAlertUsers);
  const seen = new Set<string>();
  const targetUsernames: string[] = [];
  for (const u of [...configured, ...globalUsers, ...postFollowers.map((f) => f.username)]) {
    const clean = u.trim().replace(/^\/?u\//i, '');
    if (!clean) continue;
    const lower = clean.toLowerCase();
    if (lower === 'cerynaean-hind' || seen.has(lower)) continue;
    seen.add(lower);
    targetUsernames.push(clean);
  }
  return {
    targetUsernames,
    dmEnabled: dmSetting ?? true,
    mentionEnabled: mentionSetting ?? true,
    postFollowers,
  };
}

async function sendPushAlertToOptedInUsers(
  link: T1 | T3,
  alert: { title: string; body: string }
): Promise<void> {
  try {
    const userIds: T2[] = [];
    for await (const uid of notifications.listOptedInUsersIterator()) {
      userIds.push(uid as T2);
      if (userIds.length >= 500) break;
    }
    if (!userIds.length) return;
    await notifications.enqueue({
      title: alert.title,
      body: alert.body,
      recipients: userIds.map((userId) => ({
        userId,
        link,
        data: {},
      })),
    });
  } catch (err) {
    console.warn(`Push notification enqueue skipped for ${link}:`, err);
  }
}

export async function enableLiveTradeAlertsForCurrentUser(): Promise<{
  username: string | null;
  pushOk: boolean;
  message: string;
}> {
  const username = (await reddit.getCurrentUsername()) ?? null;
  let pushOk = false;
  try {
    const res = await notifications.optInCurrentUser();
    pushOk = Boolean(res?.success);
  } catch (err) {
    console.warn('optInCurrentUser failed:', err);
  }
  if (username) {
    await setGlobalAlertUser(username, true);
  }
  if (!username && !pushOk) {
    return { username: null, pushOk: false, message: 'Sign in to Reddit to enable live trade alerts.' };
  }
  return {
    username,
    pushOk,
    message: username
      ? `Live alerts enabled for u/${username}${pushOk ? ' (Push + DM + Mention)' : ' (DM + Mention)'}.`
      : 'Push alerts enabled for your Reddit account.',
  };
}

export async function disableLiveTradeAlertsForCurrentUser(): Promise<{
  username: string | null;
  pushOk: boolean;
  message: string;
}> {
  const username = (await reddit.getCurrentUsername()) ?? null;
  let pushOk = false;
  try {
    const res = await notifications.optOutCurrentUser();
    pushOk = Boolean(res?.success);
  } catch (err) {
    console.warn('optOutCurrentUser failed:', err);
  }
  if (username) {
    await setGlobalAlertUser(username, false);
  }
  return {
    username,
    pushOk,
    message: username
      ? `Live trade push & DM alerts disabled for u/${username}.`
      : 'Live trade push alerts disabled.',
  };
}

async function submitSignalPost(
  subredditName: string,
  sig: ParsedSlackSignal,
  flairMap: Record<string, string>
): Promise<string> {
  const ch = getChannelDef(sig.channelName);
  const flairId = flairMap[sig.channelName] ?? flairMap.trading;
  const post = await reddit.submitPost({
    subredditName,
    title: sig.title,
    text: sig.body,
    flairId,
    flairText: ch.flairText,
  });
  await ensureFlairApplied(subredditName, post.id, sig.channelName, flairMap);
  await post.approve().catch(() => undefined);
  await redis.set(`ch:seen:slack:${sig.channelId}:${sig.ts}`, post.id);
  await redis.set(`ch:slackPost:${sig.channelId}:${sig.ts}`, post.id);
  await redis.set(`ch:optB:${post.id}`, '1');
  await redis.set(`ch:card:${post.id}`, JSON.stringify(sig.detail));
  await trackActivePost({
    channelId: sig.channelId,
    channelName: sig.channelName,
    parentTs: sig.ts,
    postId: post.id,
    symbol: sig.symbol,
    direction: sig.direction,
    title: sig.title,
    createdEpoch: sig.publishedEpoch,
  });
  await sendPushAlertToOptedInUsers(
    post.id as T3,
    formatPushAlert({
      title: sig.title,
      kindTag: `${sig.direction} ENTRY`,
      rLabel: `${sig.grade} ${sig.score}/100`,
      body: sig.body,
    })
  );
  return post.id;
}

async function applyLifecycleToSignalPost(
  subredditName: string,
  postId: string,
  ev: ParsedSlackLifecycle,
  flairMap: Record<string, string>
): Promise<void> {
  const cardDetail = await getSignalCardDetail(postId);
  const postTitle = cardDetail?.card
    ? `${cardDetail.card.dir} ${cardDetail.card.name} (${cardDetail.card.symbol})`
    : ev.title;

  const important = isImportantLifecycleEvent(ev.kindTag, ev.isFinal);
  const { targetUsernames, dmEnabled, mentionEnabled, postFollowers } = important
    ? await collectAlertTargets(postId)
    : { targetUsernames: [], dmEnabled: false, mentionEnabled: false, postFollowers: [] };

  const followerNames = new Set(postFollowers.map((f) => f.username.toLowerCase()));
  const nonFollowerTargets = targetUsernames.filter((u) => !followerNames.has(u.toLowerCase()));
  const shouldInlineMention = important && mentionEnabled && !dmEnabled && nonFollowerTargets.length > 0;
  const commentBody = shouldInlineMention ? appendRedditMentions(ev.body, nonFollowerTargets) : ev.body;

  const comment = await reddit.submitComment({
    id: postId as T3,
    text: commentBody,
  });

  const ch = getChannelDef(ev.channelName);
  await reddit
    .setPostFlair({
      subredditName,
      postId: postId as T3,
      flairTemplateId: flairMap[ev.channelName] ?? flairMap.trading,
      text: buildLiveFlairText(ch.flairText, ev.kindTag, ev.rLabel),
    })
    .catch((err) => console.warn(`Could not update flair on ${postId}:`, err));

  if (important) {
    for (const follower of postFollowers) {
      if (!follower.commentId || !follower.commentId.startsWith('t1_')) continue;
      await reddit
        .submitComment({
          id: follower.commentId as T1,
          text: `u/${follower.username} — **${ev.kindTag}${ev.rLabel ? ` (${ev.rLabel})` : ''}** update on **${postTitle}**:\n\n${ev.body}`,
        })
        .catch((err) => console.warn(`Could not reply to follower ${follower.username} (${follower.commentId}):`, err));
    }

    if (dmEnabled && nonFollowerTargets.length > 0) {
      const dm = formatLifecycleDm({
        subredditName,
        postId,
        commentId: comment?.id,
        title: postTitle,
        kindTag: ev.kindTag,
        rLabel: ev.rLabel,
        body: ev.body,
      });
      for (const username of nonFollowerTargets) {
        await reddit
          .sendPrivateMessage({
            to: username,
            subject: dm.subject,
            text: dm.text,
          })
          .catch((err) => console.warn(`Could not send DM to u/${username} for ${postId}:`, err));
      }
    }

    await sendPushAlertToOptedInUsers(
      (comment?.id || postId) as T1 | T3,
      formatPushAlert({
        title: postTitle,
        kindTag: ev.kindTag,
        rLabel: ev.rLabel,
        body: ev.body,
      })
    );
  }

  if (ev.isFinal) {
    await untrackActivePost(ev.channelId, ev.parentTs);
  }
}

export async function removeCommandCenterAndUpgradeSignalCards(subredditName: string): Promise<{
  removedCommandCenters: string[];
  upgradedSignalCards: string[];
}> {
  const removedCommandCenters: string[] = [];
  const upgradedSignalCards: string[] = [];

  // 1. Ensure pinned Command Center post stays removed
  const ccDone = await redis.get('ch:cleanup:v005:removedCommandCenter');
  if (!ccDone) {
    const ids = new Set<string>(['t3_1y0naq1']);
    const storedId = await redis.get('ch:commandCenterPostId');
    if (storedId) ids.add(storedId);
    for (const id of ids) {
      if (await safelyDeletePost(id)) {
        removedCommandCenters.push(id);
      }
    }
    await redis.del('ch:commandCenterPostId');
    await redis.set('ch:cleanup:v005:removedCommandCenter', 'done');
  }

  // 2. Replace Option A custom posts & legacy messy text posts with Option B Clean Native Text Posts
  const optBDone = await redis.get('ch:cleanup:v008:switchedToOptionB');
  if (!optBDone) {
    try {
      const minGrade = normalizeSelectValue(await settings.get('redditMinGrade'), 'B+');
      const rawEnabled = await settings.get<string[]>('enabledChannels');
      const enabledChannels =
        Array.isArray(rawEnabled) && rawEnabled.length > 0
          ? rawEnabled
          : MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id);

      const relay = await getSlackRelay();
      const flairMap = await getFlairMap(subredditName);
      const { signals, lifecycles } = await collectRecentSlackItems(relay, enabledChannels, 8);
      const cutoffEpoch = Math.floor(Date.now() / 1000) - 18 * 3600;
      const latestQualifying = [...signals]
        .reverse()
        .filter((s) => s.publishedEpoch >= cutoffEpoch && gradeMeetsMin(s.grade, minGrade))
        .slice(0, 6)
        .reverse();

      // Explicitly remove the 5 Option A custom card posts from v0.0.6/v0.0.7
      for (const oldCustomId of ['t3_1y0q8fg', 't3_1y0q8gd', 't3_1y0q8h6', 't3_1y0q8hv', 't3_1y0q8it']) {
        await safelyDeletePost(oldCustomId);
      }

      for (const sig of latestQualifying) {
        const seenKey = `ch:seen:slack:${sig.channelId}:${sig.ts}`;
        const oldPostId = await redis.get(seenKey);
        if (oldPostId && oldPostId.startsWith('t3_')) {
          if (await redis.get(`ch:optB:${oldPostId}`)) continue;
          await safelyDeletePost(oldPostId);
        }
        try {
          const newPostId = await submitSignalPost(subredditName, sig, flairMap);
          upgradedSignalCards.push(newPostId);

          const relatedEvs = lifecycles.filter(
            (ev) => ev.channelId === sig.channelId && ev.parentTs === sig.ts
          );
          for (const ev of relatedEvs) {
            try {
              await applyLifecycleToSignalPost(subredditName, newPostId, ev, flairMap);
              await redis.set(`ch:seen:ev:${ev.channelId}:${ev.ts}`, 'commented');
            } catch (evErr) {
              console.warn(`Could not re-attach event ${ev.ts} to ${newPostId}:`, evErr);
            }
          }
        } catch (err) {
          console.error(`Failed posting Option B signal ${sig.symbol}:`, err);
        }
      }
      await redis.set('ch:cleanup:v008:switchedToOptionB', 'done');
    } catch (err) {
      console.error('Option B migration failed:', err);
    }
  }

  // 3. v0.0.9 in-place polish: update Option B top summary to bullet-first (no smashed feed table) + apply channel flairs
  const optBPolish = await redis.get('ch:cleanup:v009:bulletSummaryAndFlairs');
  if (!optBPolish) {
    try {
      const relay = await getSlackRelay();
      await ensureSubredditChannels(subredditName);
      const flairMap = await getFlairMap(subredditName);
      const allMarketChs = MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id);
      const { signals } = await collectRecentSlackItems(relay, allMarketChs, 8);
      for (const sig of signals) {
        const postId = await redis.get(`ch:slackPost:${sig.channelId}:${sig.ts}`);
        if (!postId || !postId.startsWith('t3_')) continue;
        if (!(await redis.get(`ch:optB:${postId}`))) continue;
        try {
          const post = await reddit.getPostById(postId as T3);
          await post.edit({ text: sig.body });
          await ensureFlairApplied(subredditName, postId, sig.channelName, flairMap);
          await post.approve().catch(() => undefined);
          upgradedSignalCards.push(`polished:${postId}`);
        } catch (err) {
          console.warn(`Could not polish Option B post ${postId}:`, err);
        }
      }
      await redis.set('ch:cleanup:v009:bulletSummaryAndFlairs', 'done');
    } catch (err) {
      console.error('v0.0.9 Option B polish failed:', err);
    }
  }

  // 4. v0.0.10 upgrade: convert all live signal posts on r/GlvnsOrg to 15m Scalping + Plain-Text Profit & Protection Ladder
  const scalpPolish = await redis.get('ch:cleanup:v010:scalp15mPlainLadder');
  if (!scalpPolish) {
    try {
      const relay = await getSlackRelay();
      await ensureSubredditChannels(subredditName);
      const flairMap = await getFlairMap(subredditName);
      const allMarketChs = MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id);
      const { signals, lifecycles } = await collectRecentSlackItems(relay, allMarketChs, 10);
      const editedIds = new Set<string>();

      for (const sig of signals) {
        const postId = await redis.get(`ch:slackPost:${sig.channelId}:${sig.ts}`);
        if (!postId || !postId.startsWith('t3_')) continue;
        try {
          const post = await reddit.getPostById(postId as T3);
          await post.edit({ text: sig.body });
          await ensureFlairApplied(subredditName, postId, sig.channelName, flairMap);
          await redis.set(`ch:card:${postId}`, JSON.stringify(sig.detail));
          await post.approve().catch(() => undefined);
          editedIds.add(postId);
          upgradedSignalCards.push(`scalp15m:${postId}`);

          for (const ev of lifecycles.filter((l) => l.channelId === sig.channelId && l.parentTs === sig.ts)) {
            const seenKey = `ch:seen:ev:${ev.channelId}:${ev.ts}`;
            if (await redis.get(seenKey)) continue;
            await applyLifecycleToSignalPost(subredditName, postId, ev, flairMap);
            await redis.set(seenKey, 'commented');
          }
        } catch (err) {
          console.warn(`Could not upgrade post ${postId} to 15m scalp format:`, err);
        }
      }

      const recentPosts = await reddit.getNewPosts({ subredditName, limit: 25 }).all();
      for (const p of recentPosts) {
        if (editedIds.has(p.id)) continue;
        if (p.authorName && p.authorName.toLowerCase() !== 'cerynaean-hind') continue;
        const body = p.body ?? '';
        if (
          !/### 📋 Execution Ladder|### 🧭 Multi-Timeframe Alignment|### 🪜 Profit Ladder|### 🧭 Timeframes/i.test(body)
        ) {
          continue;
        }
        try {
          const detail = await getSignalCardDetail(p.id);
          if (detail) {
            await p.edit({ text: formatOptionBSignalBody(detail.card, detail) });
            upgradedSignalCards.push(`card15m:${p.id}`);
          }
        } catch (err) {
          console.warn(`Could not rebuild cached post ${p.id}:`, err);
        }
      }

      await redis.set('ch:cleanup:v010:scalp15mPlainLadder', 'done');
    } catch (err) {
      console.error('v0.0.10 15m scalp upgrade failed:', err);
    }
  }

  return { removedCommandCenters, upgradedSignalCards };
}

export async function getSignalCardDetail(
  postId: string | undefined,
  fallbackPostData?: Record<string, unknown>
): Promise<SignalCardDetail | null> {
  if (postId) {
    const raw = await redis.get(`ch:card:${postId}`);
    if (raw) {
      try {
        return JSON.parse(raw) as SignalCardDetail;
      } catch {
        // fallback below
      }
    }
  }
  if (fallbackPostData && fallbackPostData.kind === 'signal') {
    const card = fallbackPostData as unknown as SignalCardPostData;
    return {
      card,
      regimeSummary: `${card.regime} · ${card.trigger}`,
      validUntil: '',
      riskFields: [
        { label: 'Equity at risk', value: `≤ ${card.sizeMult.toFixed(2)}%` },
        { label: 'Reward:Risk', value: `${card.r1.toFixed(2)} / ${card.r2.toFixed(2)} / ${card.r3.toFixed(2)}` },
        { label: 'Horizon', value: card.horizon || '480m' },
      ],
      mtfRows: [],
      confluenceSummary: card.confluence || '',
      confluencePassed: [],
      confluenceMissed: [],
      marketData: [],
      whySetup: [],
      warnings: [],
      trackRecordTitle: 'Walk-Forward Track Record',
      trackRecordSummary: card.edge,
      events: [],
    };
  }
  return null;
}

async function getFlairMap(subredditName: string): Promise<Record<string, string>> {
  const raw = await redis.get('ch:flairs');
  if (raw) {
    try {
      return JSON.parse(raw) as Record<string, string>;
    } catch {
      // refresh
    }
  }
  await ensureSubredditChannels(subredditName);
  const refreshed = await redis.get('ch:flairs');
  return refreshed ? (JSON.parse(refreshed) as Record<string, string>) : {};
}

async function collectRecentSlackItems(
  relay: SlackRelayClient,
  channelNames: string[],
  limitPerChannel = 15,
  extraTrackedThreads: { channelId: string; channelName: string; parentTs: string }[] = []
): Promise<{
  signals: ParsedSlackSignal[];
  lifecycles: ParsedSlackLifecycle[];
  pulses: ParsedSlackPulse[];
  channelMap: Record<string, string>;
}> {
  const channelMap = await getSlackChannelMap(relay);
  const signals: ParsedSlackSignal[] = [];
  const lifecycles: ParsedSlackLifecycle[] = [];
  const pulses: ParsedSlackPulse[] = [];

  const targets = channelNames.filter((name) => Boolean(channelMap[name]));
  const seenLifecycleTs = new Set<string>();
  const fetchedThreads = new Set<string>();
  await Promise.all(
    targets.map(async (chName) => {
      const chId = channelMap[chName];
      try {
        const msgs = await relay.fetchChannelHistory(chId, limitPerChannel);
        const threadParents: string[] = [];
        for (const msg of msgs) {
          const parsed = parseSlackMessage(chName, chId, msg);
          if (!parsed) continue;
          if (parsed.kind === 'signal') {
            signals.push(parsed);
            if ((msg.reply_count ?? 0) > 0) threadParents.push(msg.ts);
          } else if (parsed.kind === 'lifecycle') {
            const k = `${chId}:${parsed.ts}`;
            if (!seenLifecycleTs.has(k)) {
              seenLifecycleTs.add(k);
              lifecycles.push(parsed);
            }
          } else if (parsed.kind === 'pulse') {
            pulses.push(parsed);
          }
        }
        for (const parentTs of threadParents.slice(0, 12)) {
          fetchedThreads.add(`${chId}:${parentTs}`);
          try {
            const replies = await relay.fetchThreadReplies(chId, parentTs, 25);
            for (const rep of replies) {
              const parsedRep = parseSlackMessage(chName, chId, { ...rep, thread_ts: parentTs });
              if (parsedRep?.kind === 'lifecycle') {
                const k = `${chId}:${parsedRep.ts}`;
                if (!seenLifecycleTs.has(k)) {
                  seenLifecycleTs.add(k);
                  lifecycles.push(parsedRep);
                }
              }
            }
          } catch (repErr) {
            console.warn(`Failed reading Slack thread ${parentTs} in #${chName}:`, repErr);
          }
        }
      } catch (err) {
        console.error(`Failed reading Slack #${chName}:`, err);
      }
    })
  );

  const extraPending = extraTrackedThreads.filter((t) => !fetchedThreads.has(`${t.channelId}:${t.parentTs}`));
  await Promise.all(
    extraPending.slice(0, 30).map(async (t) => {
      fetchedThreads.add(`${t.channelId}:${t.parentTs}`);
      try {
        const replies = await relay.fetchThreadReplies(t.channelId, t.parentTs, 25);
        for (const rep of replies) {
          const parsedRep = parseSlackMessage(t.channelName, t.channelId, { ...rep, thread_ts: t.parentTs });
          if (parsedRep?.kind === 'lifecycle') {
            const k = `${t.channelId}:${parsedRep.ts}`;
            if (!seenLifecycleTs.has(k)) {
              seenLifecycleTs.add(k);
              lifecycles.push(parsedRep);
            }
          }
        }
      } catch (repErr) {
        console.warn(`Failed reading tracked Slack thread ${t.parentTs} in #${t.channelName}:`, repErr);
      }
    })
  );

  signals.sort((a, b) => a.publishedEpoch - b.publishedEpoch);
  lifecycles.sort((a, b) => a.publishedEpoch - b.publishedEpoch);
  pulses.sort((a, b) => a.publishedEpoch - b.publishedEpoch);
  return { signals, lifecycles, pulses, channelMap };
}

export async function syncSignalsAndLifecycle(subredditName: string): Promise<{
  postedSignals: number;
  postedEvents: number;
  skipped: number;
  totalFound: number;
  alreadySeen: number;
  postIds: string[];
}> {
  const autoSignals = (await settings.get<boolean>('autoPostSignals')) ?? true;
  const autoLifecycle = (await settings.get<boolean>('autoCommentLifecycle')) ?? true;
  if (!autoSignals && !autoLifecycle) {
    return { postedSignals: 0, postedEvents: 0, skipped: 0, totalFound: 0, alreadySeen: 0, postIds: [] };
  }

  const minGrade = normalizeSelectValue(await settings.get('redditMinGrade'), 'B+');
  const rawEnabled = await settings.get<string[]>('enabledChannels');
  const enabledChannels =
    Array.isArray(rawEnabled) && rawEnabled.length > 0
      ? rawEnabled
      : MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id);

  const relay = await getSlackRelay();
  const flairMap = await getFlairMap(subredditName);
  const activeTracked = await loadActiveTrackedPosts();
  const { signals, lifecycles } = await collectRecentSlackItems(relay, enabledChannels, 15, activeTracked);

  // Cache latest parsed signals for fast Command Center & !calc / !scan lookups
  if (signals.length > 0) {
    await redis.set('ch:recentSignals', JSON.stringify(signals.slice(-40)));
  }

  let postedSignals = 0;
  let postedEvents = 0;
  let skipped = 0;
  let alreadySeen = 0;
  const postIds: string[] = [];

  // Only auto-post signals from the last 18 hours so old history doesn't flood on first boot
  const cutoffEpoch = Math.floor(Date.now() / 1000) - 18 * 3600;

  if (autoSignals) {
    // Take newest qualifying signals first if many exist, up to 5 posts per 2-minute tick
    const candidates = [...signals].reverse();
    const toPost: ParsedSlackSignal[] = [];
    for (const sig of candidates) {
      if (sig.publishedEpoch < cutoffEpoch) continue;
      const seenKey = `ch:seen:slack:${sig.channelId}:${sig.ts}`;
      if (await redis.get(seenKey)) {
        alreadySeen += 1;
        continue;
      }
      if (!gradeMeetsMin(sig.grade, minGrade)) {
        await redis.set(seenKey, 'filtered');
        skipped += 1;
        continue;
      }
      toPost.push(sig);
      if (toPost.length >= 5) break;
    }

    for (const sig of toPost.reverse()) {
      try {
        const postId = await submitSignalPost(subredditName, sig, flairMap);
        postedSignals += 1;
        postIds.push(postId);
      } catch (err) {
        console.error(`Failed posting signal ${sig.symbol} to r/${subredditName}:`, err);
      }
    }
  }

  if (autoLifecycle) {
    for (const ev of lifecycles) {
      const seenKey = `ch:seen:ev:${ev.channelId}:${ev.ts}`;
      if (await redis.get(seenKey)) continue;
      const postId = await redis.get(`ch:slackPost:${ev.channelId}:${ev.parentTs}`);
      if (!postId) {
        continue;
      }
      try {
        await applyLifecycleToSignalPost(subredditName, postId, ev, flairMap);
        await redis.set(seenKey, 'commented');
        postedEvents += 1;
      } catch (err) {
        console.error(`Failed commenting lifecycle ${ev.ts} on ${postId}:`, err);
      }
    }
  }

  await redis.set('ch:lastSyncEpoch', String(Math.floor(Date.now() / 1000)));
  return { postedSignals, postedEvents, skipped, totalFound: signals.length, alreadySeen, postIds };
}

export async function syncPulsesAndEod(subredditName: string): Promise<{ postedPulses: number; postIds: string[] }> {
  const autoPulses = (await settings.get<boolean>('autoPostPulses')) ?? true;
  if (!autoPulses) return { postedPulses: 0, postIds: [] };

  const relay = await getSlackRelay();
  const flairMap = await getFlairMap(subredditName);
  const { pulses } = await collectRecentSlackItems(relay, ['trading'], 6);
  const cutoffEpoch = Math.floor(Date.now() / 1000) - 24 * 3600;

  let postedPulses = 0;
  const postIds: string[] = [];
  // Post only the latest unseen pulse/EOD recap per tick so we never spam older pulses
  const latestCandidates = [...pulses].reverse().filter((p) => p.publishedEpoch >= cutoffEpoch);
  for (const pulse of latestCandidates.slice(0, 2).reverse()) {
    const key = `ch:seen:pulse:${pulse.channelId}:${pulse.ts}`;
    if (await redis.get(key)) continue;
    try {
      const post = await reddit.submitPost({
        subredditName,
        title: pulse.title,
        text: pulse.body,
        flairId: flairMap.trading,
        flairText: getChannelDef('trading').flairText,
      });
      await ensureFlairApplied(subredditName, post.id, 'trading', flairMap);
      await post.approve().catch(() => undefined);
      await redis.set(key, post.id);
      await redis.set('ch:latestPulse', JSON.stringify(pulse));
      postedPulses += 1;
      postIds.push(post.id);
    } catch (err) {
      console.error(`Failed posting pulse ${pulse.ts}:`, err);
    }
  }
  return { postedPulses, postIds };
}

export async function getLiveOverviewFromSlack(): Promise<BridgeOverviewDto> {
  const relay = await getSlackRelay();
  const allNames = MARKET_CHANNELS.map((c) => c.id);
  const { signals, lifecycles, pulses } = await collectRecentSlackItems(relay, allNames, 6);
  const signalDtos = [...signals].reverse().map(slackSignalToDto);
  const countByCh: Record<string, number> = {};
  for (const s of signalDtos) {
    countByCh[s.channel_name] = (countByCh[s.channel_name] ?? 0) + 1;
  }
  const now = Math.floor(Date.now() / 1000);
  const todayStr = new Date(now * 1000).toISOString().slice(0, 10);
  const slotNum = Math.max(1, Math.min(2, Math.floor((now % 86400) / 43200) || 1));

  const closedEvents = lifecycles.filter((l) => l.isFinal);
  const rVals = closedEvents.map((l) => parseFloat(l.rLabel) || 0);
  const wins = rVals.filter((r) => r > 0.01).length;
  const losses = rVals.filter((r) => r < -0.01).length;
  const breakevens = Math.max(0, closedEvents.length - wins - losses);
  const grossWin = rVals.filter((r) => r > 0).reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(rVals.filter((r) => r < 0).reduce((a, b) => a + b, 0));
  const totalR = rVals.reduce((a, b) => a + b, 0);
  const nClosed = closedEvents.length;
  const tp1Hits = lifecycles.filter((l) => l.kindTag === 'TP1' || l.kindTag === 'TP2' || l.kindTag === 'TP3').length;

  return {
    status: {
      connected: true,
      phase: 'live (Slack relay)',
      uptime_seconds: 0,
      symbols_total: 85,
      symbols_loaded: 85,
      validated_pairs: signalDtos.filter((s) => s.edge_status === 'validated').length,
      signals_sent: signalDtos.length,
      signals_filtered: 0,
      last_cycle_epoch: signalDtos[0]?.published_epoch ?? now,
      last_cycle_ms: 0,
      last_error: null,
    },
    settings: {
      strategy_mode: 'v3_regime',
      session_filter: true,
      edge_mode: 'advisory',
      min_grade: normalizeSelectValue(await settings.get('redditMinGrade'), 'B+'),
      default_equity: 1000,
      default_risk_pct: 1.0,
    },
    stats: {
      n: nClosed,
      wins,
      losses,
      breakevens,
      tp1_hits: tp1Hits,
      win_rate: nClosed ? wins / nClosed : 0,
      tp1_rate: nClosed ? Math.min(1, tp1Hits / nClosed) : 0,
      total_r: Number(totalR.toFixed(2)),
      avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0,
      profit_factor: grossLoss > 0 ? Number((grossWin / grossLoss).toFixed(2)) : null,
      profit_factor_inf: grossWin > 0 && grossLoss === 0,
      max_drawdown_r: Number(Math.abs(Math.min(0, ...rVals)).toFixed(2)),
      best_r: rVals.length ? Math.max(...rVals) : 0,
      worst_r: rVals.length ? Math.min(...rVals) : 0,
      gross_win_r: Number(grossWin.toFixed(2)),
      gross_loss_r: Number(grossLoss.toFixed(2)),
      current_streak: 0,
      avg_bars: 0,
      by_regime: [],
      by_grade: [],
      by_class: [],
      top_symbols: [],
    },
    perf: {
      '1h': { n: 0, wins: 0, tp1_hits: 0, win_rate: 0, tp1_rate: 0, total_r: 0, avg_r: 0 },
      '3h': { n: 0, wins: 0, tp1_hits: 0, win_rate: 0, tp1_rate: 0, total_r: 0, avg_r: 0 },
      '12h': {
        n: nClosed,
        wins,
        tp1_hits: tp1Hits,
        win_rate: nClosed ? wins / nClosed : 0,
        tp1_rate: nClosed ? Math.min(1, tp1Hits / nClosed) : 0,
        total_r: Number(totalR.toFixed(2)),
        avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0,
      },
      today: {
        n: nClosed,
        wins,
        tp1_hits: tp1Hits,
        win_rate: nClosed ? wins / nClosed : 0,
        tp1_rate: nClosed ? Math.min(1, tp1Hits / nClosed) : 0,
        total_r: Number(totalR.toFixed(2)),
        avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0,
      },
      '24h': { n: nClosed, wins, tp1_hits: tp1Hits, win_rate: nClosed ? wins / nClosed : 0, tp1_rate: 0, total_r: Number(totalR.toFixed(2)), avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0 },
      '7d': { n: nClosed, wins, tp1_hits: tp1Hits, win_rate: nClosed ? wins / nClosed : 0, tp1_rate: 0, total_r: Number(totalR.toFixed(2)), avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0 },
      all: { n: nClosed, wins, tp1_hits: tp1Hits, win_rate: nClosed ? wins / nClosed : 0, tp1_rate: 0, total_r: Number(totalR.toFixed(2)), avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0 },
    },
    daily: {
      trading_day: todayStr,
      pulse_slot: [slotNum, 2],
      signals_sent: signalDtos.length,
      signals_filtered: 0,
      pulses_sent: pulses.length,
    },
    open_trades: [],
    validated_edges: [],
    channels: MARKET_CHANNELS.map((c) => ({
      id: c.id,
      title: c.flairText,
      description: c.description,
      symbol_count: 0,
      open_count: c.id === 'trading' ? signalDtos.length : (countByCh[c.id] ?? 0),
      recent_signal_count: c.id === 'trading' ? signalDtos.length : (countByCh[c.id] ?? 0),
    })),
    recent_signals: signalDtos.slice(0, 30),
    recent_events: [],
    latest_pulse: pulses.length
      ? {
          id: pulses[pulses.length - 1].ts,
          kind: pulses[pulses.length - 1].isEod ? 'eod_recap' : 'intraday_pulse',
          timestamp: pulses[pulses.length - 1].publishedEpoch,
          trading_day: todayStr,
          data: { title: pulses[pulses.length - 1].title, body: pulses[pulses.length - 1].body },
        }
      : null,
    server_epoch: now,
  };
}

export async function getLiveChannelFromSlack(channelId: string): Promise<ChannelViewDto> {
  const relay = await getSlackRelay();
  const clean = channelId.trim().toLowerCase() || 'trading';
  const targets = clean === 'trading' ? MARKET_CHANNELS.map((c) => c.id) : [clean];
  const { signals, lifecycles } = await collectRecentSlackItems(relay, targets, clean === 'trading' ? 5 : 15);
  const dtos = [...signals].reverse().map(slackSignalToDto);
  const ch = getChannelDef(clean);
  const now = Math.floor(Date.now() / 1000);
  const todayStr = new Date(now * 1000).toISOString().slice(0, 10);
  const bull = dtos.filter((s) => s.direction === 'LONG').length;
  const bear = dtos.filter((s) => s.direction === 'SHORT').length;
  const closedEvents = lifecycles.filter((l) => l.isFinal);
  const rVals = closedEvents.map((l) => parseFloat(l.rLabel) || 0);
  const wins = rVals.filter((r) => r > 0.01).length;
  const totalR = rVals.reduce((a, b) => a + b, 0);
  const nClosed = closedEvents.length;
  return {
    channel: { id: ch.id, title: ch.flairText, description: ch.description },
    trading_day: todayStr,
    pulse_slot: [Math.max(1, Math.min(2, Math.floor((now % 86400) / 43200) || 1)), 2],
    breadth: { total: dtos.length, bull_1h: bull, bear_1h: bear, sqz_fired: 0, sqz_on: 0 },
    perf: {
      '1h': { n: 0, wins: 0, tp1_hits: 0, win_rate: 0, tp1_rate: 0, total_r: 0, avg_r: 0 },
      '3h': { n: 0, wins: 0, tp1_hits: 0, win_rate: 0, tp1_rate: 0, total_r: 0, avg_r: 0 },
      '12h': {
        n: nClosed,
        wins,
        tp1_hits: lifecycles.filter((l) => l.kindTag === 'TP1' || l.kindTag === 'TP2' || l.kindTag === 'TP3').length,
        win_rate: nClosed ? wins / nClosed : 0,
        tp1_rate: 0,
        total_r: Number(totalR.toFixed(2)),
        avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0,
      },
      today: {
        n: nClosed,
        wins,
        tp1_hits: lifecycles.filter((l) => l.kindTag === 'TP1' || l.kindTag === 'TP2' || l.kindTag === 'TP3').length,
        win_rate: nClosed ? wins / nClosed : 0,
        tp1_rate: 0,
        total_r: Number(totalR.toFixed(2)),
        avg_r: nClosed ? Number((totalR / nClosed).toFixed(2)) : 0,
      },
    },
    open_trades: [],
    recent_signals: dtos,
    recent_events: [],
    summaries: [],
    server_epoch: now,
  };
}

export async function scanSymbolFromSlack(token: string): Promise<ScanResultDto | null> {
  const relay = await getSlackRelay();
  const q = token.trim().toLowerCase();
  const { signals } = await collectRecentSlackItems(
    relay,
    MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id),
    8
  );
  const match = [...signals]
    .reverse()
    .find((s) => s.symbol.toLowerCase() === q || s.name.toLowerCase().includes(q) || s.symbol.toLowerCase().includes(q));
  if (!match) return null;
  const dto = slackSignalToDto(match);
  return {
    symbol: dto.symbol,
    name: dto.name,
    class_label: dto.class_label,
    channel_name: dto.channel_name,
    decimals: dto.decimals,
    close: dto.plan.entry,
    price_formatted: dto.plan.entry.toLocaleString('en-US', {
      minimumFractionDigits: dto.decimals,
      maximumFractionDigits: dto.decimals,
    }),
    epoch: dto.candle_epoch,
    bar_seconds: dto.bar_seconds,
    timeframe: dto.timeframe,
    strategy_mode: dto.strategy_mode,
    val: null,
    prev_val: null,
    squeeze: 'LIVE',
    direction_hint: dto.direction,
    atr: dto.plan.atr,
    adx: null,
    rsi: null,
    ema_stack: `Latest live signal (${dto.grade} · Score ${Math.round(dto.score)}/100)`,
    views: [],
    blockers: [],
    edge: dto.edge_dir,
    adaptation: {
      code: dto.adaptation.code,
      title: dto.adaptation.title,
      badge: dto.adaptation.badge,
      size_mult: dto.adaptation.size_mult,
      tp_r: dto.plan.tp_r,
      summary: match.body.slice(0, 500),
    },
  };
}

export async function calcPositionFromParams(params: Record<string, string>): Promise<CalcResultDto> {
  const equity = Number(params.equity ?? 1000);
  const riskPct = Number(params.risk_pct ?? 1.0);
  if (params.entry && params.sl && params.tp1 && params.tp2 && params.tp3) {
    const res = computeLocalPositionSize({
      entry: Number(params.entry),
      sl: Number(params.sl),
      tp1: Number(params.tp1),
      tp2: Number(params.tp2),
      tp3: Number(params.tp3),
      equity,
      riskPct,
      sizeMult: Number(params.size_mult ?? 1),
      pipValue: Number(params.pip_value ?? 0.01),
      pipUnit: params.pip_unit ?? 'pts',
    });
    return {
      symbol: params.symbol ?? 'CUSTOM',
      name: params.name ?? params.symbol ?? 'Custom',
      direction: params.direction ?? 'LONG',
      decimals: Number(params.decimals ?? 2),
      regime_label: params.regime_label ?? 'Live Signal Plan',
      result: res,
    };
  }
  const sym = (params.symbol ?? '').trim();
  const relay = await getSlackRelay();
  const { signals } = await collectRecentSlackItems(
    relay,
    MARKET_CHANNELS.filter((c) => c.id !== 'trading').map((c) => c.id),
    8
  );
  const q = sym.toLowerCase();
  const match = [...signals]
    .reverse()
    .find((s) => s.symbol.toLowerCase() === q || s.name.toLowerCase().includes(q) || s.symbol.toLowerCase().includes(q));
  if (!match) {
    throw new Error(`No recent live signal found for '${sym}'. Click Size Calc directly on any live signal card!`);
  }
  const res = computeLocalPositionSize({
    entry: match.entry,
    sl: match.sl,
    tp1: match.tp1,
    tp2: match.tp2,
    tp3: match.tp3,
    equity,
    riskPct,
    sizeMult: match.sizeMult,
    pipValue: match.pipValue,
    pipUnit: match.pipUnit,
  });
  return {
    symbol: match.symbol,
    name: match.name,
    direction: match.direction,
    decimals: match.decimals,
    regime_label: match.regimeCode || 'Strategy v3',
    result: res,
  };
}

export function parseCommentCommand(rawBody: string): { cmd: string; args: string[] } | null {
  const firstLine = rawBody.trim().split(/\r?\n/)[0]?.trim() ?? '';
  if (!firstLine.startsWith('!')) return null;
  const parts = firstLine.slice(1).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  const cmd = parts[0].toLowerCase();
  const supported = new Set([
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
  ]);
  if (!supported.has(cmd)) return null;
  return { cmd, args: parts.slice(1) };
}

export async function handleCommentCommand(
  commentId: string,
  body: string,
  authorName: string,
  postId = '',
  authorId = ''
): Promise<boolean> {
  const enabled = (await settings.get<boolean>('enableCommentCommands')) ?? true;
  if (!enabled) return false;
  if (!authorName || authorName.toLowerCase() === 'cerynaean-hind') return false;

  const parsed = parseCommentCommand(body);
  if (!parsed) return false;

  let reply = '';
  try {
    if (parsed.cmd === 'follow' || parsed.cmd === 'entered' || parsed.cmd === 'track' || parsed.cmd === 'notify') {
      if (!postId || !postId.startsWith('t3_')) {
        reply = 'Reply directly on a signal post with `!follow` to subscribe to every position update.';
      } else {
        await addPostFollower(postId, {
          username: authorName,
          commentId,
          userId: authorId || undefined,
        });
        reply =
          `🔔 **Subscribed u/${authorName}!** You will receive one clean alert on every important move (Breakeven, Profit Lock, Trailing Stop, Stop Tightened, Partial Taken, Risk/Danger/Reversal Warning, and Close) — routine ticks are tracked silently.\n\n` +
          `*Reply \`!unfollow\` anytime to stop alerts for this trade.*`;
      }
    } else if (parsed.cmd === 'unfollow' || parsed.cmd === 'exited') {
      if (!postId || !postId.startsWith('t3_')) {
        reply = 'Reply directly on the signal post with `!unfollow` to unsubscribe from that position.';
      } else {
        await removePostFollower(postId, authorName);
        reply = `🔕 **Unsubscribed u/${authorName}** from live updates on this position.`;
      }
    } else if (parsed.cmd === 'help') {
      reply = formatHelpComment();
    } else if (parsed.cmd === 'calc') {
      if (parsed.args.length < 2) {
        reply = 'Usage: `!calc <symbol> <equity> [risk%]` — e.g. `!calc BOOM99 1000 1.0`.';
      } else {
        const calc = await calcPositionFromParams({
          symbol: parsed.args[0],
          equity: parsed.args[1].replace(/[$,]/g, ''),
          risk_pct: (parsed.args[2] ?? '1.0').replace(/%/g, ''),
        });
        reply = formatCalcComment(calc);
      }
    } else if (parsed.cmd === 'scan') {
      const token = parsed.args.join(' ');
      if (!token) {
        reply = 'Usage: `!scan <symbol>` — e.g. `!scan CRASH200` or `!scan BOOM99`.';
      } else {
        const res = await scanSymbolFromSlack(token);
        reply = res
          ? `### 🔎 Latest Live Strategy v3 Signal · ${res.name} (\`${res.symbol}\`)\n**${res.direction_hint}** @ \`${res.price_formatted}\` · Channel \`#${res.channel_name}\`\n\n${res.adaptation?.summary ?? ''}`
          : `No recent live signal found for \`${token}\` across the 12 market channels yet.`;
      }
    } else {
      const ov = await getLiveOverviewFromSlack();
      reply = [
        '### 🛰️ Cerynaean Hind — Live Strategy v3 Status',
        `- **Status:** 🟢 Live via Slack Relay (\`v3_regime\`) · Min Post Grade \`${ov.settings.min_grade}\``,
        `- **Recent Live Signals Indexed:** \`${ov.recent_signals.length}\` across **13 market channels**`,
        ...ov.recent_signals
          .slice(0, 6)
          .map(
            (s) =>
              `- ${s.direction === 'LONG' ? '🟢 **LONG**' : '🔴 **SHORT**'} \`${s.symbol}\` (${s.grade}) @ \`${s.plan.entry}\` · \`#${s.channel_name}\``
          ),
      ].join('\n');
    }
  } catch (err) {
    if (err instanceof SlackRelayError || err instanceof Error) {
      reply = `⚠️ **Cerynaean Hind:** ${err.message}`;
    } else {
      reply = '⚠️ Could not fetch live data right now. Please try again shortly.';
    }
  }

  if (!reply) return false;
  await reddit.submitComment({
    id: commentId as T1,
    text: reply,
  });
  return true;
}

export async function getUserWatchlist(username: string): Promise<string[]> {
  if (!username) return [];
  const raw = await redis.get(`ch:watch:${username}`);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? arr.map(String) : [];
  } catch {
    return [];
  }
}

export async function updateUserWatchlist(
  username: string,
  action: 'add' | 'remove',
  symbol: string
): Promise<string[]> {
  const clean = symbol.trim().toUpperCase();
  if (!username || !clean) return getUserWatchlist(username);
  const current = await getUserWatchlist(username);
  let next = current;
  if (action === 'add') {
    if (!current.includes(clean) && current.length < 20) {
      next = [...current, clean];
    }
  } else {
    next = current.filter((s) => s !== clean);
  }
  await redis.set(`ch:watch:${username}`, JSON.stringify(next));
  return next;
}

export async function getUserJournal(username: string): Promise<JournalRecord[]> {
  if (!username) return [];
  const raw = await redis.get(`ch:journal:${username}`);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? (arr as JournalRecord[]) : [];
  } catch {
    return [];
  }
}

export async function logUserJournalEntry(
  username: string,
  sig: Pick<SignalDto, 'symbol' | 'name' | 'direction' | 'grade' | 'plan'>,
  equity: number,
  riskPct: number,
  units: number,
  riskAmount: number,
  note: string
): Promise<JournalRecord[]> {
  const existing = await getUserJournal(username);
  const entry: JournalRecord = {
    id: `${username}-${sig.symbol}-${Date.now()}`,
    symbol: sig.symbol,
    name: sig.name,
    direction: sig.direction,
    entry: sig.plan.entry,
    sl: sig.plan.sl,
    tp1: sig.plan.tp1,
    tp2: sig.plan.tp2,
    tp3: sig.plan.tp3,
    equity,
    riskPct,
    riskAmount,
    units,
    grade: sig.grade,
    note: note.trim().slice(0, 140),
    loggedEpoch: Math.floor(Date.now() / 1000),
  };
  const next = [entry, ...existing].slice(0, 100);
  await redis.set(`ch:journal:${username}`, JSON.stringify(next));
  return next;
}
