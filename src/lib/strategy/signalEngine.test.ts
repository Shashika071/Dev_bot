import { describe, expect, it } from 'vitest'
import { enrich, evaluateAll, evaluateIndex, evaluateLatestClosed } from './signalEngine'
import { FILTER_PRESETS } from './filters'
import { fixtureCandles } from '../testFixtures'

function passing(side: 'RISE' | 'FALL') {
  const s = enrich(fixtureCandles(100))
  const i = 99
  const up = side === 'RISE'
  s.utBuy[i] = up
  s.utSell[i] = !up
  s.stDir[i] = up ? 1 : -1
  s.sma20[i] = s.candles[i].close + (up ? -1 : 1)
  s.smaRising[i] = up
  s.smaFalling[i] = !up
  s.ao[i] = up ? 2 : -2
  s.ao[i - 1] = up ? 1 : -1
  s.rsi[i] = up ? 60 : 40
  return s
}

describe('signal acceptance and boundaries', () => {
  it.each(['RISE', 'FALL'] as const)('accepts %s only when all checks pass', (side) => {
    const s = passing(side)
    expect(evaluateIndex(s, 99).final).toBe(side)
    for (const key of ['ut', 'supertrend', 'sma', 'ao', 'rsi']) {
      const broken = passing(side)
      if (key === 'ut') { broken.utBuy[99] = false; broken.utSell[99] = false }
      if (key === 'supertrend') broken.stDir[99] = 0
      if (key === 'sma') broken.sma20[99] = broken.candles[99].close
      if (key === 'ao') broken.ao[99] = 0
      if (key === 'rsi') broken.rsi[99] = 50
      expect(evaluateIndex(broken, 99).final, key).toBe('NO_TRADE')
    }
  })
  it.each([['RISE', 52], ['RISE', 70], ['FALL', 30], ['FALL', 48]] as const)('rejects %s RSI boundary %s', (side, rsi) => {
    const s = passing(side)
    s.rsi[99] = rsi
    expect(evaluateIndex(s, 99).isQualified).toBe(false)
  })
  it.each(['RISE', 'FALL'] as const)('rejects flat slope, missing values, and open bars for %s', (side) => {
    for (const kind of ['sma', 'ao', 'missing', 'open', 'both']) {
      const s = passing(side)
      if (kind === 'sma') { s.smaRising[99] = false; s.smaFalling[99] = false }
      if (kind === 'ao') s.ao[99] = s.ao[98]
      if (kind === 'missing') s.rsi[99] = null
      if (kind === 'open') s.candles[99].isClosed = false
      if (kind === 'both') { s.utBuy[99] = true; s.utSell[99] = true }
      expect(evaluateIndex(s, 99).isQualified, kind).toBe(false)
    }
  })
  it('uses identical warmup for live and historical evaluation', () => {
    expect(evaluateLatestClosed(fixtureCandles(60))).toBeNull()
    expect(evaluateAll(fixtureCandles(60))).toEqual([])
    expect(evaluateLatestClosed(fixtureCandles(61))).toEqual(evaluateAll(fixtureCandles(61))[0])
  })
  it('does not change earlier evaluations when future candles arrive', () => {
    const candles = fixtureCandles(400)
    expect(evaluateAll(candles).filter((e) => e.time < candles[200].time)).toEqual(evaluateAll(candles.slice(0, 200)))
  })
  it('restarts warmup after a gap or invalid bar', () => {
    const candles = fixtureCandles(150)
    candles.splice(100, 1)
    expect(evaluateLatestClosed(candles)).toBeNull()
    expect(evaluateAll(candles).every((e) => e.time < candles[100].time)).toBe(true)
    const invalid = fixtureCandles(150)
    invalid[100].high = NaN
    expect(evaluateLatestClosed(invalid)).toBeNull()
  })
  it('optional filters can reject an otherwise-qualified RISE', () => {
    const s = passing('RISE')
    const atr = s.filters.atr14[99]
    expect(atr).toBeTypeOf('number')
    s.candles[99] = { ...s.candles[99], high: s.candles[99].low + (atr as number) * 3 }
    expect(evaluateIndex(s, 99).final).toBe('RISE')
    expect(evaluateIndex(s, 99, FILTER_PRESETS.skip_large_candle_1_5x).final).toBe('NO_TRADE')
  })
})
