import { describe, expect, it } from 'vitest'
import { accepts, runBacktest, variants } from './backtest'
import { summarize, wilsonInterval } from './statistics'
import { fixtureCandles } from '../testFixtures'
import { evaluateAll } from '../strategy/signalEngine'

describe('statistics', () => {
  it('reports uncertainty and loss-based accounting with a net win return', () => {
    const stats = summarize([true, false, false, true, false], 0.8)
    expect(stats.netUnits).toBeCloseTo(-1.4)
    expect(stats.maxDrawdown).toBeCloseTo(2.2)
    expect(stats.maxLosingStreak).toBe(2)
    expect(stats.breakEven).toBeCloseTo(1 / 1.8)
    expect(wilsonInterval(50, 100)?.[0]).toBeCloseTo(0.40383, 4)
    expect(wilsonInterval(50, 100)?.[1]).toBeCloseTo(0.59617, 4)
    expect(summarize([], 0.8).winRate).toBeNull()
    expect(wilsonInterval(0, 0)).toBeNull()
    expect(() => summarize([], NaN)).toThrow()
  })
})

describe('chronological research', () => {
  it('reuses actual strategy decisions and keeps outcomes inside the holdout', () => {
    const candles = fixtureCandles(4000)
    const split = candles[2800].time
    // Research "Current five rules" still requires AO; live C5 isQualified does not.
    const five = variants.find((v) => v.name === 'Current five rules')!.required
    const expected = evaluateAll(candles).filter((e) =>
      accepts(e, five) && e.time >= split && e.time < candles.at(-1)!.time)
    expect(expected.length).toBeGreaterThan(0)
    const report = runBacktest(candles)
    const result = report.rows.find((r) => r.window === 'Holdout final 30%' && r.variant === 'Current five rules')!
    expect(result.n).toBe(expected.length)
    const byTime = new Map(candles.map((c) => [c.time, c]))
    expect(result.wins).toBe(expected.filter((e) => e.final === 'RISE'
      ? byTime.get(e.time + 60)!.close > e.close : byTime.get(e.time + 60)!.close < e.close).length)
  })
  it('ignores the forming candle and rejects unsorted or malformed input', () => {
    const candles = fixtureCandles(300)
    candles.at(-1)!.isClosed = false
    expect(runBacktest(candles)).toEqual(runBacktest(candles.slice(0, -1)))
    expect(() => runBacktest([...candles].reverse())).toThrow()
    const invalid = fixtureCandles(300)
    invalid[10].low = NaN
    expect(() => runBacktest(invalid)).toThrow()
  })
  it('reports missing minutes and restarts warmup after each gap', () => {
    const candles = fixtureCandles(300)
    candles.splice(200, 1)
    expect(runBacktest(candles).gaps).toBe(1)
    expect(evaluateAll(candles).some((e) => e.time >= candles[200].time && e.time < candles[260].time)).toBe(false)
  })
})
