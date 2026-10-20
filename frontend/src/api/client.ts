export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function wsUrl(path: string): string {
  const base = import.meta.env.VITE_WS_URL || API.replace(/^http/, 'ws');
  return `${base.replace(/\/$/, '')}${path}`;
}

const FOREX_CONTROL_TOKEN = (import.meta.env.VITE_FOREX_CONTROL_TOKEN || '').trim();

export async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts?.headers as Record<string, string> | undefined),
  };
  if (FOREX_CONTROL_TOKEN && path.startsWith('/forex/')) {
    headers['X-Forex-Control-Token'] = FOREX_CONTROL_TOKEN;
  }
  const response = await fetch(`${API}${path}`, {
    ...opts,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail
      : Array.isArray(body.detail) ? body.detail.map((entry: { msg?: string }) => entry.msg || 'Invalid request').join('; ')
      : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return response.json();
}

export interface ForexFactor {
  id: number | string;
  name: string;
  status: string;
  detail: unknown;
}

export interface ForexAnalysis {
  symbol: string;
  direction: 'RISE' | 'FALL' | 'WAIT';
  score: number;
  ready: boolean;
  blockers?: string[];
  reasons?: string[];
  timeframes?: Record<string, unknown> | Array<Record<string, unknown>>;
  factors?: ForexFactor[];
  quote?: Record<string, unknown> | null;
  candlesticks?: unknown;
  hybrid_passed?: boolean | null;
  ml?: {
    ready?: boolean;
    direction?: string | null;
    probability?: number | null;
    hybrid_passed?: boolean | null;
    reason?: string;
  };
}

export interface ForexSignal {
  id: string;
  symbol: string;
  direction: string;
  created_at: string;
  expiry_epoch: number;
  duration_seconds: number;
  score: number;
  execution_status: string;
  execution_result?: Record<string, unknown> | null;
}

export interface ForexSymbol { symbol: string; display_name: string; is_open: boolean }

export interface ForexChartCandle {
  epoch: number; open: number; high: number; low: number; close: number;
}

export interface ForexChartPayload {
  symbol: string;
  timeframe: string;
  candles: ForexChartCandle[];
  levels?: {
    support_resistance?: Array<{ price: number; type?: string }>;
    fibonacci?: { available?: boolean; levels?: Array<{ price: number; ratio: number }> };
    supply_demand?: Array<{ low: number; high: number; type?: string }>;
    liquidity?: Array<{ price: number; type?: string; epoch?: number }>;
  };
  events?: Array<{ epoch?: number; type?: string; name?: string; direction?: string }>;
  signals?: Array<{ created_epoch?: number; direction?: string; expiry_epoch?: number }>;
  analysis?: ForexAnalysis | null;
  ml?: Record<string, unknown>;
  server_time?: number;
  last_candle_epoch?: number;
  last_candle_age_seconds?: number | null;
  source?: string;
  span_days?: number;
  bar_count?: number;
}

export interface ForexHistoryStatus {
  symbol: string;
  ready_for_train: boolean;
  timeframes: Record<string, { bars: number; from_epoch: number | null; to_epoch: number | null; days: number }>;
  job?: { status: string; progress?: string; error?: string | null };
}

export interface ForexTrainStatus {
  status: string;
  progress?: string;
  error?: string | null;
  result?: {
    threshold?: number;
    metrics?: Record<string, number | null>;
    trained_at?: number;
    note?: string;
  } | null;
}

export interface ForexStatus {
  running: boolean;
  config: {
    duration_seconds: number;
    max_signals_per_day: number;
    cooldown_seconds: number;
    stake: number;
    timezone: string;
    symbols: string[];
    demo_auto_trade: boolean;
  };
  execution?: { allowed: boolean; reason: string; token_configured?: boolean; server_allowed?: boolean };
  daily_count: number;
  signals_remaining_today?: number;
  duration_hours?: number;
  product?: { mode: string; duration_seconds: number; daily_cap: number; timezone: string; contract: string };
  news?: { ready: boolean; reason: string; source?: string; setup_hint?: string };
  ml?: { ready: boolean; reason?: string; threshold?: number | null; metrics?: Record<string, unknown> | null };
  history?: ForexHistoryStatus;
  train?: ForexTrainStatus;
  last_scan_at?: string | null;
  last_error?: string | null;
  analyses: ForexAnalysis[];
  signals: ForexSignal[];
}

export const forexApi = {
  status: (signal?: AbortSignal) => api<ForexStatus>('/forex/status', { signal }),
  symbols: () => api<{ symbols: ForexSymbol[]; error?: string }>('/forex/symbols'),
  chart: (symbol: string, timeframe: string, count = 180) =>
    api<ForexChartPayload>(`/forex/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&count=${count}`),
  historyStatus: () => api<ForexHistoryStatus>('/forex/history/status'),
  historyDownload: () => api<ForexHistoryStatus>('/forex/history/download', { method: 'POST' }),
  trainStatus: () => api<ForexTrainStatus>('/forex/train/status'),
  train: () => api<ForexTrainStatus>('/forex/train', { method: 'POST' }),
  scan: () => api<ForexStatus>('/forex/scan', { method: 'POST' }),
  start: (config: { symbols: string[]; stake: number; duration_seconds: number; demo_auto_trade: boolean }) =>
    api<ForexStatus>('/forex/start', { method: 'POST', body: JSON.stringify(config) }),
  stop: () => api<ForexStatus>('/forex/stop', { method: 'POST' }),
  account: (config: { token?: string; app_id?: string; enabled: boolean }) =>
    api<ForexStatus>('/forex/account', { method: 'PUT', body: JSON.stringify(config) }),
};
