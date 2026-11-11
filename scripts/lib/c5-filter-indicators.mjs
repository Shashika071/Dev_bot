/**
 * Research-only, causal filters for C5. This module never places trades.
 * CHOP: https://www.tradingview.com/support/solutions/43000501980-choppiness-index-chop/
 * MACD: https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/
 * ER: https://www.tradingview.com/support/solutions/43000773012-kaufman-s-adaptive-moving-average-kama/
 * ER(20) >= 0.30 is a predeclared exploratory rule, not an official optimum.
 */

export const FILTER_DEFINITIONS = Object.freeze([
  Object.freeze({ id: 'BASELINE', label: 'C5 unchanged', description: 'Keep every eligible C5 signal.' }),
  Object.freeze({ id: 'CHOP14_LT_38_2', label: 'CHOP(14) < 38.2', description: 'Keep lower-choppiness conditions; this filter has no direction.' }),
  Object.freeze({ id: 'MACD_HIST_SIDE', label: 'MACD(12,26,9) histogram agrees', description: 'RISE requires histogram > 0; FALL requires histogram < 0.' }),
  Object.freeze({ id: 'ER20_GE_030', label: 'Efficiency Ratio(20) >= 0.30', description: 'Keep more efficient price movement; this filter has no direction.' }),
]);

// Lower-case alias keeps the research runner interface simple.
export const filterDefinitions = FILTER_DEFINITIONS;

function validCandle(candle) {
  return candle?.isClosed === true
    && ['time', 'open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key]))
    && candle.high >= Math.max(candle.open, candle.close, candle.low)
    && candle.low <= Math.min(candle.open, candle.close, candle.high);
}

/**
 * Compute aligned arrays from closed, chronological M1 candles. Each value
 * uses only candles at or before its own index. Invalid/open candles and any
 * non-60-second timestamp step reset all state. Null means unavailable.
 * EMA recursion starts at the first close (MACD signal EMA starts at zero).
 * A 100-candle warmup is required for MACD and EMA50 to limit seed effects.
 */
export function computeFilterSeries(candles) {
  const series = Object.fromEntries(
    ['chop14', 'macdHist', 'er20', 'ema50', 'segmentLength'].map(key => [key, Array(candles.length).fill(null)]),
  );
  let recent = [];
  let recentTr = [];
  let previousTime = null;
  let previousClose = null;
  let count = 0;
  let fast = null;
  let slow = null;
  let signal = null;
  let trend = null;

  function reset() {
    recent = [];
    recentTr = [];
    previousTime = null;
    previousClose = null;
    count = 0;
    fast = slow = signal = trend = null;
  }

  for (let i = 0; i < candles.length; i++) {
    const candle = candles[i];
    if (!validCandle(candle)) {
      reset();
      continue;
    }
    if (previousTime !== null && candle.time - previousTime !== 60) reset();

    const trueRange = previousClose === null ? null : Math.max(
      candle.high - candle.low,
      Math.abs(candle.high - previousClose),
      Math.abs(candle.low - previousClose),
    );
    count++;
    recent.push(candle);
    if (recent.length > 21) recent.shift();
    recentTr.push(trueRange);
    if (recentTr.length > 14) recentTr.shift();

    fast = fast === null ? candle.close : fast + (2 / 13) * (candle.close - fast);
    slow = slow === null ? candle.close : slow + (2 / 27) * (candle.close - slow);
    const macd = fast - slow;
    signal = signal === null ? macd : signal + (2 / 10) * (macd - signal);
    trend = trend === null ? candle.close : trend + (2 / 51) * (candle.close - trend);
    series.segmentLength[i] = count;
    if (count >= 100) {
      series.macdHist[i] = macd - signal;
      series.ema50[i] = trend;
    }

    // Require 14 true ranges with an observed previous close (15 candles).
    if (count >= 15) {
      const window = recent.slice(-14);
      const range = Math.max(...window.map(bar => bar.high)) - Math.min(...window.map(bar => bar.low));
      const totalTr = recentTr.reduce((sum, value) => sum + value, 0);
      if (range > 0 && totalTr > 0) series.chop14[i] = 100 * Math.log10(totalTr / range) / Math.log10(14);
    }

    if (count >= 21) {
      let pathLength = 0;
      for (let j = 1; j < recent.length; j++) pathLength += Math.abs(recent[j].close - recent[j - 1].close);
      if (pathLength > 0) series.er20[i] = Math.abs(candle.close - recent[0].close) / pathLength;
    }
    previousTime = candle.time;
    previousClose = candle.close;
  }
  return series;
}

export function filterPass(id, side, index, series) {
  if (!FILTER_DEFINITIONS.some(definition => definition.id === id)) throw new Error(`Unknown C5 research filter: ${id}`);
  if (side !== 'RISE' && side !== 'FALL') return false;
  if (!Number.isInteger(index) || index < 0 || series.segmentLength[index] === null || series.segmentLength[index] === undefined) return false;
  switch (id) {
    case 'BASELINE': return true;
    case 'CHOP14_LT_38_2': return Number.isFinite(series.chop14[index]) && series.chop14[index] < 38.2;
    case 'MACD_HIST_SIDE': return Number.isFinite(series.macdHist[index]) && (side === 'RISE' ? series.macdHist[index] > 0 : series.macdHist[index] < 0);
    case 'ER20_GE_030': return Number.isFinite(series.er20[index]) && series.er20[index] >= 0.30;
    default: return false;
  }
}
