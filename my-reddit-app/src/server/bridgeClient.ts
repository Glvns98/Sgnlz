import type {
  BridgeOverviewDto,
  CalcResultDto,
  ChannelViewDto,
  PlaybookTopicDto,
  PulseItemDto,
  ScanResultDto,
  SignalDto,
  TradeEventDto,
} from '../shared/types.js';

export interface BridgeConfig {
  baseUrl: string;
  token: string;
}

export interface FeedResponseDto {
  signals: SignalDto[];
  events: TradeEventDto[];
  pulses: PulseItemDto[];
  server_epoch: number;
}

export class BridgeClientError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'BridgeClientError';
    this.status = status;
  }
}

export class BridgeClient {
  private readonly baseUrl: string;
  private readonly token: string;

  constructor(cfg: BridgeConfig) {
    this.baseUrl = cfg.baseUrl.replace(/\/+$/, '');
    this.token = cfg.token.trim();
  }

  private async request<T>(path: string): Promise<T> {
    if (!this.baseUrl) {
      throw new BridgeClientError('Railway API Bridge URL is not configured in app settings.', 503);
    }
    if (!this.token) {
      throw new BridgeClientError(
        'Railway API Bridge Token is not configured yet. Run `npx devvit settings set apiBridgeToken` to connect.',
        503
      );
    }
    const url = `${this.baseUrl}${path}`;
    const resp = await fetch(url, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${this.token}`,
        Accept: 'application/json',
      },
    });
    const text = await resp.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = { error: text.slice(0, 200) };
    }
    if (!resp.ok) {
      const msg = typeof parsed.error === 'string' ? parsed.error : `Bridge HTTP ${resp.status}`;
      throw new BridgeClientError(msg, resp.status);
    }
    return parsed as unknown as T;
  }

  async getOverview(): Promise<BridgeOverviewDto> {
    return this.request<BridgeOverviewDto>('/api/overview');
  }

  async getFeed(params: {
    sinceEpoch?: number;
    channel?: string;
    minGrade?: string;
    limit?: number;
  }): Promise<FeedResponseDto> {
    const qs = new URLSearchParams();
    if (params.sinceEpoch) qs.set('since_epoch', String(params.sinceEpoch));
    if (params.channel) qs.set('channel', params.channel);
    if (params.minGrade) qs.set('min_grade', params.minGrade);
    if (params.limit) qs.set('limit', String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return this.request<FeedResponseDto>(`/api/feed${suffix}`);
  }

  async getChannel(channelId: string): Promise<ChannelViewDto> {
    return this.request<ChannelViewDto>(`/api/channel/${encodeURIComponent(channelId)}`);
  }

  async scan(symbol: string): Promise<ScanResultDto> {
    return this.request<ScanResultDto>(`/api/scan?symbol=${encodeURIComponent(symbol)}`);
  }

  async calc(params: Record<string, string | number>): Promise<CalcResultDto> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && String(v).trim() !== '') {
        qs.set(k, String(v));
      }
    }
    return this.request<CalcResultDto>(`/api/calc?${qs.toString()}`);
  }

  async getPlaybook(): Promise<{ topics: Record<string, PlaybookTopicDto> }> {
    return this.request<{ topics: Record<string, PlaybookTopicDto> }>('/api/playbook');
  }
}
