import test from 'node:test';
import assert from 'node:assert/strict';
import { computeFilterSeries, filterPass, FILTER_DEFINITIONS } from './c5-filter-indicators.mjs';

function bars(count, closeAt = i => 100 + i, width = 0.25) {
  return Array.from({ length: count }, (_, i) => {
    const close = closeAt(i);
    return { time: 1_700_000_000 + i * 60, open: close, high: close + width, low: close - width, close, isClosed: true };
  });
}

function near(actual, expected, tolerance = 1e-10) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `Expected ${actual} to be near ${expected}`);
}

test('monotonic prices have ER=1 and CHOP follows the documented TR/range formula', () => {
  const result = computeFilterSeries(bars(150));
  assert.equal(result.chop14[13], null);
  near(result.chop14[14], 100 * Math.log10(17.5 / 13.5) / Math.log10(14));
  assert.equal(result.er20[19], null);
  assert.equal(result.er20[20], 1);
  assert.equal(result.macdHist[98], null);
  assert.equal(result.ema50[98], null);
  assert.ok(result.macdHist[99] > 0);
  assert.ok(result.ema50[99] < 199);
  assert.equal(filterPass('MACD_HIST_SIDE', 'RISE', 99, result), true);
  assert.equal(filterPass('MACD_HIST_SIDE', 'FALL', 99, result), false);
  assert.equal(filterPass('CHOP14_LT_38_2', 'RISE', 99, result), true);
  assert.equal(filterPass('ER20_GE_030', 'FALL', 99, result), true);
});

test('alternating prices are choppy and have zero net efficiency', () => {
  const result = computeFilterSeries(bars(150, i => 100 + i % 2));
  near(result.chop14[100], 100 * Math.log10(17.5 / 1.5) / Math.log10(14));
  assert.equal(result.er20[100], 0);
  assert.equal(filterPass('CHOP14_LT_38_2', 'FALL', 100, result), false);
  assert.equal(filterPass('ER20_GE_030', 'RISE', 100, result), false);
});

test('flat prices produce unavailable zero-range CHOP and ER, zero MACD', () => {
  const result = computeFilterSeries(bars(120, () => 100, 0));
  assert.equal(result.chop14[119], null);
  assert.equal(result.er20[119], null);
  assert.equal(result.macdHist[119], 0);
  assert.equal(result.ema50[119], 100);
  for (const definition of FILTER_DEFINITIONS.slice(1)) assert.equal(filterPass(definition.id, 'RISE', 119, result), false);
});

test('MACD and EMA50 match independent first-value seeded recursion', () => {
  const candles = bars(180, i => 100 + Math.sin(i / 7) * 3 + i / 20);
  const result = computeFilterSeries(candles);
  let ema12 = candles[0].close;
  let ema26 = candles[0].close;
  let ema50 = candles[0].close;
  let signal = 0;
  for (let i = 1; i < candles.length; i++) {
    ema12 = (2 / 13) * candles[i].close + (11 / 13) * ema12;
    ema26 = (2 / 27) * candles[i].close + (25 / 27) * ema26;
    ema50 = (2 / 51) * candles[i].close + (49 / 51) * ema50;
    signal = 0.2 * (ema12 - ema26) + 0.8 * signal;
    if (i >= 99) {
      near(result.macdHist[i], ema12 - ema26 - signal);
      near(result.ema50[i], ema50);
    }
  }
});

test('prefix invariance prevents future candles changing earlier filter values', () => {
  const candles = bars(180, i => 100 + Math.sin(i / 3) + i / 100);
  const full = computeFilterSeries(candles);
  for (const length of [15, 21, 100, 137]) {
    const prefix = computeFilterSeries(candles.slice(0, length));
    for (const key of Object.keys(full)) assert.deepEqual(full[key].slice(0, length), prefix[key]);
  }
});

test('a missing minute resets all history and recursive states', () => {
  const candles = bars(240);
  for (let i = 120; i < candles.length; i++) candles[i].time += 60;
  const full = computeFilterSeries(candles);
  const fresh = computeFilterSeries(candles.slice(120));
  for (const key of Object.keys(full)) assert.deepEqual(full[key].slice(120), fresh[key]);
  assert.equal(full.macdHist[218], null);
  assert.notEqual(full.macdHist[219], null);
  assert.equal(full.segmentLength[120], 1);
});

test('invalid OHLC or unfinished candles reset and cannot pass a filter', () => {
  for (const mutation of [{ isClosed: false }, { close: NaN }, { high: -1 }, { low: Infinity }]) {
    const candles = bars(230);
    Object.assign(candles[120], mutation);
    const result = computeFilterSeries(candles);
    const fresh = computeFilterSeries(candles.slice(121));
    for (const key of Object.keys(result)) assert.deepEqual(result[key].slice(121), fresh[key]);
    for (const definition of FILTER_DEFINITIONS) assert.equal(filterPass(definition.id, 'RISE', 120, result), false);
  }
});

test('filter thresholds are strict for CHOP and MACD, inclusive for ER', () => {
  const series = { segmentLength: [100], chop14: [38.2], macdHist: [0], er20: [0.30] };
  assert.equal(filterPass('CHOP14_LT_38_2', 'RISE', 0, series), false);
  assert.equal(filterPass('MACD_HIST_SIDE', 'RISE', 0, series), false);
  assert.equal(filterPass('MACD_HIST_SIDE', 'FALL', 0, series), false);
  assert.equal(filterPass('ER20_GE_030', 'FALL', 0, series), true);
  assert.equal(filterPass('BASELINE', 'NO_TRADE', 0, series), false);
  assert.equal(filterPass('BASELINE', 'RISE', -1, series), false);
  assert.throws(() => filterPass('TYPO', 'RISE', 0, series), /Unknown/);
});
