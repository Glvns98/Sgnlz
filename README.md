<div align="center">

# ⚡ Sgnlz — Deriv Squeeze 15m Intelligent Trade Management Engine

**An institutional-grade Python trading intelligence & dynamic position-management engine for Deriv markets — grounded 1:1 in LazyBear's [`Squeeze.pine`](Squeeze.pine), monitoring all ~95 instruments on `15m` candles with `30s` intra-bar protection and live threaded Slack + Reddit updates 24/7.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](pyproject.toml)
[![Timeframe: 15m Pure](https://img.shields.io/badge/Timeframe-15m%20Pure%20Scalp-FF6B35?style=for-the-badge)](config/settings.yaml)
[![Monitor: 30s Intra-Bar](https://img.shields.io/badge/Monitor-30s%20Intra--Bar-00B894?style=for-the-badge)](bot/app/lifecycle.py)
[![Pine Parity](https://img.shields.io/badge/Squeeze.pine-1%3A1%20Math%20Parity-2962FF?style=for-the-badge)](Squeeze.pine)
[![Tests: 35 Passing](https://img.shields.io/badge/Tests-27%20Python%20%2B%208%20TS%20Passing-2ECC71?style=for-the-badge)](bot/tests/)
[![Railway 24/7](https://img.shields.io/badge/Cloud-Railway%2024%2F7-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](app.py)
[![Slack & Reddit](https://img.shields.io/badge/Alerts-Slack%20%2B%20r%2FGlvnsOrg-FF4500?style=for-the-badge&logo=reddit&logoColor=white)](my-reddit-app/README.md)

</div>

---

## 🎯 Core Philosophy

> **The objective is NOT to maximize signal count.**  
> **A trade can lose despite a clean entry, and win despite a mediocre entry.**  
> **This engine concentrates its intelligence on _what happens after exposure exists_.**

Every active position runs through a deterministic **10-stage lifecycle state machine** — protecting capital, advancing stops monotonically to breakeven and profit floors, trailing dynamic volatility-scaled runners, cutting decaying momentum early, and posting **only meaningful action updates** as clean thread replies until trade close.

---

## ✨ Key Capabilities

- **📐 100% Pure [`Squeeze.pine`](Squeeze.pine) Signals (Zero External Indicator Clutter)**
  - Exact LazyBear implementation in [`bot/indicators/squeeze.py`](bot/indicators/squeeze.py): `ddof=0` population `stdev`, Pine `tr(false)` semantics, LazyBear `multKC` Bollinger quirk, closed-form OLS `linreg`, and `nz(val[1])` bar/dot coloring — verified line-by-line against an independent Pine reference.
  - Operates purely on **`15m` closed candles** (`900s`) with **no higher-timeframe (HTF) filter** blocking setups, and uses `Squeeze.pine`'s own `range_ma` for stop sizing and trailing distance.
- **🌐 Full-Universe Deriv v1 Discovery (~95 Instruments)**
  - Connects to Deriv WebSocket v1 (`wss://api.derivws.com/trading/v1/options/ws/public`) + REST `/accounts`, discovers all ~95 active instruments dynamically across Volatility, Crash/Boom, Step, Jump, Range Break, Daily Reset, Forex, Metals, Crypto, and Stock Indices, and prioritizes currently open markets.
- **🛡️ Continuous `30s` Intra-Bar Protection & Clean Exit Execution**
  - Evaluates closed `15m` candles strictly without repainting, **and** polls active forming bars every `30s` (`_monitor_open_position_intrabar`) for open trades so stop-loss hits, breakeven moves (`+0.50R`), profit locks (`+1.00R` ➜ `+0.45R` floor), and dynamic trails (`+1.50R+`) execute cleanly to closing.
- **💬 Noise-Free Threaded Slack & Reddit (`r/GlvnsOrg`) Alerts**
  - Suppresses repetitive market pulses and unentered signal spam (`send_market_pulses: false`, `send_unentered_analysis_signals: false`).
  - Streams rich Block Kit parent cards to 12 dedicated Slack submarket channels and mirrors them live to [`r/GlvnsOrg`](https://www.reddit.com/r/GlvnsOrg) via the included Devvit Web app (`my-reddit-app`), followed by threaded comments only on key protection moves and close.
- **🔒 Institutional Risk Guardrails**
  - Strictly prohibits martingale (`allow_martingale: false`) and averaging down (`allow_averaging_down: false`).
  - Enforces fixed-fractional risk sizing (`1%`), per-group exposure caps (`max_per_group_open: 2`), portfolio caps (`max_open_positions: 8`, `max_concurrent_risk: 8%`), daily loss & consecutive-loss circuit breakers, and double-locked `LIVE_TRADING` protection.

---

## 🏗️ System Architecture & Trade Lifecycle

### Engine Pipeline

```mermaid
flowchart LR
    D["Deriv WS v1 & REST\n(~95 Active Symbols)"] -->|"15m Closed Bars\n+ 30s Intra-Bar Forming"| MD["MarketDataEngine\nAnti-Repainting"]
    MD --> SQ["Squeeze.pine Core\nbot/indicators/squeeze.py"]
    SQ --> MS["MarketStateEngine\nState · Trajectory · Regime"]
    MS --> SG["15m SignalEngine\nPure Squeeze Scoring"]
    SG --> RK["RiskManager\nSizing · Group Caps · Breakers"]
    RK --> EX["ExecutionEngine\nPaper / Double-Locked Live"]
    EX --> TM["TradeManager\nBE · Profit Lock · Trail · Exit"]
    MD -.->|"30s Live Price"| TM
    TM --> DB["SQLite 9-Table Store\n+ JSONL Audit Log"]
    TM --> NT["Slack 12 Channels\n(Parent + Thread Replies)"]
    NT -->|"Devvit Sync (*/2m)"| RD["r/GlvnsOrg\nReddit Mirror"]
```

### 10-Stage Position Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> ENTERED: Risk Approved & Filled
    ENTERED --> INITIAL_PROTECTION: Initial Squeeze range_ma Stop Set
    INITIAL_PROTECTION --> PROFITABLE: +0.50R Reached -> Stop to Breakeven (+0.05R)
    PROFITABLE --> PROFIT_PROTECTION: +1.00R Reached -> Lock +0.45R Floor
    PROFIT_PROTECTION --> TRAILING: +1.50R+ -> Dynamic Volatility & Momentum Trail
    TRAILING --> MOMENTUM_DECAY: Deceleration / Zero-Cross Warning
    PROFITABLE --> MOMENTUM_DECAY: Stagnation / Thesis Weakening
    INITIAL_PROTECTION --> EXIT: Initial SL / Abnormal Volatility
    PROFITABLE --> EXIT: Breakeven Stop Hit
    PROFIT_PROTECTION --> EXIT: Profit-Lock Stop Hit
    TRAILING --> EXIT: Trailing Stop Hit / Exhaustion
    MOMENTUM_DECAY --> EXIT: Momentum Deterioration Exit
    EXIT --> CLOSED: P&L, MFE/MAE & Giveback Recorded
    CLOSED --> POST_TRADE_ANALYSIS: Entry vs Management Attribution
    POST_TRADE_ANALYSIS --> [*]
```

---

## 📱 Live Signal & Threaded Follow-Up Design

Every trade posts a clean, mobile-friendly **15m Scalp** card with a **plain-text Profit & Protection Ladder** (no code blocks or table cells) and tracks the trade in the same thread until close:

### Parent Signal Card (`15m Scalp`)

```markdown
## 🟢 LONG · Volatility 100 Index (`R_100`)
🏷️ `R_100` · ⚡ `15m Scalp` · 📈 #volatility-indices · ⏰ 02:25 UTC · 🆔 `TRD-3552EB71`

**🔥 B+ ⭐⭐⭐⭐☆ · 78/100** · 🚀 Expansion Trend · 💥 15m Squeeze Breakout

---

### 🎯 Scalp Execution Plan

- 📍 **Entry:** `1,653.50`
- 🛡️ **Stop Loss:** `1,622.00` (1.90% risk width)
- ⚖️ **Position Risk:** `1.00%` equity
- ⏱️ **Timeframe & Monitor:** `15m` pure squeeze · Monitored live until close

### 🪜 Profit & Protection Ladder

🔐 **Breakeven** (+0.50R) ➜ `1,669.25` · Move stop to entry (+0.05R)
🔒 **Profit Lock** (+1.00R) ➜ `1,685.00` · Lock +0.45R floor
📐 **Dynamic Trail** (+1.50R) ➜ `1,700.75` · Trail momentum wave
🚀 **Runner Mode** (+2.00R) ➜ `1,716.50` · Let trend run free

### ⚡ 15m Scalp Readout (5/6 checks)

⚪ Squeeze Released · 🟢 Lime (Surging Up) · `val` `+7.1658` · Strength `0.84` · 💥 Fired (6 bars coiled)

- ✅ Squeeze Release / Expansion Active
- ✅ Strong 15m Squeeze Momentum
- ✅ 15m Momentum Accelerating
- ✅ Clean Volatility Regime
```

### Threaded Comment Lifecycle Updates (Important Moves Only)

- **🛡️ `BREAKEVEN SECURED`:** Risk removed at `+0.50R` (`Stop 1,622.00 ➜ 1,655.08`).
- **🔒 `PROFIT LOCKED`:** Floor locked at `+0.45R` once price hits `+1.00R`.
- **📐 `TRAILING STOP MOVED`:** Dynamic momentum trail updates (only posted when stop advances by `>= 0.05R`).
- **🎉 `WIN` / 😌 `SCRATCH` / 🛡️ `LOSS CAPPED`:** Final exit attribution (`MFE / MAE`, giveback, bars held, entry vs management quality).

---

## 📡 Multi-Channel Slack & Reddit Routing

Instruments discovered from Deriv are automatically categorized into **12 dedicated Slack channels** + **matching `r/GlvnsOrg` post flairs**:

| Market Group | Slack Channel | Reddit Flair | Deriv Submarkets |
| :--- | :--- | :--- | :--- |
| **Volatility Indices** | `#volatility-indices` | `📈 #volatility-indices` | `random_index` (`R_10`–`R_100`, `1HZ*`) |
| **Crash & Boom** | `#crash-boom` | `💥 #crash-boom` | `crash_index` |
| **Step Indices** | `#step-indices` | `🪜 #step-indices` | `step_index` |
| **Jump Indices** | `#jump-indices` | `🦘 #jump-indices` | `jump_index` |
| **Range Break** | `#range-break` | `📦 #range-break` | `range_index`, `range_break` |
| **Daily Reset** | `#daily-reset` | `🔄 #daily-reset` | `random_daily` (`RDBULL`, `RDBEAR`) |
| **Forex Majors** | `#forex-majors` | `💱 #forex-majors` | `major_pairs` |
| **Forex Minors** | `#forex-minors` | `🌐 #forex-minors` | `minor_pairs` |
| **FX Baskets** | `#fx-baskets` | `🧺 #fx-baskets` | `forex_basket` |
| **Metals & Commodities** | `#metals` | `🥇 #metals` | `metals`, `commodity_basket`, `energy` |
| **Crypto** | `#crypto-signals` | `₿ #crypto-signals` | `non_stable_coin` |
| **Stock Indices (OTC)** | `#stock-indices` | `📊 #stock-indices` | `americas_OTC`, `asia_oceania_OTC`, `europe_OTC` |

---

## 🔬 `Squeeze.pine` Verification Matrix

| `Squeeze.pine` Component | Pine Script Definition | Python Verification |
| :--- | :--- | :--- |
| Bollinger Bands | `basis = sma(close, 20)`, `dev = multKC * stdev(close, 20)` | Exact population `ddof=0` & LazyBear `multKC` parity |
| Keltner Channels | `ma = sma(close, 20)`, `rangema = sma(tr, 20)` | Exact Pine `tr(false)` bar-0 `NaN` semantics |
| Squeeze States | `sqzOn`, `sqzOff`, `noSqz` + `scolor` (`black`/`gray`/`blue`) | Verified vectorized & streaming (`test_squeeze_indicator.py`) |
| Momentum Oscillator | `linreg(close - avg(avg(highest(high,20), lowest(low,20)), sma(close,20)), 20, 0)` | Closed-form OLS weights match `np.polyfit` to `1e-11` |
| Histogram Colors | `bcolor` (`lime`, `green`, `red`, `maroon` vs `nz(val[1])`) | Exact parity across warmup & interior `NaN` slices |

---

## 🗂️ Repository Structure

```text
Sgnlz/
├── Squeeze.pine                  # Untouched Pine Script indicator source of truth
├── app.py                        # Railway 24/7 container entrypoint (ENGINE_MODE=paper)
├── requirements.txt              # Runtime Python dependencies for Railway Railpack
├── pyproject.toml                # Package metadata, Ruff & Pytest config
├── config/
│   ├── settings.yaml             # 15m defaults, 30s polling, risk caps, notification filters
│   └── symbols.yaml              # Symbol override profiles (all configured for 15m)
├── docs/
│   └── ARCHITECTURE.md           # Deep-dive system diagrams & engineering spec
├── bot/
│   ├── app/                      # EngineOrchestrator, 30s intra-bar monitor, JSONL audit logs
│   ├── config/                   # Pydantic v2 YAML + ${ENV_VAR} config loader
│   ├── core/                     # Enums, immutable events, Position & MarketState models
│   ├── indicators/               # Exact Squeeze.pine engine (vectorized + streaming)
│   ├── strategy/                 # MarketStateEngine & pure 15m SignalEngine
│   ├── management/               # AdaptiveStopManager, ProfitStateManager, ExitEngine, TradeManager
│   ├── risk/                     # Portfolio RiskManager, group caps, circuit breakers
│   ├── deriv/                    # Async Deriv v1 WS + REST client, discovery, MarketDataEngine
│   ├── storage/                  # 9-table SQLite repository (restart recovery + PostgreSQL interface)
│   ├── analytics/                # Performance metrics, management quality, post-trade insights
│   ├── notifications/            # Formatter, priority/cooldown router, Slack & sanitized Reddit outbox
│   ├── dashboard/                # Rich live terminal dashboard
│   ├── backtest/                 # Zero-look-ahead causal replay, 5-policy comparison, Walk-Forward
│   └── tests/                    # 27 automated Python tests (parity, anti-repaint, intra-bar, Slack)
└── my-reddit-app/                # Reddit Devvit Web app (`cerynaean-hind`) mirroring Slack -> r/GlvnsOrg
```

---

## 🚀 Quick Start (Local Development)

### 1. Environment Setup

```powershell
python -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Create a local `.env` file (**never commit `.env`**):

```env
DERIV_APP_ID=your_deriv_app_id
DERIV_API_TOKEN=your_deriv_token
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
ENGINE_MODE=paper
```

### 2. Run Quality Checks & Tests

```powershell
# Python engine (27 tests + Ruff lint)
.\.venv\Scripts\ruff.exe check bot app.py
.\.venv\Scripts\pytest.exe -v

# Reddit Devvit mirror app (8 Vitest specs + TypeScript + Vite build)
cd my-reddit-app
npm install
npm run typecheck
npm test
npm run build
```

### 3. CLI Usage

```powershell
# Run in Paper Trading mode (live Deriv 15m stream + 30s intra-bar monitor)
.\.venv\Scripts\python.exe -m bot.app.main run --mode paper --no-dashboard

# Run 5-Policy Management Comparison Backtest (STATIC vs BE vs TRAIL vs MOM vs DYNAMIC)
.\.venv\Scripts\python.exe -m bot.app.main backtest --symbols R_100,R_75,R_50 --bars 1200

# Run Out-of-Sample Walk-Forward Validation
.\.venv\Scripts\python.exe -m bot.app.main walkforward --symbols R_100,R_75 --bars 1800 --folds 4

# Generate SQLite Performance & Management-Quality Attribution Report
.\.venv\Scripts\python.exe -m bot.app.main report --db data/squeeze_tme.db
```

> **Safety Lock:** Live execution requires **both** `--mode live` **and** `DERIV_ALLOW_LIVE_EXECUTION=true`. By default, `app.py` and `config/settings.yaml` never place real-money orders.

---

## ☁️ 24/7 Cloud Deployment

### Railway (Python Engine)
Railway automatically detects [`app.py`](app.py) and [`requirements.txt`](requirements.txt) via Railpack (`$ python app.py`), mounts persistent storage for SQLite/logs, and runs the 15m engine continuously:

```powershell
railway link -p <project-id> -e production -s <service-id>
railway variables --set "ENGINE_MODE=paper" --set "DERIV_APP_ID=..." --set "DERIV_API_TOKEN=..." --set "SLACK_BOT_TOKEN=..." --skip-deploys
railway up --ci --message "Deploy Deriv Squeeze 15m Engine"
```

### Reddit Devvit (`r/GlvnsOrg`)
Inside [`my-reddit-app/`](my-reddit-app), the `cerynaean-hind` Devvit Web app syncs Slack channel posts and thread replies onto Reddit every 2 minutes via Reddit's allowlisted `slack.com` relay:

```powershell
cd my-reddit-app
npx devvit upload --bump patch
npx devvit install GlvnsOrg
```

---

## ⚠️ Disclaimer

Synthetic indices, FX, and CFDs carry substantial risk of financial loss. Historical backtests, walk-forward evaluations, and paper-trading results do not guarantee future performance. This repository is a systematic risk-management and trade-lifecycle software engineering framework — **not financial advice**.
