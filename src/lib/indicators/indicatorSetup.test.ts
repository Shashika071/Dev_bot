import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import { enrich } from '../strategy/signalEngine'
import { atrRma, trueRange } from './atr'
import { computeRsi } from './rsi'
import { computeSma20 } from './sma'
import { computeSupertrend } from './supertrend'
import { computeUtBot } from './utBot'

function bars(prices: number[]): Candle[] {
  return prices.map((close, i) => ({
    time: 1_700_000_040 + i * 60,
    open: close, high: close + 1, low: close - 1, close, isClosed: true,
  }))
}

describe('configured indicator numerical checks', () => {
  it('uses true range including gaps and Wilder smoothing after the SMA seed', () => {
    expect(trueRange(12, 10, 8)).toBe(4)
    expect(trueRange(8, 6, 10)).toBe(4)
    expect(atrRma([10, 13, 12, 16], [8, 11, 9, 14], [9, 12, 10, 15], 3))
      .toEqual([null, null, 3, 4])
  })

  it.each([1, -1])('SMA20 averages closes and compares adjacent averages (%s)', (sign) => {
    const result = computeSma20(bars(Array.from({ length: 21 }, (_, i) => 100 + sign * i)))
    expect(result.sma.slice(0, 19)).toEqual(new Array(19).fill(null))
    expect(result.sma[19]).toBe(100 + sign * 9.5)
    expect(result.sma[20]).toBe(100 + sign * 10.5)
    expect(result.rising[19]).toBe(false)
    expect(result.falling[19]).toBe(false)
    expect(result.rising[20]).toBe(sign === 1)
    expect(result.falling[20]).toBe(sign === -1)
  })

  it('does not classify an unchanged SMA20 as rising or falling', () => {
    const result = computeSma20(bars(new Array(21).fill(100)))
    expect(result.sma[20]).toBe(100)
    expect(result.rising[20]).toBe(false)
    expect(result.falling[20]).toBe(false)
  })

  it('RSI14 seeds from 14 price changes and smooths gains/losses with Wilder RMA', () => {
    // Seven +2 changes and seven -1 changes: average gain=1, loss=0.5.
    const prices = Array.from({ length: 15 }, (_, i) => 100 + Math.floor(i / 2) + (i % 2) * 2)
    prices.push(prices.at(-1)! - 2)
    const result = computeRsi(bars(prices))
    expect(result.slice(0, 14)).toEqual(new Array(14).fill(null))
    expect(result[14]).toBeCloseTo(200 / 3, 10)
    // Next gain=13/14, loss=17/28, so RSI=2600/43.
    expect(result[15]).toBeCloseTo(2600 / 43, 10)
  })

  it.each([1, -1])('RSI14 handles a one-way price series (%s)', (sign) => {
    const result = computeRsi(bars(Array.from({ length: 17 }, (_, i) => 100 + sign * i)))
    expect(result.slice(14)).toEqual(new Array(3).fill(sign === 1 ? 100 : 0))
  })

  it('UT uses ATR10 × 2 and emits only fresh close/trail crossovers', () => {
    const candles = bars([...new Array(10).fill(100), 96, 95, 94, 101])
    const result = computeUtBot(candles)
    expect(result.trail.slice(0, 9)).toEqual(new Array(9).fill(null))
    expect(result.trail[9]).toBe(96)
    expect(result.trail[10]).toBeCloseTo(100.6, 10)
    expect(result.sell[10]).toBe(true)
    expect(result.sell[11]).toBe(false)
    expect(result.sell[12]).toBe(false)
    expect(result.buy[13]).toBe(true)
    expect(result.buy.slice(0, 13).some(Boolean)).toBe(false)
  })

  it('the shared strategy uses the configured indicator periods and multipliers', () => {
    const candles = bars(Array.from({ length: 80 }, (_, i) => 100 + Math.sin(i / 3) * 8))
    const series = enrich(candles)
    const ut = computeUtBot(candles, 2, 10)
    const st = computeSupertrend(candles, 7, 2)
    const sma = computeSma20(candles, 20)
    expect(series.utTrail).toEqual(ut.trail)
    expect(series.utBuy).toEqual(ut.buy)
    expect(series.utSell).toEqual(ut.sell)
    expect(series.stLine).toEqual(st.line)
    expect(series.stDir).toEqual(st.dir)
    expect(series.sma20).toEqual(sma.sma)
    expect(series.smaRising).toEqual(sma.rising)
    expect(series.smaFalling).toEqual(sma.falling)
    expect(series.rsi).toEqual(computeRsi(candles, 14))
  })
})

// Reference: KivancOzbilgic's original SuperTrend (not ta.supertrend).
// https://www.tradingview.com/script/r6dAP7yi/
describe('Kivanc Supertrend ATR7 × 2', () => {
  it('waits for seven true ranges and seeds bullish when close is above the lower band', () => {
    const result = computeSupertrend(bars(new Array(7).fill(100)))
    expect(result.line).toEqual([...new Array(6).fill(null), 96])
    expect(result.dir).toEqual([0, 0, 0, 0, 0, 0, 1])
  })

  it('compares the close with the previous band when a large wick moves the new band', () => {
    const candles = bars(new Array(9).fill(100))
    candles[7]!.high = 130
    const result = computeSupertrend(candles)
    // ATR[7]=43/7; the new lower band is above 100, but the prior band is 96.
    expect(result.dir[7]).toBe(1)
    expect(result.line[7]).toBeCloseTo(114.5 - 2 * 43 / 7, 10)
    expect(result.dir[8]).toBe(-1)
    expect(result.line[8]).toBe(104)
  })

  it('resets the lower band when the previous close equals that band', () => {
    const candles = bars(new Array(9).fill(100))
    Object.assign(candles[7]!, { open: 100, high: 100, low: 95, close: 96 })
    Object.assign(candles[8]!, { open: 96, high: 99, low: 95, close: 98 })
    const result = computeSupertrend(candles)
    expect(result.dir[7]).toBe(1)
    expect(result.line[7]).toBe(96)
    expect(result.dir[8]).toBe(1)
    expect(result.line[8]).toBeCloseTo(97 - 2 * 130 / 49, 10)
  })

  it('evaluates the first valid band instead of always forcing a bullish seed', () => {
    const candles = bars(new Array(7).fill(100))
    for (const c of candles) { c.high = 100; c.low = 100 }
    candles[6]!.high = 140
    const result = computeSupertrend(candles)
    expect(result.dir[6]).toBe(-1)
    expect(result.line[6]).toBeCloseTo(120 + 2 * 40 / 7, 10)
  })

  it('preserves direction when zero ATR makes both bands equal', () => {
    const candles = bars(new Array(12).fill(100))
    for (const c of candles) { c.high = 100; c.low = 100 }
    const result = computeSupertrend(candles)
    expect(result.dir.slice(6)).toEqual(new Array(6).fill(1))
    expect(result.line.slice(6)).toEqual(new Array(6).fill(100))
  })
})
