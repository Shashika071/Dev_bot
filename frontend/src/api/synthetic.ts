import { API } from './client';

export type Direction = 'RISE' | 'FALL' | 'NO TRADE' | 'WAIT' | 'NEUTRAL';
export interface IndicatorState {
  mid?: number; upper?: number; lower?: number; line?: number; range?: number;
  direction?: string; cross?: string | boolean; signal_age?: number; alpha?: number;
}
export interface Prediction {
  available?: boolean; promoted?: boolean; compatible?: boolean;
  direction?: string; p_rise?: number; p_fall?: number; p_tie?: number;
  confidence?: number; threshold?: number; hybrid_passed?: boolean; reason?: string;
  probability_note?: string;
}
export interface ModelMetrics {
  accuracy?: number; auc?: number; brier?: number; selected?: number;
  selected_accuracy?: number; coverage?: number; selected_wilson_lower?: number;
  payout_sensitivity?: Array<{ net_payout: number; break_even_accuracy: number; expected_return_per_stake: number }>;
}
export interface TrainingReport {
  symbol?: string; trained_at?: string; promoted?: boolean; accepted?: boolean;
  acceptance_reasons?: string[]; threshold?: number; probability_note?: string; label_kind?: string;
  dataset?: { samples?: number; ties?: number; decision_start?: number; decision_end?: number };
  walk_forward?: Array<{ fold: number; train_samples?: number; validation_samples?: number; catboost?: ModelMetrics; logistic?: ModelMetrics }>;
  final_test?: { samples?: number; decision_start?: number; catboost?: ModelMetrics; logistic?: ModelMetrics };
}
export interface JobState {
  status?: string; progress?: number; error?: string | null; message?: string;
  result?: TrainingReport; [key: string]: unknown;
}
export interface SyntheticAnalysis {
  symbol: string; direction: Direction; eligible?: boolean; blockers?: string[];
  decision_epoch?: number; indicators?: { frama?: IndicatorState; range_filter?: IndicatorState };
  timeframes?: Record<string, {
    trend?: string; structure?: string; close?: number; ema20?: number; ema50?: number;
    rsi?: number; macd_hist?: number; atr?: number; closed_at?: number; bars?: number;
  }>;
  factors?: Array<{ id: string | number; name: string; status: string; detail: unknown }>;
  ml?: Prediction;
}
export interface SyntheticSignal {
  id?: string; symbol?: string; direction?: string; created_at?: string;
  created_epoch?: number; decision_epoch?: number; expiry_epoch?: number;
  execution_status?: string; execution_result?: Record<string, unknown> | null;
  result?: string; [key: string]: unknown;
}
export interface SyntheticStatus {
  running: boolean;
  config?: { symbols?: string[]; duration_seconds?: number; stake?: number; max_signals_per_day?: number; demo_auto_trade?: boolean };
  analyses?: SyntheticAnalysis[]; signals?: SyntheticSignal[];
  execution?: { allowed?: boolean; reason?: string; [key: string]: unknown };
  history?: {
    symbol?: string; ready_for_train?: boolean; job?: JobState;
    timeframes?: Record<string, { bars?: number; days?: number; from_epoch?: number; to_epoch?: number; gaps?: number }>;
  };
  train?: JobState;
  ml?: {
    available?: boolean; promoted?: boolean; compatible?: boolean; ready?: boolean;
    state?: string; reason?: string; threshold?: number; trained_at?: string;
    report?: TrainingReport; metadata?: Record<string, unknown>;
  };
  [key: string]: unknown;
}
export interface SyntheticChartPayload {
  symbol: string; timeframe: string;
  candles: Array<{ epoch: number; open: number; high: number; low: number; close: number }>;
  overlays?: Partial<Record<'frama' | 'frama_upper' | 'frama_lower' | 'range_filter', Array<{ epoch: number; value: number }>>> & {
    signals?: Array<{ epoch: number; side: 'BUY' | 'SELL'; source: 'FRAMA' | 'Range Filter' }>;
  };
  trend_system?: {
    signals?: Array<{ epoch: number; side: 'BUY' | 'SELL' }>;
    dashboard?: { trend?: string; roc?: string; tsi?: string; mode?: string };
    attribution?: string;
  };
  forming?: boolean;
  granularity?: number;
  last_candle_age_seconds?: number;
  error?: string;
}

const controlToken = (import.meta.env.VITE_FOREX_CONTROL_TOKEN || '').trim();

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const response = await fetch(`${API.replace(/\/$/, '')}/synthetic${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(controlToken ? { 'X-Forex-Control-Token': controlToken } : {}),
      ...opts?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail
      : Array.isArray(body.detail) ? body.detail.map((entry: { msg?: string }) => entry.msg || 'Invalid request').join('; ')
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return response.json();
}

const post = <T,>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const syntheticApi = {
  status: (signal?: AbortSignal) => request<SyntheticStatus>('/status', { signal }),
  chart: (timeframe: string, signal?: AbortSignal) =>
    request<SyntheticChartPayload>(`/chart?symbol=R_100&timeframe=${encodeURIComponent(timeframe)}&count=1000`, { signal }),
  quote: (signal?: AbortSignal) => request<{ epoch?: number | null; quote?: number | null; age_seconds?: number | null }>('/quote', { signal }),
  scan: () => post<SyntheticStatus>('/scan'),
  start: (body: { stake: number; max_signals_per_day: number; demo_auto_trade: boolean }) =>
    post<SyntheticStatus>('/start', body),
  stop: () => post<SyntheticStatus>('/stop'),
  account: (body: { token?: string; app_id?: string; enabled: boolean }) => post<unknown>('/account', body),
  download: (days: number) => post<unknown>('/history/download', { days }),
  train: () => post<unknown>('/train'),
};
