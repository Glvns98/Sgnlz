import type { PlaybookTopicDto } from './types.js';

export const PLAYBOOK_TOPICS: Record<string, PlaybookTopicDto> = {
  structure: {
    title: '🏗️ Strategy v3 · Multi-Timeframe Regime Engine',
    level: 'Competent → Expert',
    sections: [
      {
        heading: 'Symbol-Specialized Timeframes (30m & 1H)',
        body: 'Strategy v3 assigns each real instrument to its empirically optimal timeframe: 1H (+ 4H/12H macro) for Forex Majors/Minors, Metals, Equity Indices, and FX Baskets; 30m (+ 2H/8H macro) for 24/7 Crypto and Gold Basket (WLDXAU). Driftless RNG synthetic indices are quarantined by default.',
      },
      {
        heading: 'Three Causal Institutional Setups',
        body: '• A_PB — HTF Pullback + Micro-BOS: Trend pullback into the EMA21/50 value zone followed by micro-structure breakout in alignment with HTF flow.\n• B_BRK — Squeeze Fire Breakout: Volatility compression releasing into directional momentum expansion.\n• D_REC — EMA21 Reclaim Continuation: Reclaim across EMA21 after shallow counter-trend dip in an established macro trend.',
      },
      {
        heading: 'Portfolio Guardrails & 12h Reporting',
        body: '• Regional Session Windows: Restricts FX, Metals, and Cash Equity Indices to liquid UTC hours; Crypto & Baskets run 24/7.\n• Correlation & Heat Caps: Limits correlated currency-factor exposure (max 2) and total open portfolio risk (≤ 5.0%).\n• Net-of-Cost Validation: Non-overlapping walk-forward calibration with a 90% CI negative-drift circuit breaker.\n• 12-Hour Pulse + Daily Reset: Posts 12-hour Market Pulses at 12:00 UTC and an EOD Desk Recap with counter reset at 00:00 UTC.',
      },
    ],
  },
  squeeze: {
    title: '🌀 Squeeze Momentum Mechanics',
    level: 'Beginner',
    sections: [
      {
        heading: 'What is a Squeeze?',
        body: 'Squeeze ON occurs when Bollinger Bands contract strictly inside Keltner Channels — volatility has coiled like a spring. When bands expand back outside Keltner Channels, the squeeze FIRES into a directional impulse.',
      },
      {
        heading: 'Histogram Acceleration',
        body: '• Accelerating momentum confirms early impulse expansion.\n• Fading momentum warns of pullback risk — prefer limit entries or wait for structure reclaim.',
      },
    ],
  },
  regimes: {
    title: '🧭 Multi-Timeframe Regime Adaptation',
    level: 'Competent',
    sections: [
      {
        heading: 'Adaptive Risk & Targets',
        body: '• 🔥 STRONG_TREND / 🌊 MODERATE_TREND: Full or scaled trend-following risk with 1.10R–1.25R TP1, 2.00R–2.30R TP2, and 3.20R–3.80R runner targets.\n• 🚀 BREAKOUT: Momentum expansion from coiled squeeze conditions with ATR-buffered structural stops.\n• ⚖️ RANGE / ⚡ HIGH_VOL_SPIKE / 🌫️ NOISY: Automatically adapts stop width, position scale, or suppresses weak low-conviction setups.',
      },
    ],
  },
  risk: {
    title: '🧮 Risk Sizing & 3-Stage Scaling',
    level: 'Competent → Advanced',
    sections: [
      {
        heading: 'What is 1R?',
        body: '1R equals |Entry - SL|. On a $1,000 account risking 1%, 1R = $10. Position Size = ($ Risk) ÷ |Entry - SL|. Use the Size Calc tab or comment !calc <symbol> <equity> [risk%] on any post.',
      },
      {
        heading: '3-Stage Take Profit Ladder',
        body: '1. TP1 hit: Bank first partial (40–45%) and move Stop Loss to exact Entry (breakeven).\n2. TP2 hit: Bank half of remaining size and raise Stop Loss to TP1 (locking guaranteed profit).\n3. TP3 / Runner: Trail final runner at 1.4×–1.5× ATR toward TP3.',
      },
    ],
  },
  lifecycle: {
    title: '📡 Live Lifecycle Comment Alerts',
    level: 'Competent',
    sections: [
      {
        heading: 'Live Alerts Commented on Every Signal Post',
        body: '• 🛡️ PARAM_BE: Move stop to entry (zero downside).\n• 📐 PARAM_TRAIL: Raise trailing stop to lock profit.\n• 🎯 TP1 / 🥈 TP2 / 👑 TP3: Staged profit targets.\n• 📈 TRAIL_SL / 🛡️ BE: Runner closed on locked stop.\n• 🛑 SL: Initial structural stop reached (-1.00R).\n• ⚠️ INVALIDATED: Momentum flipped across zero and price closed across EMA21 — exit early.',
      },
    ],
  },
  edges: {
    title: '🧪 Net-of-Cost Edge Validation',
    level: 'Advanced → Expert',
    sections: [
      {
        heading: 'Statistical Honesty',
        body: 'Every 6 hours the Railway engine calibrates walk-forward trades net of per-class spreads and Crash/Boom spike-gap SL slippage. A direction is marked Proven Edge only when it clears minimum sample size, average R, and profitability across both halves. Historical backtests never guarantee future returns.',
      },
    ],
  },
};
