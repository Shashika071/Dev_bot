import type { CandlestickData } from 'lightweight-charts';

export interface CandleRow { epoch: number; open: number; high: number; low: number; close: number }
export interface Tick { epoch: number; quote: number }
export interface QuoteReading {
  epoch?: number | null; quote?: number | null; age_seconds?: number | null;
  stale?: boolean; status?: string; error?: string | null;
}
export const MAX_CHART_BARS = 1000;

export function chartCandles(rows: CandleRow[]): CandlestickData[] {
  const unique = new Map<number, CandlestickData>();
  for (const row of rows) {
    if (![row.epoch, row.open, row.high, row.low, row.close].every(Number.isFinite)
      || row.epoch <= 0 || row.low <= 0 || row.high < Math.max(row.open, row.close, row.low)
      || row.low > Math.min(row.open, row.close)) continue;
    const epoch = Math.floor(row.epoch);
    unique.set(epoch, { time: epoch as CandlestickData['time'], open: row.open, high: row.high, low: row.low, close: row.close });
  }
  return [...unique.entries()].sort(([a], [b]) => a - b).map(([, row]) => row).slice(-MAX_CHART_BARS);
}

export function freshTick(reading: QuoteReading, previous: Tick | null): Tick | null {
  const { epoch, quote, age_seconds: age } = reading;
  if (epoch == null || quote == null || age == null || ![epoch, quote, age].every(Number.isFinite)
    || epoch <= 0 || quote <= 0 || age < 0 || age > 8 || reading.stale
    || (previous && epoch < previous.epoch)) return null;
  return { epoch: Math.floor(epoch), quote };
}

/** Only the active bar changes on a tick; gaps are repaired by the next history snapshot. */
export function appendTick(bars: CandlestickData[], tick: Tick, step: number): CandlestickData[] {
  const bucket = Math.floor(tick.epoch / step) * step;
  const last = bars[bars.length - 1];
  if (!last || bucket < Number(last.time)) return bars;
  const next = bucket === Number(last.time)
    ? { ...last, high: Math.max(last.high, tick.quote), low: Math.min(last.low, tick.quote), close: tick.quote }
    : { time: bucket as CandlestickData['time'], open: tick.quote, high: tick.quote, low: tick.quote, close: tick.quote };
  return (bucket === Number(last.time) ? [...bars.slice(0, -1), next] : [...bars, next]).slice(-MAX_CHART_BARS);
}

/**
 * A routine history poll only needs to patch the live tail.
 * Replacing the whole series resets the viewport and looks like skipped ticks.
 */
export function candleRefresh(previous: { time: unknown }[], next: { time: unknown }[]): { mode: 'replace' | 'update'; from: number } {
  if (!previous.length || !next.length) return { mode: 'replace', from: 0 };
  const prevLast = Number(previous[previous.length - 1].time);
  const nextLast = Number(next[next.length - 1].time);
  const nextFirst = Number(next[0].time);
  const prevFirst = Number(previous[0].time);
  if (!Number.isFinite(prevLast) || !Number.isFinite(nextLast) || nextLast < prevLast || nextFirst < prevFirst) {
    return { mode: 'replace', from: 0 };
  }
  const anchor = next.findIndex(row => Number(row.time) === prevLast);
  if (anchor < 0 || next.length - anchor > 4) return { mode: 'replace', from: 0 };
  return { mode: 'update', from: anchor };
}

/** Snapshots repair all closed bars, while ticks received during the request keep the live bar current. */
export function reconcileCandles(
  incoming: CandleRow[], current: CandlestickData[], latest: Tick | null, step: number, snapshotEpoch: number,
): CandlestickData[] {
  const rows = chartCandles(incoming);
  if (!rows.length) return current;
  if (!latest || latest.epoch < snapshotEpoch) return rows;
  const liveBucket = Math.floor(latest.epoch / step) * step;
  const latestSnapshot = Number(rows[rows.length - 1].time);
  const retained = current.filter(row => Number(row.time) > latestSnapshot);
  const liveBar = current.find(row => Number(row.time) === liveBucket);
  const merged = [...rows, ...retained];
  const index = merged.findIndex(row => Number(row.time) === liveBucket);
  if (liveBar && index >= 0) {
    merged[index] = { ...merged[index], high: Math.max(merged[index].high, liveBar.high), low: Math.min(merged[index].low, liveBar.low) };
  }
  return appendTick(merged, latest, step);
}

export interface ChartSignal {
  direction?: string;
  decision_epoch?: number;
  created_epoch?: number;
}

/** Place a generated Rise or Fall on the candle that contains its decision time. */
export function chartSignalMarkers(signals: ChartSignal[], candleTimes: number[], step: number): Array<{ time: number; direction: 'RISE' | 'FALL' }> {
  const available = new Set(candleTimes.filter(time => Number.isFinite(time)));
  const placed = new Map<number, { time: number; direction: 'RISE' | 'FALL'; order: number }>();
  signals.forEach((signal, order) => {
    const direction = signal.direction === 'RISE' || signal.direction === 'FALL' ? signal.direction : null;
    const epoch = signal.decision_epoch ?? signal.created_epoch;
    if (!direction || epoch == null || !Number.isFinite(epoch) || epoch <= 0 || step <= 0) return;
    const time = Math.floor(epoch / step) * step;
    if (!available.has(time)) return;
    const previous = placed.get(time);
    if (!previous || order >= previous.order) placed.set(time, { time, direction, order });
  });
  return [...placed.values()].sort((a, b) => a.time - b.time).map(({ time, direction }) => ({ time, direction }));
}

/** Bounded requests recover from a hung network and never publish results after cleanup. */
export function pollResource<T>({ request, success, failure, busy, intervalMs, timeoutMs }: {
  request: (signal: AbortSignal) => Promise<T>;
  success: (value: T) => void;
  failure: (error: unknown) => void;
  busy?: (value: boolean) => void;
  intervalMs: number;
  timeoutMs: number;
}): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  async function run() {
    controller = new AbortController();
    const active = controller;
    busy?.(true);
    try {
      const value = await Promise.race([
        request(active.signal),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => {
            active.abort();
            reject(new Error('Request timed out; reconnecting.'));
          }, timeoutMs);
        }),
      ]);
      if (!stopped) success(value);
    } catch (error) {
      if (!stopped) failure(error);
    } finally {
      clearTimeout(deadline);
      if (!stopped) {
        busy?.(false);
        timer = setTimeout(() => void run(), intervalMs);
      }
    }
  }
  void run();
  return () => { stopped = true; clearTimeout(timer); clearTimeout(deadline); controller?.abort(); };
}
