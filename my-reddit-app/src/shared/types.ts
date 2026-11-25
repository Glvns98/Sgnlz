export interface EdgeSummary {
  n: number;
  tp1_rate: number;
  win_rate: number;
  avg_r: number;
  shrunk_avg_r: number;
  days: number;
  consistent: boolean;
  validated: boolean;
}

export interface TfViewDto {
  label: string;
  bias: string;
  momentum: string;
  squeeze: string;
  aligned: boolean;
}

export interface SignalPlanDto {
  entry: number;
  entry_limit: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  risk: number;
  atr: number;
  tp_r: [number, number, number];
}

export interface AdaptationDto {
  code: string;
  title: string;
  badge: string;
  sl_mult: number;
  tp_mult: number;
  size_mult: number;
  partial_tp1: number;
  early_be_r: number | null;
  trail_atr_mult: number;
  horizon_bars: number;
  summary: string;
}

export interface SignalDto {
  id: string;
  published_epoch: number;
  symbol: string;
  name: string;
  class_key: string;
  class_label: string;
  emoji: string;
  channel_name: string;
  decimals: number;
  pip_value: number;
  pip_unit: string;
  is_forex: boolean;
  direction: 'LONG' | 'SHORT';
  score: number;
  grade: string;
  triggers: string[];
  candle_epoch: number;
  close_epoch: number;
  bar_seconds: number;
  timeframe: string;
  strategy_mode: string;
  edge_status: string;
  net_of_costs: boolean;
  valid_until: number;
  session: string;
  plan: SignalPlanDto;
  adaptation: AdaptationDto;
  views: TfViewDto[];
  checks: { label: string; passed: boolean }[];
  reasons: string[];
  warnings: string[];
  metrics: {
    val: number | null;
    prev_val: number | null;
    squeeze_state: string;
    squeeze_bars: number;
    compression_pct: number | null;
    atr_pct: number | null;
    adx: number | null;
    rsi: number | null;
    ema_stack: string;
    resistance: number[];
    support: number[];
    range_24h_pos: number | null;
  };
  edge_dir: EdgeSummary;
  explain: {
    title: string;
    summary: string;
    mtf_explain: string;
    edge_explain: string;
    checklist: string;
    warnings_text: string;
  };
}

export interface TradeEventDto {
  event_id: string;
  signal_id: string;
  timestamp: number;
  channel_name: string;
  symbol: string;
  name: string;
  direction: 'LONG' | 'SHORT';
  kind: string;
  r_total: number;
  price: number;
  final: boolean;
  old_sl: number | null;
  new_sl: number | null;
  old_tp: number | null;
  new_tp: number | null;
  reason: string;
  entry: number;
  active_sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  stage: number;
  bars: number;
  horizon: number;
  decimals: number;
  regime_code: string;
  partial_tp1: number;
  partial_tp2: number;
  runner_fraction: number;
}

export interface OpenTradeDto {
  id: string;
  symbol: string;
  name: string;
  channel_name: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  sl: number;
  active_sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  risk: number;
  r1: number;
  r2: number;
  r3: number;
  stage: number;
  banked: number;
  be_locked: boolean;
  bars: number;
  horizon: number;
  decimals: number;
  grade: string;
  regime_code: string;
  regime_badge: string;
  class_key: string;
  opened_epoch: number;
  bar_seconds: number;
  max_favorable_r: number;
  max_adverse_r: number;
}

export interface PerfSummaryDto {
  n: number;
  wins: number;
  tp1_hits: number;
  win_rate: number;
  tp1_rate: number;
  total_r: number;
  avg_r: number;
}

export interface BucketPerfDto {
  label: string;
  n: number;
  win_rate: number;
  total_r: number;
  avg_r: number;
}

export interface DetailedStatsDto {
  n: number;
  wins: number;
  losses: number;
  breakevens: number;
  tp1_hits: number;
  win_rate: number;
  tp1_rate: number;
  total_r: number;
  avg_r: number;
  profit_factor: number | null;
  profit_factor_inf: boolean;
  max_drawdown_r: number;
  best_r: number;
  worst_r: number;
  gross_win_r: number;
  gross_loss_r: number;
  current_streak: number;
  avg_bars: number;
  by_regime: BucketPerfDto[];
  by_grade: BucketPerfDto[];
  by_class: BucketPerfDto[];
  top_symbols: BucketPerfDto[];
}

export interface SummaryRowDto {
  symbol: string;
  name: string;
  class_label: string;
  channel_name: string;
  emoji: string;
  decimals: number;
  close: number;
  price_formatted: string;
  change_1h_pct: number;
  range_1h_atr: number;
  pos_1h: number;
  bias_5m: string;
  bias_15m: string;
  bias_1h: string;
  trend_label: string;
  squeeze_state: string;
  squeeze_bars: number;
  progress_label: string;
  active_trade_status: string;
}

export interface ChannelCatalogItem {
  id: string;
  title: string;
  description: string;
  symbol_count: number;
  open_count: number;
  recent_signal_count: number;
}

export interface PulseItemDto {
  id: string;
  kind: 'intraday_pulse' | 'eod_recap';
  timestamp: number;
  trading_day: string;
  pulse_slot?: [number, number];
  data: Record<string, unknown>;
}

export interface BridgeOverviewDto {
  status: {
    connected: boolean;
    phase: string;
    uptime_seconds: number;
    symbols_total: number;
    symbols_loaded: number;
    validated_pairs: number;
    signals_sent: number;
    signals_filtered: number;
    last_cycle_epoch: number;
    last_cycle_ms: number;
    last_error: string | null;
  };
  settings: {
    strategy_mode: string;
    session_filter: boolean;
    edge_mode: string;
    min_grade: string;
    default_equity: number;
    default_risk_pct: number;
  };
  stats: DetailedStatsDto;
  perf: {
    '1h': PerfSummaryDto;
    '3h': PerfSummaryDto;
    '12h'?: PerfSummaryDto;
    today: PerfSummaryDto;
    '24h': PerfSummaryDto;
    '7d': PerfSummaryDto;
    all: PerfSummaryDto;
  };
  daily: {
    trading_day: string;
    pulse_slot: [number, number];
    signals_sent: number;
    signals_filtered: number;
    pulses_sent: number;
  };
  open_trades: OpenTradeDto[];
  validated_edges: {
    symbol: string;
    direction: string;
    grade: string;
    n: number;
    tp1: number;
    avg_r: number;
    rank: number;
  }[];
  channels: ChannelCatalogItem[];
  recent_signals: SignalDto[];
  recent_events: TradeEventDto[];
  latest_pulse: PulseItemDto | null;
  server_epoch: number;
}

export interface ChannelViewDto {
  channel: { id: string; title: string; description: string };
  trading_day: string;
  pulse_slot: [number, number];
  breadth: {
    total: number;
    bull_1h: number;
    bear_1h: number;
    sqz_fired: number;
    sqz_on: number;
  };
  perf: {
    '1h': PerfSummaryDto;
    '3h': PerfSummaryDto;
    '12h'?: PerfSummaryDto;
    today: PerfSummaryDto;
  };
  open_trades: OpenTradeDto[];
  recent_signals: SignalDto[];
  recent_events: TradeEventDto[];
  summaries: SummaryRowDto[];
  server_epoch: number;
}

export interface ScanResultDto {
  symbol: string;
  name: string;
  class_label: string;
  channel_name: string;
  decimals: number;
  close: number;
  price_formatted: string;
  epoch: number;
  bar_seconds: number;
  timeframe: string;
  strategy_mode: string;
  val: number | null;
  prev_val: number | null;
  squeeze: string;
  direction_hint: 'LONG' | 'SHORT';
  atr: number;
  adx: number | null;
  rsi: number | null;
  ema_stack: string;
  views: TfViewDto[];
  blockers: string[];
  edge: EdgeSummary;
  adaptation: {
    code: string;
    title: string;
    badge: string;
    size_mult: number;
    tp_r: [number, number, number];
    summary: string;
  } | null;
}

export interface CalcResultDto {
  symbol: string;
  name: string;
  direction: string;
  decimals: number;
  regime_label: string;
  result: {
    entry: number;
    sl: number;
    tp1: number;
    tp2: number;
    tp3: number;
    equity: number;
    base_risk_pct: number;
    size_mult: number;
    effective_risk_pct: number;
    risk_amount: number;
    stop_distance: number;
    units: number;
    stop_pips: number;
    pip_unit: string;
    tp1_profit: number;
    tp2_profit: number;
    tp3_profit: number;
  };
}

export interface PlaybookTopicDto {
  title: string;
  level: string;
  sections: { heading: string; body: string }[];
}

export interface JournalRecord {
  id: string;
  symbol: string;
  name: string;
  direction: string;
  entry: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  equity: number;
  riskPct: number;
  riskAmount: number;
  units: number;
  grade: string;
  note: string;
  loggedEpoch: number;
}

/** Compact JSON payload (< 2 KB) stored directly on each Devvit Signal Custom Post for instant feed rendering. */
export interface SignalCardPostData {
  [key: string]: string | number | boolean | null;
  kind: 'signal';
  signalId: string;
  sym: string;
  name: string;
  dir: 'LONG' | 'SHORT';
  grade: string;
  score: number;
  ch: string;
  chLabel: string;
  chColor: string;
  entry: number;
  limit: number;
  sl: number;
  tp1: number;
  tp2: number;
  tp3: number;
  dec: number;
  r1: number;
  r2: number;
  r3: number;
  tp1Bank: number;
  tp2Bank: number;
  tp3Bank: number;
  atrMult: string;
  riskPct: number;
  sizeMult: number;
  pipValue: number;
  pipUnit: string;
  regime: string;
  trigger: string;
  edge: string;
  tf: string;
  session: string;
  timeUtc: string;
  horizon: string;
  confluence: string;
  status: string;
  rLabel: string;
  closed: boolean;
}

export interface SignalCardEventItem {
  ts: string;
  kindTag: string;
  title: string;
  summary: string;
  rLabel: string;
  isFinal: boolean;
  epoch: number;
}

/** Rich structured breakdown stored in Devvit Redis per signal post and shown in the expanded modal. */
export interface SignalCardDetail {
  card: SignalCardPostData;
  regimeSummary: string;
  validUntil: string;
  riskFields: { label: string; value: string }[];
  mtfRows: { label: string; detail: string; aligned: boolean }[];
  confluenceSummary: string;
  confluencePassed: string[];
  confluenceMissed: string[];
  marketData: { label: string; value: string }[];
  whySetup: string[];
  warnings: string[];
  trackRecordTitle: string;
  trackRecordSummary: string;
  events: SignalCardEventItem[];
}
