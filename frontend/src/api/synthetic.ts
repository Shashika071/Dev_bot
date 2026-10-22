import { API } from './client';

export type Direction = 'RISE' | 'FALL' | 'NO TRADE' | 'WAIT' | 'NEUTRAL';
export interface IndicatorState {
  mid?: number; upper?: number; lower?: number; line?: number; range?: number;
  direction?: string; cross?: string | boolean; signal_age?: number; alpha?: number;
}
export interface Prediction {
  available?: boolean; promoted?: boolean; compatible?: boolean;
  direction?: string; p_rise?: number | null; p_fall?: number | null; p_tie?: number | null;
  confidence?: number | null; combined_confidence?: number | null;
  checks?: Array<{ id: string; name: string; side: string; status: string; detail: string }>;
  threshold?: number | null; hybrid_passed?: boolean; reason?: string;
  inference_available?: boolean; display_only?: boolean; decision_epoch?: number | null;
  probability_note?: string;
}
export interface ModelMetrics {
  accuracy?: number; auc?: number; brier?: number; selected?: number;
  selected_accuracy?: number; coverage?: number; selected_wilson_lower?: number;
  selection_diagnostics?: {
    confidence_passed?: number; rule_eligible?: number; confidence_and_rules?: number;
    direction_conflicts?: number; selected?: number; below_threshold?: number;
    technical_blocked?: number; confidence_min?: number; confidence_median?: number;
    confidence_p95?: number; confidence_max?: number; reason?: string;
  };
  payout_sensitivity?: Array<{ net_payout: number; break_even_accuracy: number; expected_return_per_stake: number }>;
}
export interface TrainingReport {
  symbol?: string; trained_at?: string | number; promoted?: boolean; accepted?: boolean;
  acceptance_reasons?: string[]; threshold?: number; probability_note?: string; label_kind?: string;
  dataset?: { samples?: number; ties?: number; decision_start?: number; decision_end?: number };
  walk_forward?: Array<{ fold: number; train_samples?: number; validation_samples?: number; catboost?: ModelMetrics; logistic?: ModelMetrics }>;
  final_test?: { samples?: number; decision_start?: number; catboost?: ModelMetrics; logistic?: ModelMetrics };
}
export interface JobState {
  status?: string; progress?: number | string; error?: string | null; message?: string;
  result?: TrainingReport; [key: string]: unknown;
}
export interface SyntheticAnalysis {
  symbol: string; direction: Direction; eligible?: boolean; blockers?: string[];
  decision_epoch?: number; indicators?: { frama?: IndicatorState; range_filter?: IndicatorState };
  timeframes?: Record<string, {
    trend?: string; structure?: string; close?: number; ema20?: number; ema50?: number;
    rsi?: number; macd_hist?: number; atr?: number; closed_at?: number; bars?: number;
  }>;
  factors?: Array<{ id: string | number; name: string; status: string; detail: unknown; side?: string }>;
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
  confidence?: SyntheticConfidence;
  ml?: {
    available?: boolean; promoted?: boolean; compatible?: boolean; ready?: boolean;
    state?: string; reason?: string; threshold?: number; trained_at?: string | number;
    report?: TrainingReport; metadata?: Record<string, unknown>;
  };
  research?: ResearchStatus;
  [key: string]: unknown;
}
export interface ResearchModelRow {
  name?: string; role?: string; votes?: boolean; validation_log_loss?: number | null;
  test_log_loss?: number | null; test_auc?: number | null; test_brier?: number | null;
  research_log_loss?: number | null; error?: string | null; note?: string | null;
}
export interface ResearchBacktest {
  source?: string; signals?: number; wins?: number; losses?: number;
  win_rate?: number | null; expected_profit_per_stake?: number | null;
  max_drawdown?: number | null; payout?: number | null; note?: string;
}
export interface ResearchPaper {
  source?: string; recorded?: number; rejections?: number; resolved?: number;
  open?: number; wins?: number; losses?: number; profit?: number | null;
  max_drawdown?: number | null; consecutive_losses?: number; next_stake?: number;
}
export interface ResearchStatus {
  market?: string; contract?: string; duration_seconds?: number;
  direction?: string; final_signal?: string; candidate_direction?: string | null;
  raw_win_probability?: number | null; calibrated_win_probability?: number | null; market_regime?: string; volatility?: string;
  expected_value?: number | null; model_status?: string; approval_state?: string;
  approval_reasons?: string[]; reason?: string; places_trades?: boolean; stale?: boolean;
  selected_model?: string | null; models?: ResearchModelRow[]; backtest?: ResearchBacktest | null;
  paper?: ResearchPaper; limitations?: string[]; notes?: string[];
  dataset?: { rows?: number; ties?: number; tie_rate?: number | null } | null;
  job?: JobState; payout?: number | null; data_quality?: { bars?: number; gaps?: number; blocks_signal?: boolean; reason?: string } | null;
  optional_research?: {
    sequence?: { used_for_signal?: boolean; reason?: string; results?: Record<string, { test_log_loss?: number; test_rows?: number }> | null };
    hmm?: { state?: string | number | null; reason?: string };
    structures?: Array<{ name: string; samples: number; rise_rate?: number | null; wilson_lower?: number | null; reason?: string }>;
    hyperparameters?: Record<string, Record<string, number>>;
  };
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
  snapshot_epoch?: number;
  server_time?: number;
  granularity?: number;
  last_candle_age_seconds?: number;
  error?: string;
}
export interface SyntheticConfidence {
  symbol?: string;
  direction?: string;
  p_rise?: number | null;
  p_fall?: number | null;
  p_tie?: number | null;
  confidence?: number | null;
  combined_p_rise?: number | null;
  combined_p_fall?: number | null;
  combined_confidence?: number | null;
  checks?: Array<{ id: string; name: string; side: string; status: string; detail: string }>;
  threshold?: number | null;
  decision_epoch?: number | null;
  promoted?: boolean;
  reason?: string;
  updated_at?: number | null;
  status?: string;
  stale?: boolean;
  age_seconds?: number | null;
  error?: string | null;
  inference_available?: boolean;
  display_only?: boolean;
  probability_note?: string;
}

const controlToken = (import.meta.env.VITE_FOREX_CONTROL_TOKEN || '').trim();

async function request<T>(path: string, opts?: RequestInit, timeoutMs = 20000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  opts?.signal?.addEventListener('abort', abort, { once: true });
  if (opts?.signal?.aborted) controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(`${API.replace(/\/$/, '')}/synthetic${path}`, {
    ...opts,
    signal: controller.signal,
    cache: 'no-store',
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
    return await response.json();
  } catch (error) {
    if (timedOut) throw new Error('Request timed out. Refresh status before retrying an action; it may already have reached the server.');
    throw error;
  } finally {
    clearTimeout(timer);
    opts?.signal?.removeEventListener('abort', abort);
  }
}

const post = <T,>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) }, path === '/scan' ? 120000 : 45000);

export const syntheticApi = {
  status: (signal?: AbortSignal) => request<SyntheticStatus>('/status', { signal }),
  chart: (timeframe: string, signal?: AbortSignal) =>
    request<SyntheticChartPayload>(`/chart?symbol=R_100&timeframe=${encodeURIComponent(timeframe)}&count=1000`, { signal }, 50000),
  quote: (signal?: AbortSignal) => request<{ epoch?: number | null; quote?: number | null; age_seconds?: number | null; status?: string; stale?: boolean; error?: string | null; updated_at?: number | null; server_time?: number }>('/quote', { signal }),
  confidence: (signal?: AbortSignal) => request<SyntheticConfidence>('/confidence', { signal }),
  scan: () => post<SyntheticStatus>('/scan'),
  start: (body: { stake: number; max_signals_per_day: number; demo_auto_trade: boolean }) =>
    post<SyntheticStatus>('/start', body),
  stop: () => post<SyntheticStatus>('/stop'),
  account: (body: { token?: string; app_id?: string; enabled: boolean }) =>
    request<SyntheticStatus>('/account', { method: 'PUT', body: JSON.stringify(body) }, 45000),
  download: (days: number) => post<unknown>('/history/download', { days }),
  train: () => post<unknown>('/train'),
  evaluateResearch: (body: { duration_seconds: number; payout: number | null }) =>
    request<ResearchStatus>('/research/evaluate', { method: 'POST', body: JSON.stringify(body) }, 60000),
  scoreResearch: (body: { payout: number | null }) =>
    request<ResearchStatus>('/research/score', { method: 'POST', body: JSON.stringify(body) }, 60000),
  recordPaper: () => request<ResearchStatus>('/research/paper', { method: 'POST', body: '{}' }, 60000),
  downloadTicks: (hours: number) =>
    request<ResearchStatus>('/research/ticks', { method: 'POST', body: JSON.stringify({ hours }) }, 60000),
};
