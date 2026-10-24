import { afterEach, describe, expect, it, vi } from 'vitest';
import { appendTick, candleRefresh, chartCandles, chartSignalMarkers, freshTick, MAX_CHART_BARS, pollResource, reconcileCandles, signalPaths, signalWinRate } from './syntheticChartData';

const bar = (epoch: number, close = 10) => ({ epoch, open: 10, high: Math.max(11, close), low: 9, close });

describe('live chart data', () => {
  it('sorts, deduplicates, and rejects invalid broker candles', () => {
    const rows = chartCandles([bar(120), bar(60), bar(120, 12), bar(180, NaN), { ...bar(240), high: 1 }, bar(-1)]);
    expect(rows.map(row => row.time)).toEqual([60, 120]);
    expect(rows[1].close).toBe(12);
  });

  it('updates intra-candle OHLC, rolls to a new candle, and ignores an older bucket', () => {
    let rows = chartCandles([bar(120)]);
    rows = appendTick(rows, { epoch: 150, quote: 12 }, 60);
    rows = appendTick(rows, { epoch: 170, quote: 8 }, 60);
    expect(rows[0]).toMatchObject({ time: 120, open: 10, high: 12, low: 8, close: 8 });
    rows = appendTick(rows, { epoch: 180, quote: 13 }, 60);
    expect(rows[1]).toMatchObject({ time: 180, open: 13, high: 13, low: 13, close: 13 });
    expect(appendTick(rows, { epoch: 170, quote: 99 }, 60)).toBe(rows);
  });

  it('repairs missing closed candles and preserves a tick newer than the snapshot request', () => {
    let current = chartCandles([bar(60), bar(240)]);
    current = appendTick(current, { epoch: 270, quote: 13 }, 60);
    const result = reconcileCandles([bar(60, 9), bar(120), bar(180), bar(240, 11)], current, { epoch: 270, quote: 13 }, 60, 260);
    expect(result.map(row => row.time)).toEqual([60, 120, 180, 240]);
    expect(result[0].close).toBe(9);
    expect(result[3]).toMatchObject({ open: 10, high: 13, close: 13 });
  });

  it('does not let an old tick override a fresh snapshot', () => {
    const result = reconcileCandles([bar(120, 11)], chartCandles([bar(120, 15)]), { epoch: 150, quote: 15 }, 60, 160);
    expect(result[0].close).toBe(11);
  });

  it('keeps a new candle created while the snapshot was in flight', () => {
    const result = reconcileCandles([bar(120)], chartCandles([bar(120), bar(180, 12)]), { epoch: 181, quote: 12 }, 60, 179);
    expect(result.map(row => row.time)).toEqual([120, 180]);
    expect(result[1].close).toBe(12);
  });

  it('rebuilds the full chart from history after an empty/reset state and caps memory', () => {
    const history = Array.from({ length: MAX_CHART_BARS + 10 }, (_, index) => bar((index + 1) * 60));
    const rows = reconcileCandles(history, [], null, 60, 0);
    expect(rows).toHaveLength(MAX_CHART_BARS);
    expect(appendTick(rows, { epoch: (MAX_CHART_BARS + 11) * 60, quote: 11 }, 60)).toHaveLength(MAX_CHART_BARS);
  });

  it('patches the live candle on a history poll and reloads only when the series breaks', () => {
    const drawn = chartCandles([bar(60), bar(120), bar(180)]);
    expect(candleRefresh(drawn, chartCandles([bar(60), bar(120), bar(180, 12)]))).toEqual({ mode: 'update', from: 2 });
    expect(candleRefresh(drawn, chartCandles([bar(120), bar(180), bar(240)]))).toEqual({ mode: 'update', from: 1 });
    expect(candleRefresh([], chartCandles([bar(60)]))).toEqual({ mode: 'replace', from: 0 });
    expect(candleRefresh(drawn, chartCandles([bar(60)]))).toEqual({ mode: 'replace', from: 0 });
  });

  it('rejects stale, non-finite, and out-of-order ticks without moving the chart backwards', () => {
    const previous = { epoch: 150, quote: 10 };
    expect(freshTick({ epoch: 151, quote: 11, age_seconds: 1 }, previous)).toEqual({ epoch: 151, quote: 11 });
    for (const reading of [
      { epoch: 140, quote: 11, age_seconds: 1 },
      { epoch: 151, quote: Infinity, age_seconds: 1 },
      { epoch: 151, quote: 11, age_seconds: 9 },
      { epoch: 151, quote: 11, age_seconds: 1, stale: true },
      { epoch: 151, quote: 11, age_seconds: null },
    ]) expect(freshTick(reading, previous)).toBeNull();
  });
});

describe('chart polling lifecycle', () => {
  afterEach(() => vi.useRealTimers());

  it('aborts a hung request, retries, and never overlaps unresolved network requests without aborting', async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const success = vi.fn();
    const failure = vi.fn();
    const request = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? new Promise<number>(() => undefined) : Promise.resolve(42);
    });
    const stop = pollResource({ request, success, failure, intervalMs: 500, timeoutMs: 1000 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(signals[0].aborted).toBe(true);
    expect(failure).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(500);
    expect(success).toHaveBeenCalledWith(42);
    stop();
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('cancels requests on unmount/timeframe change and suppresses late responses', async () => {
    vi.useFakeTimers();
    const success = vi.fn();
    const failure = vi.fn();
    let resolve!: (value: number) => void;
    let signal!: AbortSignal;
    const stop = pollResource({
      request: next => { signal = next; return new Promise<number>(done => { resolve = done; }); },
      success, failure, intervalMs: 500, timeoutMs: 1000,
    });
    stop();
    expect(signal.aborted).toBe(true);
    resolve(42);
    await vi.advanceTimersByTimeAsync(5000);
    expect(success).not.toHaveBeenCalled();
    expect(failure).not.toHaveBeenCalled();
  });
});

describe('generated signal markers', () => {
  it('snaps a Rise or Fall onto the candle that contains the decision and skips other rows', () => {
    const marks = chartSignalMarkers([
      { direction: 'RISE', decision_epoch: 130 },
      { direction: 'NO TRADE', decision_epoch: 180 },
      { direction: 'FALL', decision_epoch: 400 },
      { direction: 'FALL', decision_epoch: 190 },
      { direction: 'RISE', decision_epoch: 200 },
    ], [60, 120, 180], 60);
    expect(marks).toEqual([
      { time: 120, direction: 'RISE' },
      { time: 180, direction: 'RISE' },
    ]);
  });
});

describe('five-minute signal path', () => {
  const bars = [
    { time: 60, open: 10, high: 10.2, low: 9.8, close: 10 },
    { time: 120, open: 10, high: 10.4, low: 9.7, close: 10.1 },
    { time: 180, open: 10.1, high: 10.8, low: 10, close: 10.2 },
    { time: 240, open: 10.2, high: 10.3, low: 9.5, close: 9.6 },
    { time: 300, open: 9.6, high: 9.9, low: 9.4, close: 9.7 },
    { time: 360, open: 9.7, high: 9.8, low: 9.6, close: 9.75 },
  ];

  it('reports the farthest move in the signaled direction before the 5 minutes finish', () => {
    const [path] = signalPaths([{ direction: 'RISE', decision_epoch: 120 }], bars, 150, 60, { epoch: 140, price: 11 });
    expect(path.entry).toBe(10);
    expect(path.maxMove).toBeCloseTo(1);
    expect(path.followed).toBeNull();
    expect(path.complete).toBe(false);
  });

  it('says whether the 5-minute price finished in the told direction', () => {
    const [rise] = signalPaths([{ direction: 'RISE', decision_epoch: 120 }], bars, 420, 60);
    expect(rise.complete).toBe(true);
    expect(rise.followed).toBe(false);
    expect(rise.maxMove).toBeCloseTo(0.8);
    const [fall] = signalPaths([{ direction: 'FALL', decision_epoch: 120 }], bars, 420, 60);
    expect(fall.followed).toBe(true);
    expect(fall.maxMove).toBeCloseTo(0.6);
  });

  it('counts the winning percentage from finished signals only', () => {
    const finished = signalPaths([
      { direction: 'RISE', decision_epoch: 120 },
      { direction: 'FALL', decision_epoch: 120 },
    ], bars, 420, 60);
    const watching = signalPaths([{ direction: 'RISE', decision_epoch: 120 }], bars, 150, 60);
    expect(signalWinRate([...finished, ...watching])).toEqual({ wins: 1, losses: 1, rate: 0.5 });
    expect(signalWinRate(watching)).toEqual({ wins: 0, losses: 0, rate: null });
  });
});
