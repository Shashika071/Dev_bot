export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function wsUrl(path: string): string {
  const base = import.meta.env.VITE_WS_URL || API.replace(/^http/, 'ws');
  return `${base.replace(/\/$/, '')}${path}`;
}

export async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
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
  last_scan_at?: string | null;
  last_error?: string | null;
  analyses: ForexAnalysis[];
  signals: ForexSignal[];
}

export interface ForexSymbol { symbol: string; display_name: string; is_open: boolean }

export const forexApi = {
  status: (signal?: AbortSignal) => api<ForexStatus>('/forex/status', { signal }),
  symbols: () => api<{ symbols: ForexSymbol[]; error?: string }>('/forex/symbols'),
  scan: () => api<ForexStatus>('/forex/scan', { method: 'POST' }),
  start: (config: { symbols: string[]; stake: number; duration_seconds: number; demo_auto_trade: boolean }) =>
    api<ForexStatus>('/forex/start', { method: 'POST', body: JSON.stringify(config) }),
  stop: () => api<ForexStatus>('/forex/stop', { method: 'POST' }),
  account: (config: { token?: string; app_id?: string; enabled: boolean }) =>
    api<ForexStatus>('/forex/account', { method: 'PUT', body: JSON.stringify(config) }),
};
