import { describe, expect, it } from 'vitest';
import { coverChart, defaultOverlays, asLiveSignal, emaSeries, indicatorOverlay, mergeCandles, snapToBar, toggleOverlay } from './state';

describe('chart overlay state', () => {
  it('toggles one group without clearing the others', () => {
    const next = toggleOverlay(defaultOverlays, 'patterns');
    expect(next.patterns).toBe(true);
    expect(next.ema).toBe(false);
    expect(next.signals).toBe(false);
  });

  it('keeps older candles and lets the live bar replace the same epoch', () => {
    const merged = mergeCandles(
      [{ epoch: 60, open: 1, high: 1, low: 1, close: 1 }, { epoch: 120, open: 1, high: 1, low: 1, close: 1 }],
      [{ epoch: 120, open: 1, high: 3, low: 1, close: 2 }, { epoch: 180, open: 2, high: 2, low: 2, close: 2 }],
    );
    expect(merged.map((bar) => bar.epoch)).toEqual([60, 120, 180]);
    expect(merged[1].close).toBe(2);
  });

  it('places a cross-barrier signal on the candle that was open', () => {
    const signal = asLiveSignal({
      signal_id: 'SIG-1',
      symbol: 'R_100',
      direction: 'upper',
      calibrated_probability: 0.82,
      epoch: 175,
      resolved_barrier: 660,
    });
    expect(signal?.direction).toBe('upper');
    expect(signal?.probability).toBe(0.82);
    expect(snapToBar(175, [60, 120, 180])).toBe(120);
    expect(snapToBar(400, [60, 120, 180])).toBe(180);
  });

  it('draws moving averages across every loaded candle', () => {
    const candles = Array.from({ length: 80 }, (_, index) => ({
      epoch: (index + 1) * 60,
      open: 100,
      high: 101,
      low: 99,
      close: 100 + index,
    }));
    const overlay = indicatorOverlay(candles);
    expect(overlay.ema20[0].epoch).toBe(20 * 60);
    expect(overlay.ema20[overlay.ema20.length - 1].epoch).toBe(80 * 60);
    expect(overlay.ema20).toHaveLength(61);
    expect(emaSeries(candles, 8)).toHaveLength(73);
    expect(overlay.upper[0].epoch).toBe(20 * 60);
    expect(overlay.lower[overlay.lower.length - 1].epoch).toBe(80 * 60);
  });

  it('extends a trend line across the loaded candles', () => {
    const covered = coverChart(
      { id: 'channel', time1: 100, price1: 10, time2: 200, price2: 20, color: '#fff' },
      0,
      400,
    );
    expect(covered.time1).toBe(0);
    expect(covered.price1).toBe(0);
    expect(covered.time2).toBe(400);
    expect(covered.price2).toBe(40);
  });
});
