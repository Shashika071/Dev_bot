export type OverlayKey = 'ema' | 'bands' | 'levels' | 'patterns' | 'signals';

export const defaultOverlays: Record<OverlayKey, boolean> = {
  ema: false,
  bands: false,
  levels: false,
  patterns: false,
  signals: false,
};

export type CandleBar = { epoch: number; open: number; high: number; low: number; close: number };

export function mergeCandles(older: CandleBar[], recent: CandleBar[], cap = 6000): CandleBar[] {
  const byEpoch = new Map<number, CandleBar>();
  for (const bar of older) byEpoch.set(bar.epoch, bar);
  for (const bar of recent) byEpoch.set(bar.epoch, bar);
  return [...byEpoch.values()].sort((a, b) => a.epoch - b.epoch).slice(-cap);
}

export type LiveSignal = {
  signal_id: string;
  symbol?: string;
  epoch: number;
  direction: 'upper' | 'lower';
  probability: number | null;
  resolved_barrier: number | null;
  source?: 'accuracy';
  resolved?: boolean;
  outcome?: boolean | null;
  expiry_epoch?: number | null;
  barrier_input?: string;
  feature_profile?: string;
  feature_groups?: string[];
};

function optionalNumber(value: unknown): number | null {
  if (value == null || value === '' || typeof value === 'boolean') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function asLiveSignal(row: Record<string, unknown>): LiveSignal | null {
  const direction = String(row.direction || row.touch_direction || '');
  if (direction !== 'upper' && direction !== 'lower') return null;
  const fromEpoch = Number(row.epoch);
  const fromCreated = typeof row.created_at === 'string' ? Math.floor(Date.parse(row.created_at) / 1000) : Number.NaN;
  const fromQuote = typeof row.quote_timestamp === 'string' ? Math.floor(Date.parse(row.quote_timestamp) / 1000) : Number.NaN;
  const epoch = Number.isFinite(fromEpoch) && fromEpoch > 0
    ? fromEpoch
    : Number.isFinite(fromCreated) ? fromCreated : fromQuote;
  if (!Number.isFinite(epoch) || epoch <= 0) return null;
  const probability = optionalNumber(row.calibrated_probability ?? row.probability);
  const barrier = optionalNumber(row.resolved_barrier);
  return {
    signal_id: String(row.signal_id || `${epoch}-${direction}`),
    symbol: row.symbol ? String(row.symbol) : undefined,
    epoch,
    direction,
    probability: probability !== null && probability >= 0 && probability <= 1 ? probability : null,
    resolved_barrier: barrier,
  };
}

/** Accuracy signals use their decision time, not the later alert delivery time. */
export function asAccuracySignal(row: Record<string, unknown>): LiveSignal | null {
  const signal = asLiveSignal({
    ...row,
    epoch: row.decision_epoch,
    calibrated_probability: row.estimated_probability,
    // The reference price is not an accepted contract entry or absolute barrier.
    resolved_barrier: null,
  });
  if (!signal) return null;
  return {
    ...signal,
    source: 'accuracy',
    resolved: row.resolved === true,
    outcome: typeof row.outcome === 'boolean' ? row.outcome : null,
    expiry_epoch: optionalNumber(row.expiry_epoch),
    barrier_input: typeof row.barrier_input === 'string' ? row.barrier_input : undefined,
    feature_profile: typeof row.feature_profile === 'string' ? row.feature_profile : undefined,
    feature_groups: Array.isArray(row.feature_groups)
      ? row.feature_groups.filter((group): group is string => typeof group === 'string')
      : undefined,
  };
}

export function accuracyProfileLabel(profile?: string): string {
  if (!profile || profile === 'compact_v1') return 'Compact profile';
  if (profile === 'technical_v2') return 'Technical analysis profile';
  return profile.replace(/_/g, ' ');
}

export function accuracyFeatureGroupLabel(group: string): string {
  const labels: Record<string, string> = {
    trend: 'Market trend',
    structure: 'Market structure',
    multi_timeframe: 'Multi-timeframe',
    support_resistance: 'Support/resistance',
    sweep_proxies: 'Price sweeps',
    breakouts_retests: 'Breakouts/retests',
    candlestick: 'Candlestick patterns',
    indicators: 'Technical indicators',
    momentum_confirmation: 'Momentum/confirmation',
  };
  return labels[group] || group.replace(/_/g, ' ');
}

/** A slower history response must not erase a new alert or roll back its outcome. */
export function mergeLiveSignals(current: LiveSignal[], incoming: LiveSignal[], cap = 100): LiveSignal[] {
  const byId = new Map(current.map((signal) => [signal.signal_id, signal]));
  incoming.forEach((signal) => {
    const prior = byId.get(signal.signal_id);
    byId.set(signal.signal_id, prior?.resolved && !signal.resolved ? prior : signal);
  });
  return [...byId.values()].sort((a, b) => b.epoch - a.epoch).slice(0, cap);
}

export function signalOutcomeLabel(signal: LiveSignal): string {
  if (!signal.resolved) return 'Pending';
  return signal.outcome === true ? 'Touched' : signal.outcome === false ? 'Missed' : 'Unresolved data';
}

/** Coarse candles show the latest signal and a count of others on that candle. */
export function liveSignalMarkers(signals: LiveSignal[], times: number[]) {
  const orderedTimes = [...new Set(times.filter(Number.isFinite))].sort((a, b) => a - b);
  if (!orderedTimes.length) return [];
  const groups = new Map<number, LiveSignal[]>();
  [...signals].sort((a, b) => b.epoch - a.epoch).forEach((signal) => {
    if (signal.epoch < orderedTimes[0]) return;
    const epoch = snapToBar(signal.epoch, orderedTimes);
    groups.set(epoch, [...(groups.get(epoch) || []), signal]);
  });
  return [...groups.entries()].map(([epoch, group]) => {
    const signal = group.find((item) => item.source === 'accuracy') || group[0];
    const probability = signal.probability == null ? '' : ` ${(signal.probability * 100).toFixed(1)}%`;
    const prefix = signal.source === 'accuracy' ? 'TOUCH ' : '';
    const outcome = signal.source === 'accuracy' ? ` · ${signalOutcomeLabel(signal)}` : '';
    const others = group.length > 1 ? ` · +${group.length - 1} more` : '';
    return { epoch, direction: signal.direction, text: `${prefix}${signal.direction.toUpperCase()}${probability}${outcome}${others}` };
  });
}

export function snapToBar(epoch: number, times: number[]): number {
  if (!times.length) return epoch;
  let best = times[0];
  for (const time of times) {
    if (time <= epoch) best = time;
    else break;
  }
  return epoch >= times[times.length - 1] ? times[times.length - 1] : best;
}

export type OverlayPoint = { epoch: number; value: number };

export type ChartSegment = {
  id: string;
  time1: number;
  price1: number;
  time2: number;
  price2: number;
  color: string;
};

/** EMA with the same seed as pandas ewm(span, adjust=False, min_periods=period). */
export function emaSeries(candles: CandleBar[], period: number): OverlayPoint[] {
  if (period < 1 || candles.length < period) return [];
  const alpha = 2 / (period + 1);
  let value = candles[0].close;
  const out: OverlayPoint[] = [];
  for (let index = 0; index < candles.length; index += 1) {
    const close = candles[index].close;
    value = index === 0 ? close : close * alpha + value * (1 - alpha);
    if (index >= period - 1) out.push({ epoch: candles[index].epoch, value });
  }
  return out;
}

/** Bollinger bands. Sample standard deviation, matching the analysis snapshot. */
export function bandSeries(candles: CandleBar[], period = 20, mult = 2): { upper: OverlayPoint[]; lower: OverlayPoint[] } {
  const upper: OverlayPoint[] = [];
  const lower: OverlayPoint[] = [];
  if (period < 2 || candles.length < period) return { upper, lower };
  for (let index = period - 1; index < candles.length; index += 1) {
    let sum = 0;
    for (let cursor = index - period + 1; cursor <= index; cursor += 1) sum += candles[cursor].close;
    const mean = sum / period;
    let square = 0;
    for (let cursor = index - period + 1; cursor <= index; cursor += 1) {
      const delta = candles[cursor].close - mean;
      square += delta * delta;
    }
    const deviation = Math.sqrt(square / (period - 1));
    upper.push({ epoch: candles[index].epoch, value: mean + mult * deviation });
    lower.push({ epoch: candles[index].epoch, value: mean - mult * deviation });
  }
  return { upper, lower };
}

export function indicatorOverlay(candles: CandleBar[]) {
  const bands = bandSeries(candles);
  return {
    ema8: emaSeries(candles, 8),
    ema20: emaSeries(candles, 20),
    ema50: emaSeries(candles, 50),
    upper: bands.upper,
    lower: bands.lower,
  };
}

/** Keep a segment's slope and run it from the first loaded candle to the last. */
export function coverChart<T extends ChartSegment>(segment: T, start: number, end: number): T {
  const span = segment.time2 - segment.time1;
  if (!Number.isFinite(span) || span === 0 || !(end > start)) return segment;
  const slope = (segment.price2 - segment.price1) / span;
  return {
    ...segment,
    time1: start,
    price1: segment.price1 + slope * (start - segment.time1),
    time2: end,
    price2: segment.price1 + slope * (end - segment.time1),
  };
}

export function toggleOverlay(
  current: Record<OverlayKey, boolean>,
  key: OverlayKey,
): Record<OverlayKey, boolean> {
  return { ...current, [key]: !current[key] };
}
