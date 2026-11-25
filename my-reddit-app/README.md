# Cerynaean Hind — Reddit Devvit App (`r/GlvnsOrg`)

Reddit-native mirror of the **Cerynaean Hind (Strategy v2 · 15m Structure + 1H/4H Macro)** Slack trading bot, built on Reddit's official **Devvit Web (`0.15.4`)** platform and connected live via Reddit's globally allowlisted `slack.com` API relay (zero custom-domain approval wait!).

## What It Builds Inside `r/GlvnsOrg`

1. **13 Dedicated Market Channels (Slack 1:1 Mirror):**
   - Automatically creates all **13 colored Post Flair channels** (`🌐 #trading · Pulse & EOD`, `💥 #crash-boom`, `📈 #volatility-indices`, `🪜 #step-indices`, `🦘 #jump-indices`, `📦 #range-break`, `🔄 #daily-reset`, `💱 #forex-majors`, `🌐 #forex-minors`, `🧺 #fx-baskets`, `🥇 #metals`, `₿ #crypto-signals`, `📊 #stock-indices`).
   - Adds a clickable **"Market Channels"** sidebar widget right inside `r/GlvnsOrg` and posts a **Channel Directory & Bot Commands Guide**.
2. **Option B — Clean Native Reddit Text Signal Posts (Auto-Approved):**
   - Every qualifying Strategy v2 signal posts as a clean native Reddit text post tagged with its channel flair and auto-approved immediately (`post.approve()`).
   - Opens with a compact, organized key-level summary (`Entry | Stop Loss | TP1 | TP2 | TP3` + Setup / Regime & Risk / Confluence & Edge) with zero redundant metadata or `>` blockquotes, expanding into full structured Markdown tables (**Execution Ladder**, **Multi-Timeframe Alignment**, **Confluence Checklist**, **Why This Setup**, **Market Data**, **Walk-Forward Track Record**) when clicked open.
3. **Live Lifecycle Comments + 3-Hour Pulses:**
   - Cron job (`*/2 * * * *`) streams signals via Reddit's allowlisted `slack.com` relay, comments **TP1 / TP2 / TP3 / PARAM_BE / PARAM_TRAIL / SL / EARLY EXIT / TIME EXPIRY** directly under each signal post, and updates the post flair on close.
   - Cron job (`*/15 * * * *`) publishes UTC-aligned **3-Hour Market Pulses (`1/8–7/8`)** and the **`00:00 UTC` End-of-Day Desk Recap** under `🌐 #trading · Pulse & EOD`.
4. **Interactive Comment Bot Commands (`r/GlvnsOrg`):**
   - Reply anywhere with `!scan <symbol>`, `!calc <symbol> <equity> [risk%]`, `!status`, `!open`, `!stats`, `!edges`, or `!help`.

## First-Time Launch into `r/GlvnsOrg`

```powershell
cd my-reddit-app

# 1. Authenticate Devvit CLI with your Reddit account (opens browser once)
npm run login

# 2. Store SLACK_BOT_TOKEN (from your workspace .env) in Devvit global secret settings
npx devvit settings set slackBotToken

# 3. Build, upload, and install into r/GlvnsOrg
npm run launch
```

### Live Local Playtest Mode

```powershell
npm run dev
```

### Verification Commands

```powershell
npm run typecheck
npm test
npm run build
```

## Subreddit Mod Controls

In `r/GlvnsOrg`, open the subreddit `...` menu (or Mod Settings → Installed Apps → `cerynaean-hind`):
- **Setup GlvnsOrg Market Channels & Flairs** — creates/repairs all 13 flairs, sidebar widget & channel directory.
- **Post Interactive Command Center** — publishes & pins the interactive multi-channel Command Center post.
- **Sync Live Signals & Pulse Now** — forces an immediate sync from your live Slack channels.
- **Minimum Grade for Individual Subreddit Signal Posts** — defaults to `B+` (adjustable live to `A+`, `A`, `B+`, `B`, or `ALL`!).
