import { describe, expect, it } from 'vitest';
import { accuracyFeatureGroupLabel, accuracyProfileLabel, coverChart, defaultOverlays, asAccuracySignal, asLiveSignal, emaSeries, indicatorOverlay, liveSignalMarkers, mergeCandles, mergeLiveSignals, signalOutcomeLabel, snapToBar, toggleOverlay } from './state';

const accuracyPayload = {
  signal_id: 'accuracy-1200', symbol: 'R_100', direction: 'upper',
  decision_epoch: 1200, expiry_epoch: 1745, created_at: '1970-01-01T00:20:02Z',
  estimated_probability: .97269, reference_price: 676.22, barrier_input: '+0.01',
  resolved: false, outcome: null,
};

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

  it('reads accuracy confidence and decision time without inventing an absolute barrier', () => {
    const signal = asAccuracySignal(accuracyPayload)!;
    expect(signal.epoch).toBe(1200);
    expect(signal.probability).toBe(.97269);
    expect(signal.source).toBe('accuracy');
    expect(signal.resolved_barrier).toBeNull();
    expect(signal.barrier_input).toBe('+0.01');
    expect(signalOutcomeLabel(signal)).toBe('Pending');
    expect(asLiveSignal({ ...accuracyPayload, epoch: 1200, probability: null, resolved_barrier: null })?.probability).toBeNull();
    expect(asLiveSignal({ ...accuracyPayload, epoch: 1200, resolved_barrier: null })?.resolved_barrier).toBeNull();
  });

  it('keeps the feature profile of the original signal when the active model changes', () => {
    const compact = asAccuracySignal(accuracyPayload)!;
    const technical = asAccuracySignal({
      ...accuracyPayload, signal_id: 'accuracy-7200', decision_epoch: 7200,
      feature_profile: 'technical_v2', feature_groups: ['trend', 'momentum_confirmation', null],
    })!;
    const merged = mergeLiveSignals([compact], [technical]);
    expect(accuracyProfileLabel(merged[0].feature_profile)).toBe('Technical analysis profile');
    expect(merged[0].feature_groups?.map(accuracyFeatureGroupLabel)).toEqual(['Market trend', 'Momentum/confirmation']);
    expect(accuracyProfileLabel(merged[1].feature_profile)).toBe('Compact profile');
    expect(merged[1].feature_groups).toBeUndefined();
  });

  it('retains a live signal absent from a slower history response and deduplicates replay', () => {
    const pending = asAccuracySignal(accuracyPayload)!;
    const newer = asAccuracySignal({ ...accuracyPayload, signal_id: 'accuracy-7200', decision_epoch: 7200 })!;
    const merged = mergeLiveSignals([newer, pending], [pending]);
    expect(merged.map((row) => row.signal_id)).toEqual(['accuracy-7200', 'accuracy-1200']);
  });

  it.each([true, false, null])('keeps resolved outcome %s when stale history or a repeated alert arrives', (outcome) => {
    const pending = asAccuracySignal(accuracyPayload)!;
    const resolved = asAccuracySignal({ ...accuracyPayload, resolved: true, outcome, touched: outcome })!;
    const updated = mergeLiveSignals([pending], [resolved]);
    const replayed = mergeLiveSignals(updated, [pending]);
    expect(replayed).toHaveLength(1);
    expect(replayed[0].resolved).toBe(true);
    expect(replayed[0].outcome).toBe(outcome);
    expect(signalOutcomeLabel(replayed[0])).toBe(outcome === true ? 'Touched' : outcome === false ? 'Missed' : 'Unresolved data');
  });

  it('paints accuracy arrows and changes their outcome text on the original candle', () => {
    const pending = asAccuracySignal({ ...accuracyPayload, direction: 'lower', barrier_input: '-0.01', decision_epoch: 1215 })!;
    expect(liveSignalMarkers([pending], [1140, 1200, 1260])).toEqual([
      { epoch: 1200, direction: 'lower', text: 'TOUCH LOWER 97.3% · Pending' },
    ]);
    const touched = { ...pending, resolved: true, outcome: true };
    expect(liveSignalMarkers([touched], [1140, 1200, 1260])[0].text).toBe('TOUCH LOWER 97.3% · Touched');
    expect(liveSignalMarkers([touched], [1260, 1320])).toEqual([]);
    expect(liveSignalMarkers([touched], [])).toEqual([]);
  });

  it('counts signals sharing a coarse candle instead of silently overwriting them', () => {
    const first = asAccuracySignal(accuracyPayload)!;
    const second = asAccuracySignal({ ...accuracyPayload, signal_id: 'accuracy-7200', decision_epoch: 7200, direction: 'lower' })!;
    const legacy = asLiveSignal({ signal_id: 'legacy', epoch: 8000, direction: 'upper', probability: .9 })!;
    expect(liveSignalMarkers([legacy, first, second], [0, 86400])).toEqual([
      { epoch: 0, direction: 'lower', text: 'TOUCH LOWER 97.3% · Pending · +2 more' },
    ]);
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
