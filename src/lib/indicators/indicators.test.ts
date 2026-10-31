import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import { atrRma } from './atr'
import { computeUtBot } from './utBot'
import { computeSupertrend } from './supertrend'
import { computeSma20 } from './sma'
import { computeAO } from './ao'
import { computeRsi } from './rsi'
import { computeSupportResistance } from './supportResistance'
import { evaluateIndex, enrich, evaluateLatestClosed, evaluateAll } from '../strategy/signalEngine'
import { minuteBucket, applyTick } from '../deriv/candles'

function makeCandles(n: number, start = 1_700_000_000): Candle[] {
  const out: Candle[] = []
  let px = 1000
  for (let i = 0; i < n; i++) {
    const o = px
    const c = px + ((i % 5) - 2) * 0.4
    const h = Math.max(o, c) + 0.5
    const l = Math.min(o, c) - 0.5
    out.push({
      time: minuteBucket(start) + i * 60,
      open: o,
      high: h,
      low: l,
      close: c,
      isClosed: true,
    })
    px = c
  }
  return out
}

describe('ATR RMA', () => {
  it('seeds then recurses', () => {
    const h = [10, 11, 12, 13, 14, 15, 16]
    const l = [9, 9.5, 10, 11, 12, 13, 14]
    const c = [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5]
    const atr = atrRma(h, l, c, 3)
    expect(atr[0]).toBeNull()
    expect(atr[1]).toBeNull()
    expect(atr[2]).not.toBeNull()
    const tr3 = Math.max(h[3] - l[3], Math.abs(h[3] - c[2]), Math.abs(l[3] - c[2]))
    const expected = (atr[2] as number) + (1 / 3) * (tr3 - (atr[2] as number))
    expect(atr[3]).toBeCloseTo(expected, 10)
  })
})

describe('candle timing', () => {
  it('uses UTC minute buckets', () => {
    expect(minuteBucket(100)).toBe(60)
    expect(minuteBucket(119)).toBe(60)
    expect(minuteBucket(120)).toBe(120)
  })

  it('closes previous candle on new minute tick', () => {
    const t0 = 1_700_000_000
    let candles: Candle[] = []
    let r = applyTick(candles, { epoch: t0 + 10, quote: 100, symbol: 'R_100' })
    candles = r.candles
    expect(r.closedCandle).toBeNull()
    r = applyTick(candles, { epoch: t0 + 70, quote: 101, symbol: 'R_100' })
    expect(r.closedCandle).not.toBeNull()
    expect(r.closedCandle!.time).toBe(minuteBucket(t0 + 10))
    expect(r.candles[r.candles.length - 1].isClosed).toBe(false)
  })
})

describe('indicators produce values', () => {
  it('ut / st / sma / ao / rsi', () => {
    const candles = makeCandles(120)
    const ut = computeUtBot(candles)
    const st = computeSupertrend(candles)
    const sma = computeSma20(candles)
    const ao = computeAO(candles)
    const rsi = computeRsi(candles)
    expect(ut.trail.some((x) => x != null)).toBe(true)
    expect(st.dir.some((d) => d !== 0)).toBe(true)
    expect(sma.sma[50]).not.toBeNull()
    expect(ao[50]).not.toBeNull()
    expect(rsi[50]).not.toBeNull()
  })
})

describe('support resistance no lookahead', () => {
  it('pivot confirm index is i+right', () => {
    const candles = makeCandles(40)
    candles[10] = { ...candles[10], high: 9999 }
    const levels = computeSupportResistance(candles, 3, 2)
    const res = levels.find((l) => l.type === 'resistance' && Math.abs(l.price - 9999) < 1e-6)
    expect(res).toBeTruthy()
    expect(res!.confirmTime).toBe(candles[12].time)
  })
})

describe('signal engine rules', () => {
  it('NO TRADE without fresh UT signal', () => {
    const candles = makeCandles(100)
    const series = enrich(candles)
    // Force no UT on last bar
    series.utBuy[series.utBuy.length - 1] = false
    series.utSell[series.utSell.length - 1] = false
    const ev = evaluateIndex(series, series.candles.length - 1)
    expect(ev.final).toBe('NO_TRADE')
    expect(ev.rawUt).toBe('NONE')
  })

  it('rejects RISE when Supertrend bearish even if UT buy', () => {
    const candles = makeCandles(100)
    const series = enrich(candles)
    const i = series.candles.length - 1
    series.utBuy[i] = true
    series.utSell[i] = false
    series.stDir[i] = -1
    const ev = evaluateIndex(series, i)
    expect(ev.final).toBe('NO_TRADE')
    expect(ev.failures.some((f) => f.toLowerCase().includes('supertrend'))).toBe(true)
  })

  it('does not use open candle when evaluating latest closed', () => {
    const candles = makeCandles(80)
    candles[candles.length - 1] = { ...candles[candles.length - 1], isClosed: false }
    const expected = evaluateLatestClosed(candles.slice(0, -1))
    expect(evaluateLatestClosed(candles)).toEqual(expected)
    candles[candles.length - 1].close = 999999
    expect(evaluateLatestClosed(candles)).toEqual(expected)
    expect(evaluateAll(candles).at(-1)).toEqual(expected)
  })
})
