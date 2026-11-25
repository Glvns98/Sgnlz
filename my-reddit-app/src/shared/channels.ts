export interface MarketChannelDef {
  id: string;
  emoji: string;
  label: string;
  flairText: string;
  bgColor: string;
  textColor: 'light' | 'dark';
  description: string;
  category: 'hub' | 'synthetic' | 'macro';
}

export const MARKET_CHANNELS: readonly MarketChannelDef[] = [
  {
    id: 'trading',
    emoji: '🌐',
    label: '#trading · Master Hub',
    flairText: '🌐 #trading · Pulse & EOD',
    bgColor: '#2563EB',
    textColor: 'light',
    description: 'Master overview, UTC-aligned 12-hour Market Pulses, and 00:00 UTC EOD Desk Recaps',
    category: 'hub',
  },
  {
    id: 'crash-boom',
    emoji: '💥',
    label: '#crash-boom',
    flairText: '💥 #crash-boom',
    bgColor: '#DC2626',
    textColor: 'light',
    description: 'Crash & Boom synthetic indices (CRASH500, CRASH1000, BOOM500, BOOM1000, etc.)',
    category: 'synthetic',
  },
  {
    id: 'volatility-indices',
    emoji: '📈',
    label: '#volatility-indices',
    flairText: '📈 #volatility-indices',
    bgColor: '#7C3AED',
    textColor: 'light',
    description: 'Volatility 10–250 & 1-second continuous synthetic indices',
    category: 'synthetic',
  },
  {
    id: 'step-indices',
    emoji: '🪜',
    label: '#step-indices',
    flairText: '🪜 #step-indices',
    bgColor: '#0D9488',
    textColor: 'light',
    description: 'Step Index & Multi-Step symmetric random walk indices',
    category: 'synthetic',
  },
  {
    id: 'jump-indices',
    emoji: '🦘',
    label: '#jump-indices',
    flairText: '🦘 #jump-indices',
    bgColor: '#EA580C',
    textColor: 'light',
    description: 'Jump 10, 25, 50, 75 & 100 shock-volatility indices',
    category: 'synthetic',
  },
  {
    id: 'range-break',
    emoji: '📦',
    label: '#range-break',
    flairText: '📦 #range-break',
    bgColor: '#4F46E5',
    textColor: 'light',
    description: 'Range Break 100 & 200 mean-reverting/breakout indices',
    category: 'synthetic',
  },
  {
    id: 'daily-reset',
    emoji: '🔄',
    label: '#daily-reset',
    flairText: '🔄 #daily-reset',
    bgColor: '#0891B2',
    textColor: 'light',
    description: 'Bull Market & Bear Market Daily Reset indices',
    category: 'synthetic',
  },
  {
    id: 'forex-majors',
    emoji: '💱',
    label: '#forex-majors',
    flairText: '💱 #forex-majors',
    bgColor: '#16A34A',
    textColor: 'light',
    description: 'Major Forex pairs (EURUSD, GBPUSD, USDJPY, etc. · 07:00–20:00 UTC)',
    category: 'macro',
  },
  {
    id: 'forex-minors',
    emoji: '🌐',
    label: '#forex-minors',
    flairText: '🌐 #forex-minors',
    bgColor: '#15803D',
    textColor: 'light',
    description: 'Minor & Cross Forex pairs (EURJPY, GBPJPY, AUDNZD, etc. · 07:00–20:00 UTC)',
    category: 'macro',
  },
  {
    id: 'fx-baskets',
    emoji: '🧺',
    label: '#fx-baskets',
    flairText: '🧺 #fx-baskets',
    bgColor: '#0284C7',
    textColor: 'light',
    description: 'USD, EUR, GBP, AUD & Gold currency baskets',
    category: 'macro',
  },
  {
    id: 'metals',
    emoji: '🥇',
    label: '#metals',
    flairText: '🥇 #metals',
    bgColor: '#CA8A04',
    textColor: 'dark',
    description: 'Gold (XAUUSD), Silver (XAGUSD), Platinum & Palladium (07:00–20:00 UTC)',
    category: 'macro',
  },
  {
    id: 'crypto-signals',
    emoji: '₿',
    label: '#crypto-signals',
    flairText: '₿ #crypto-signals',
    bgColor: '#D97706',
    textColor: 'light',
    description: '24/7 Cryptocurrency pairs (BTCUSD, ETHUSD)',
    category: 'macro',
  },
  {
    id: 'stock-indices',
    emoji: '📊',
    label: '#stock-indices',
    flairText: '📊 #stock-indices',
    bgColor: '#334155',
    textColor: 'light',
    description: 'Global OTC Stock Indices (US Tech 100, Wall Street 30, Germany 40, etc.)',
    category: 'macro',
  },
] as const;

export const CHANNEL_BY_ID: Record<string, MarketChannelDef> = Object.fromEntries(
  MARKET_CHANNELS.map((c) => [c.id, c])
);

export function getChannelDef(channelId: string): MarketChannelDef {
  return CHANNEL_BY_ID[channelId] ?? MARKET_CHANNELS[0];
}
