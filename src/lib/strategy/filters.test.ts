import { describe, expect, it } from 'vitest'
import { fixtureCandles } from '../testFixtures'
import {
  applyOptionalFilters,
  buildFilterSeries,
  DEFAULT_FILTER_CONFIG,
  FILTER_PRESETS,
} from './filters'
import { enrich, evaluateIndex } from './signalEngine'

function passingRise() {
  const s = enrich(fixtureCandles(120))
  const i = 119
  s.utBuy[i] = true
  s.utSell[i] = false
  s.stDir[i] = 1
  s.sma20[i] = s.candles[i].close - 1
  s.smaRising[i] = true
  s.smaFalling[i] = false
  s.ao[i] = 2
  s.ao[i - 1] = 1
  s.rsi[i] = 60
  return { s, i }
}

describe('optional filters', () => {
  it('baseline accepts when five rules pass', () => {
    const { s, i } = passingRise()
    expect(evaluateIndex(s, i, DEFAULT_FILTER_CONFIG).final).toBe('RISE')
  })

  it('large-candle filter rejects oversized range', () => {
    const { s, i } = passingRise()
    const atr = s.filters.atr14[i]
    expect(atr).toBeTypeOf('number')
    s.candles[i] = {
      ...s.candles[i],
      high: s.candles[i].low + (atr as number) * 2,
    }
    const check = applyOptionalFilters(s.candles, s.filters, i, 'RISE', FILTER_PRESETS.skip_large_candle_1_5x)
    expect(check.passed).toBe(false)
    expect(evaluateIndex(s, i, FILTER_PRESETS.skip_large_candle_1_5x).final).toBe('NO_TRADE')
  })

  it('volatility min filter rejects low ATR vs target', () => {
    const { s, i } = passingRise()
    s.filters.atr14[i] = 0.03 * 5 // below 10× target
    const check = applyOptionalFilters(s.candles, s.filters, i, 'RISE', FILTER_PRESETS.vol_atr_ge_10x)
    expect(check.passed).toBe(false)
  })

  it('m5 trend filter requires completed M5 alignment', () => {
    const candles = fixtureCandles(400)
    const series = buildFilterSeries(candles)
    const i = candles.length - 1
    expect(series.m5Close[i]).not.toBeNull()
    const cfg = FILTER_PRESETS.m5_trend
    // Force mismatch: RISE needs close > rising SMA
    series.m5Close[i] = 1
    series.m5Sma20[i] = 100
    series.m5SmaRising[i] = false
    const check = applyOptionalFilters(candles, series, i, 'RISE', cfg)
    expect(check.passed).toBe(false)
    expect(check.failures.some((f) => f.includes('M5'))).toBe(true)
  })

  it('presets keep targetDistance at 0.03', () => {
    for (const preset of Object.values(FILTER_PRESETS)) {
      expect(preset.targetDistance).toBe(0.03)
    }
  })
})
