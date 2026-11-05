import { describe, expect, it } from 'vitest'
import { enrich, evaluateAll, evaluateIndex, evaluateLatestClosed } from './signalEngine'
import { fixtureCandles } from '../testFixtures'

function passing(side: 'RISE' | 'FALL', aoAgree = true) {
  const s = enrich(fixtureCandles(100))
  const i = 99
  const up = side === 'RISE'
  s.utBuy[i] = up
  s.utSell[i] = !up
  s.stDir[i] = up ? 1 : -1
  s.sma20[i] = s.candles[i]!.close + (up ? -1 : 1)
  s.smaRising[i] = up
  s.smaFalling[i] = !up
  s.rsi[i] = up ? 60 : 40
  if (aoAgree) {
    s.ao[i] = up ? 2 : -2
    s.ao[i - 1] = up ? 1 : -1
  } else {
    s.ao[i] = up ? -2 : 2
    s.ao[i - 1] = up ? -1 : 1
  }
  s.candles[i]!.isClosed = true
  return s
}

describe('C5 main signal acceptance and boundaries', () => {
  it.each(['RISE', 'FALL'] as const)('accepts %s when all four required pass', (side) => {
    const s = passing(side)
    const ev = evaluateIndex(s, 99)
    expect(ev.final).toBe(side)
    expect(ev.strategyId).toBe('C5')
    expect(ev.requiredPassed).toBe(4)
    for (const key of ['ut', 'supertrend', 'sma', 'rsi'] as const) {
      const broken = passing(side)
      if (key === 'ut') { broken.utBuy[99] = false; broken.utSell[99] = false }
      if (key === 'supertrend') broken.stDir[99] = 0
      if (key === 'sma') broken.sma20[99] = broken.candles[99]!.close
      if (key === 'rsi') broken.rsi[99] = 50
      expect(evaluateIndex(broken, 99).final, key).toBe('NO_TRADE')
    }
  })

  it.each(['RISE', 'FALL'] as const)('AO disagreement does not block %s', (side) => {
    const s = passing(side, false)
    const ev = evaluateIndex(s, 99)
    expect(ev.final).toBe(side)
    expect(ev.confirmations.ao).toBe(false)
    expect(ev.isQualified).toBe(true)
  })

  it.each([['RISE', 52], ['RISE', 70], ['FALL', 30], ['FALL', 48]] as const)(
    'rejects %s RSI boundary %s',
    (side, rsi) => {
      const s = passing(side)
      s.rsi[99] = rsi
      expect(evaluateIndex(s, 99).isQualified).toBe(false)
    },
  )

  it.each(['RISE', 'FALL'] as const)('rejects flat slope, missing values, open bars, dual UT for %s', (side) => {
    for (const kind of ['sma', 'missing', 'open', 'both'] as const) {
      const s = passing(side)
      if (kind === 'sma') { s.smaRising[99] = false; s.smaFalling[99] = false }
      if (kind === 'missing') s.rsi[99] = null
      if (kind === 'open') s.candles[99]!.isClosed = false
      if (kind === 'both') { s.utBuy[99] = true; s.utSell[99] = true }
      expect(evaluateIndex(s, 99).isQualified, kind).toBe(false)
    }
  })

  it('uses identical warmup for live and historical evaluation', () => {
    expect(evaluateLatestClosed(fixtureCandles(60))).toBeNull()
    expect(evaluateAll(fixtureCandles(60))).toEqual([])
    expect(evaluateLatestClosed(fixtureCandles(61))).toEqual(evaluateAll(fixtureCandles(61))[0])
  })

  it('historical evaluations are causal (prefix-only history matches)', () => {
    const candles = fixtureCandles(150)
    const all = evaluateAll(candles)
    expect(all.length).toBeGreaterThan(0)
    for (const ev of all.slice(-8)) {
      const prefix = candles.filter((c) => c.time <= ev.time)
      expect(evaluateLatestClosed(prefix)).toEqual(ev)
    }
  })

  it('restarts warmup after a gap or invalid bar', () => {
    const candles = fixtureCandles(150)
    candles.splice(100, 1)
    expect(evaluateLatestClosed(candles)).toBeNull()
    expect(evaluateAll(candles).every((e) => e.time < candles[100]!.time)).toBe(true)
    const invalid = fixtureCandles(150)
    invalid[100]!.high = NaN
    expect(evaluateLatestClosed(invalid)).toBeNull()
  })
})
