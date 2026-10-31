import { describe, expect, it } from 'vitest'
import { observeTouch, observeTouchWindow, runTouchBacktest, summarizeTouches } from './touchBacktest'
import { fixtureCandles } from '../testFixtures'
import { evaluateAll } from '../strategy/signalEngine'
import type { Candle } from '../types'

const signal: Candle = { time: 1800000000, open: 100, high: 100.1, low: 99.9, close: 100, isClosed: true }
const next = (high: number, low: number, close = 100): Candle => ({ ...signal, time: signal.time + 60, high, low, close })

describe('absolute 0.03 directional touch', () => {
  it('includes exact +0.03 and -0.03 thresholds despite floating-point rounding', () => {
    expect(observeTouch(signal, next(100.03, 100), 'RISE')?.touched).toBe(true)
    expect(observeTouch(signal, next(100, 99.97), 'FALL')?.touched).toBe(true)
    expect(observeTouch(signal, next(100.029, 100), 'RISE')?.touched).toBe(false)
    expect(observeTouch(signal, next(100, 99.971), 'FALL')?.touched).toBe(false)
  })
  it('separates touches, opposite touches and end-of-minute direction', () => {
    const row = observeTouch(signal, next(100.05, 99.95, 99.96), 'RISE')!
    expect(row.touched).toBe(true)
    expect(row.oppositeTouched).toBe(true)
    expect(row.bothTouched).toBe(true)
    expect(row.closeDirectionHit).toBe(false)
  })
  it('reports sensitivity to using next open rather than signal close', () => {
    const row = observeTouch(signal, { ...next(100.05, 100.04, 100.04), open: 100.04 }, 'RISE')!
    expect(row.touched).toBe(true)
    expect(row.touchedFromNextOpen).toBe(false)
  })
  it('excludes incomplete, non-adjacent and invalid outcome candles', () => {
    expect(observeTouch(signal, { ...next(101, 99), isClosed: false }, 'RISE')).toBeNull()
    expect(observeTouch(signal, { ...next(101, 99), time: signal.time + 120 }, 'RISE')).toBeNull()
    expect(observeTouch(signal, { ...next(101, 99), high: NaN }, 'RISE')).toBeNull()
    expect(() => observeTouch(signal, next(101, 99), 'RISE', 0)).toThrow()
  })
  it('partitions every outcome exactly once and calculates the matched baseline', () => {
    const rows = [next(100.04, 99.99), next(100.01, 99.96), next(100.05, 99.95), next(100.01, 99.99)].map((c) => observeTouch(signal, c, 'RISE')!)
    const stats = summarizeTouches(rows)
    expect([stats.onlySignalDirection, stats.onlyOppositeDirection, stats.bothTouched, stats.neither]).toEqual([1, 1, 1, 1])
    expect(stats.touchRate).toBe(0.5)
    expect(stats.matchedRandomDirectionTouchRate).toBe(0.5)
  })
  it('uses exactly the production signals, and future data does not change past rows', () => {
    const candles = fixtureCandles(4000)
    const result = runTouchBacktest(candles)
    const expected = evaluateAll(candles).filter((e) => e.isQualified && e.time < candles.at(-1)!.time)
    expect(result.rows.map((r) => [r.signalTime, r.side])).toEqual(expected.map((e) => [e.time, e.final]))
    const prefix = runTouchBacktest(candles.slice(0, 2000))
    expect(result.rows.filter((r) => r.outcomeTime <= candles[1999].time)).toEqual(prefix.rows)
    expect(result.report.overall.n).toBeGreaterThan(0)
    expect(result.report.monthly.reduce((n, m) => n + m.n, 0)).toBe(result.report.overall.n)
  })
  it('includes minute seven, excludes minute eight, and uses the seventh close', () => {
    const future = Array.from({ length: 8 }, (_, i) => ({ ...next(100.01, 99.99), time: signal.time + (i + 1) * 60 }))
    future[6] = { ...future[6], high: 100.03, low: 99.97, close: 99.98 }
    future[7].high = 101
    const row = observeTouchWindow(signal, future.slice(0, 7), 'RISE', 0.03, 7)!
    expect(row.touched).toBe(true)
    expect(row.firstTouchMinute).toBe(7)
    expect(row.nextHigh).toBe(100.03)
    expect(row.nextClose).toBe(99.98)
    expect(row.closeDirectionHit).toBe(false)
    expect(row.outcomeEndTime).toBe(signal.time + 8 * 60)
    future[6].high = 100.01
    expect(observeTouchWindow(signal, future.slice(0, 7), 'RISE', 0.03, 7)?.touched).toBe(false)
  })
  it('requires seven consecutive completed outcome candles', () => {
    const future = Array.from({ length: 7 }, (_, i) => ({ ...next(100.01, 99.99), time: signal.time + (i + 1) * 60 }))
    expect(observeTouchWindow(signal, future.slice(0, 6), 'RISE', 0.03, 7)).toBeNull()
    future[6].isClosed = false
    expect(observeTouchWindow(signal, future, 'RISE', 0.03, 7)).toBeNull()
    future[6].isClosed = true
    future[3].time += 60
    expect(observeTouchWindow(signal, future, 'RISE', 0.03, 7)).toBeNull()
  })
  it('keeps live signals unchanged when extending the horizon and accounts for first-touch timing', () => {
    const candles = fixtureCandles(4000)
    const one = runTouchBacktest(candles)
    const seven = runTouchBacktest(candles, 0.03, 7)
    expect(seven.report.qualified).toBe(one.report.qualified)
    const map = new Map(one.rows.map((r) => [r.signalTime, r]))
    for (const row of seven.rows) {
      expect(row.side).toBe(map.get(row.signalTime)?.side)
      if (map.get(row.signalTime)?.touched) expect(row.touched).toBe(true)
    }
    expect(seven.report.touchTiming.reduce((n, t) => n + t.firstTouches, 0)).toBe(seven.report.overall.touched)
    expect(seven.rows.every((r) => r.outcomeEndTime <= candles.at(-1)!.time + 60)).toBe(true)
    expect(() => runTouchBacktest(candles, 0.03, 0)).toThrow()
  })
})
