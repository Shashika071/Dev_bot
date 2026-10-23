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

export const SIGNAL_HORIZON_SECONDS = 300;

export interface PriceBar { time: number; open: number; high: number; low: number; close: number }

export interface SignalPath {
  key: string;
  direction: 'RISE' | 'FALL';
  entry: number | null;
  maxMove: number | null;
  followed: boolean | null;
  complete: boolean;
}

/** How far price traveled in the signaled direction during the next 5 minutes. */
export function signalPaths(
  signals: ChartSignal[],
  bars: PriceBar[],
  now: number,
  step: number,
  live?: { epoch: number; price: number } | null,
): SignalPath[] {
  if (step <= 0) return [];
  return signals.flatMap(signal => {
    const direction = signal.direction === 'RISE' || signal.direction === 'FALL' ? signal.direction : null;
    const start = signal.decision_epoch ?? signal.created_epoch;
    if (!direction || start == null || !Number.isFinite(start) || start <= 0) return [];
    const entryOpen = start % step === 0 ? start - step : Math.floor(start / step) * step;
    const entryBar = bars.find(bar => bar.time === entryOpen);
    const entry = entryBar && Number.isFinite(entryBar.close) ? entryBar.close : null;
    const end = start + SIGNAL_HORIZON_SECONDS;
    const window = bars.filter(bar => bar.time >= start && bar.time < end && bar.time <= now);
    const liveInWindow = live && live.epoch >= start && live.epoch < end ? live.price : null;
    let maxMove: number | null = null;
    let followed: boolean | null = null;
    if (entry != null) {
      const highs = window.map(bar => bar.high);
      const lows = window.map(bar => bar.low);
      if (liveInWindow != null) {
        highs.push(liveInWindow);
        lows.push(liveInWindow);
      }
      const top = highs.length ? Math.max(entry, ...highs) : entry;
      const bottom = lows.length ? Math.min(entry, ...lows) : entry;
      maxMove = direction === 'RISE' ? top - entry : entry - bottom;
      if (now >= end) {
        const finished = [...window].reverse().find(bar => bar.time + step <= end) ?? window[window.length - 1];
        const endPrice = finished?.close ?? liveInWindow;
        followed = endPrice == null ? null : direction === 'RISE' ? endPrice > entry : endPrice < entry;
      }
    }
    return [{
      key: `${start}:${direction}`,
      direction,
      entry,
      maxMove,
      followed,
      complete: entry != null && now >= end,
    }];
  }).filter((path, index, all) => all.findIndex(item => item.key === path.key) === index);
}

export function pathLabel(path?: SignalPath | null): string {
  if (!path || path.entry == null || path.maxMove == null) return 'Signal candle is not on the 1-minute chart';
  const side = path.direction === 'RISE' ? 'Rise' : 'Fall';
  const far = path.maxMove.toFixed(2);
  if (!path.complete) return `Watching 5 minutes · farthest ${side} ${far}`;
  if (path.followed == null) return `5 minutes ended · farthest ${side} ${far}`;
  return `${path.followed ? 'Went' : 'Did not go'} ${side} · farthest ${side} ${far}`;
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
