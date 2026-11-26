import express from 'express';
import { context, createServer, getServerPort, reddit } from '@devvit/web/server';
import { PLAYBOOK_TOPICS } from '../shared/playbook.js';
import type { SignalDto } from '../shared/types.js';
import {
  calcPositionFromParams,
  disableLiveTradeAlertsForCurrentUser,
  enableLiveTradeAlertsForCurrentUser,
  ensureSubredditChannels,
  getLiveChannelFromSlack,
  getLiveOverviewFromSlack,
  getSignalCardDetail,
  getUserJournal,
  getUserWatchlist,
  handleCommentCommand,
  logUserJournalEntry,
  removeCommandCenterAndUpgradeSignalCards,
  scanSymbolFromSlack,
  syncPulsesAndEod,
  syncSignalsAndLifecycle,
  updateUserWatchlist,
} from './syncService.js';

const app = express();
app.use(express.json({ limit: '1mb' }));

// ---------- Signal Card Post API (/api/signal-post) ----------

app.get('/api/signal-post', async (_req, res) => {
  try {
    const detail = await getSignalCardDetail(
      context.postId,
      context.postData as Record<string, unknown> | undefined
    );
    if (!detail) {
      res.status(404).json({ error: 'Signal breakdown not found for this post.' });
      return;
    }
    res.json({
      postId: context.postId ?? null,
      subredditName: context.subredditName ?? 'GlvnsOrg',
      username: (await reddit.getCurrentUsername()) ?? 'guest',
      detail,
    });
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed loading signal card' });
  }
});

// ---------- Client WebView API (/api/*) ----------

app.get('/api/init', async (_req, res) => {
  const username = (await reddit.getCurrentUsername()) ?? 'guest';
  const subredditName = context.subredditName ?? 'GlvnsOrg';
  const [watchlist, journal] = await Promise.all([
    username !== 'guest' ? getUserWatchlist(username) : Promise.resolve([]),
    username !== 'guest' ? getUserJournal(username) : Promise.resolve([]),
  ]);

  try {
    const overview = await getLiveOverviewFromSlack();
    res.json({
      username,
      subredditName,
      overview,
      watchlist,
      journal,
      bridgeError: null,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Unable to reach live Slack signal feed.';
    res.json({
      username,
      subredditName,
      overview: null,
      watchlist,
      journal,
      bridgeError: msg,
    });
  }
});

app.get('/api/channel/:id', async (req, res) => {
  try {
    const data = await getLiveChannelFromSlack(req.params.id);
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: err instanceof Error ? err.message : 'Channel feed error' });
  }
});

app.get('/api/scan', async (req, res) => {
  const symbol = String(req.query.symbol ?? '').trim();
  if (!symbol) {
    res.status(400).json({ error: 'Provide a symbol (e.g. CRASH200, BOOM99, R_100).' });
    return;
  }
  try {
    const data = await scanSymbolFromSlack(symbol);
    if (!data) {
      res.status(404).json({ error: `No recent live signal found for '${symbol}' yet.` });
      return;
    }
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: err instanceof Error ? err.message : 'Scan failed' });
  }
});

app.get('/api/calc', async (req, res) => {
  try {
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(req.query)) {
      if (typeof v === 'string') params[k] = v;
    }
    const data = await calcPositionFromParams(params);
    res.json(data);
  } catch (err) {
    res.status(400).json({ error: err instanceof Error ? err.message : 'Calculation failed' });
  }
});

app.get('/api/playbook', (_req, res) => {
  res.json({ topics: PLAYBOOK_TOPICS });
});

app.post('/api/watchlist', async (req, res) => {
  const username = await reddit.getCurrentUsername();
  if (!username) {
    res.status(401).json({ error: 'Sign in to Reddit to manage your personal watchlist.' });
    return;
  }
  const action = req.body?.action === 'remove' ? 'remove' : 'add';
  const symbol = String(req.body?.symbol ?? '').trim();
  if (!symbol) {
    res.status(400).json({ error: 'Symbol is required.' });
    return;
  }
  const watchlist = await updateUserWatchlist(username, action, symbol);
  res.json({ watchlist });
});

app.post('/api/journal', async (req, res) => {
  const username = await reddit.getCurrentUsername();
  if (!username) {
    res.status(401).json({ error: 'Sign in to Reddit to log trades to your personal journal.' });
    return;
  }
  const sig = req.body?.signal as Pick<SignalDto, 'symbol' | 'name' | 'direction' | 'grade' | 'plan'> | undefined;
  const equity = Number(req.body?.equity ?? 1000);
  const riskPct = Number(req.body?.riskPct ?? 1);
  const units = Number(req.body?.units ?? 0);
  const riskAmount = Number(req.body?.riskAmount ?? 0);
  const note = String(req.body?.note ?? '');
  if (!sig || !sig.symbol || !sig.plan) {
    res.status(400).json({ error: 'Signal payload missing.' });
    return;
  }
  const journal = await logUserJournalEntry(username, sig, equity, riskPct, units, riskAmount, note);
  res.json({ journal });
});

// ---------- Devvit Internal Triggers (/internal/triggers/*) ----------

app.post('/internal/triggers/app-install', async (_req, res) => {
  const subredditName = context.subredditName;
  if (!subredditName) {
    res.status(200).json({ status: 'ok' });
    return;
  }
  try {
    await ensureSubredditChannels(subredditName);
    await syncSignalsAndLifecycle(subredditName);
    await syncPulsesAndEod(subredditName);
  } catch (err) {
    console.error('onAppInstall setup error:', err);
  }
  res.status(200).json({ status: 'ok' });
});

app.post('/internal/triggers/app-upgrade', async (_req, res) => {
  const subredditName = context.subredditName;
  if (subredditName) {
    try {
      await ensureSubredditChannels(subredditName);
      const cleanup = await removeCommandCenterAndUpgradeSignalCards(subredditName);
      const s = await syncSignalsAndLifecycle(subredditName);
      const p = await syncPulsesAndEod(subredditName);
      console.warn(
        `[SYNC] onAppUpgrade r/${subredditName}: removedCC=${cleanup.removedCommandCenters.join(',')} upgradedCards=${cleanup.upgradedSignalCards.join(',')} found=${s.totalFound} seen=${s.alreadySeen} postedSignals=${s.postedSignals} postedEvents=${s.postedEvents} signalPosts=${s.postIds.join(',')} postedPulses=${p.postedPulses}`
      );
    } catch (err) {
      console.error('onAppUpgrade setup error:', err);
    }
  }
  res.status(200).json({ status: 'ok' });
});

app.post('/internal/triggers/comment-create', async (req, res) => {
  try {
    const comment = req.body?.comment as { id?: string; body?: string; postId?: string } | undefined;
    const author = req.body?.author as { id?: string; name?: string } | undefined;
    const post = req.body?.post as { id?: string } | undefined;
    const postId = comment?.postId || post?.id || '';
    if (comment?.id && comment?.body) {
      await handleCommentCommand(comment.id, comment.body, author?.name ?? '', postId, author?.id ?? '');
    }
  } catch (err) {
    console.error('onCommentCreate error:', err);
  }
  res.status(200).json({ status: 'ok' });
});

// ---------- Devvit Scheduled Cron Tasks (/internal/scheduler/*) ----------

app.post('/internal/scheduler/sync-signals', async (_req, res) => {
  const subredditName = context.subredditName;
  if (!subredditName) {
    res.status(200).json({ status: 'skipped' });
    return;
  }
  try {
    const result = await syncSignalsAndLifecycle(subredditName);
    console.warn(
      `[SYNC] sync-signals r/${subredditName}: found=${result.totalFound} seen=${result.alreadySeen} signals=${result.postedSignals} events=${result.postedEvents} skipped=${result.skipped} posts=${result.postIds.join(',')}`
    );
    res.status(200).json({ status: 'ok', ...result });
  } catch (err) {
    console.error('sync-signals cron error:', err);
    res.status(200).json({ status: 'error', message: err instanceof Error ? err.message : String(err) });
  }
});

app.post('/internal/scheduler/sync-pulses', async (_req, res) => {
  const subredditName = context.subredditName;
  if (!subredditName) {
    res.status(200).json({ status: 'skipped' });
    return;
  }
  try {
    const result = await syncPulsesAndEod(subredditName);
    console.warn(`[SYNC] sync-pulses r/${subredditName}: pulses=${result.postedPulses} posts=${result.postIds.join(',')}`);
    res.status(200).json({ status: 'ok', ...result });
  } catch (err) {
    console.error('sync-pulses cron error:', err);
    res.status(200).json({ status: 'error', message: err instanceof Error ? err.message : String(err) });
  }
});

// ---------- Subreddit Menu Actions & Forms (/internal/menu/*, /internal/forms/*) ----------

app.post('/internal/menu/setup-channels', async (_req, res) => {
  const subredditName = context.subredditName;
  if (!subredditName) {
    res.json({ showToast: 'Subreddit context not available.' });
    return;
  }
  try {
    const out = await ensureSubredditChannels(subredditName);
    res.json({
      showToast: {
        text: `Ready! ${out.totalFlairs} market channel flairs active (${out.createdFlairs} new)${out.widgetCreated ? ' + sidebar widget added' : ''}.`,
        appearance: 'success',
      },
    });
  } catch (err) {
    res.json({ showToast: `Channel setup failed: ${err instanceof Error ? err.message : String(err)}` });
  }
});

app.post('/internal/menu/sync-now', async (_req, res) => {
  const subredditName = context.subredditName;
  if (!subredditName) {
    res.json({ showToast: 'Subreddit context not available.' });
    return;
  }
  try {
    const s = await syncSignalsAndLifecycle(subredditName);
    const p = await syncPulsesAndEod(subredditName);
    res.json({
      showToast: {
        text: `Synced r/${subredditName}: +${s.postedSignals} signals, +${s.postedEvents} updates, +${p.postedPulses} pulses.`,
        appearance: 'success',
      },
    });
  } catch (err) {
    res.json({ showToast: `Sync error: ${err instanceof Error ? err.message : String(err)}` });
  }
});

app.post('/internal/menu/quick-scan', async (_req, res) => {
  res.json({
    showForm: {
      name: 'quickScanForm',
      form: {
        title: 'Quick Strategy v3 Market Scan',
        description: 'Look up the latest live Strategy v3 signal for any Deriv instrument.',
        fields: [
          {
            type: 'string',
            name: 'symbol',
            label: 'Symbol or Market Name',
            placeholder: 'e.g. CRASH200, BOOM99, R_100, EURUSD',
            required: true,
          },
        ],
        acceptLabel: 'Scan Now',
        cancelLabel: 'Cancel',
      },
    },
  });
});

app.post('/internal/forms/quick-scan', async (req, res) => {
  const sym = String(req.body?.symbol ?? '').trim();
  if (!sym) {
    res.json({ showToast: 'Enter a symbol to scan.' });
    return;
  }
  try {
    const scan = await scanSymbolFromSlack(sym);
    if (!scan) {
      res.json({ showToast: `No recent live signal found for ${sym} yet.` });
      return;
    }
    res.json({
      showToast: {
        text: `${scan.symbol} (${scan.price_formatted}) · ${scan.direction_hint} · #${scan.channel_name}`.slice(0, 220),
        appearance: 'success',
      },
    });
  } catch (err) {
    res.json({ showToast: `Scan failed: ${err instanceof Error ? err.message : String(err)}` });
  }
});

app.post('/internal/menu/enable-alerts', async (_req, res) => {
  try {
    const out = await enableLiveTradeAlertsForCurrentUser();
    res.json({
      showToast: {
        text: out.message,
        appearance: out.username || out.pushOk ? 'success' : 'neutral',
      },
    });
  } catch (err) {
    res.json({ showToast: `Could not enable alerts: ${err instanceof Error ? err.message : String(err)}` });
  }
});

app.post('/internal/menu/disable-alerts', async (_req, res) => {
  try {
    const out = await disableLiveTradeAlertsForCurrentUser();
    res.json({
      showToast: {
        text: out.message,
        appearance: 'success',
      },
    });
  } catch (err) {
    res.json({ showToast: `Could not disable alerts: ${err instanceof Error ? err.message : String(err)}` });
  }
});

const server = createServer(app);
const port = getServerPort();
server.listen(port);
