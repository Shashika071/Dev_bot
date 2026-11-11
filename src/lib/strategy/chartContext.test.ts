import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import { contextAt, getChartContexts } from './chartContext'

const bar = (i: number, values: Partial<Candle> = {}): Candle => ({
  time: i * 60, open: 100, high: 101, low: 99, close: 100, isClosed: true, ...values,
})
const history = () => Array.from({ length: 21 }, (_, i) => bar(i))

describe('display-only chart context', () => {
  it('uses the prior 20 bars and never changes with future candles', () => {
    const candles = history()
    candles[20] = bar(20, { high: 103, close: 102 })
    const context = contextAt(candles, 20)!
    expect(context.high).toBe(101)
    expect(context.low).toBe(99)
    expect(context.label).toBe('Up breakout')
    expect(context.locationPct).toBe(150)
    expect(contextAt([...candles, bar(21, { high: 999 })], 20)).toEqual(context)
  })

  it('requires strict closes outside the channel and recognizes rejected wicks', () => {
    const candles = history()
    candles[20] = bar(20, { high: 103, close: 101 })
    expect(contextAt(candles, 20)?.label).toBe('Upper rejection')
    candles[20] = bar(20, { low: 97, close: 99 })
    expect(contextAt(candles, 20)?.label).toBe('Lower rejection')
    candles[20] = bar(20, { low: 97, high: 103 })
    expect(contextAt(candles, 20)?.label).toBe('Both wicks rejected')
    candles[20] = bar(20, { low: 97, close: 98 })
    expect(contextAt(candles, 20)?.label).toBe('Down breakout')
    candles[20] = bar(20, { close: 101 })
    expect(contextAt(candles, 20)?.label).toBe('Inside range')
  })

  it('does not promote forming geometry to a confirmed breakout', () => {
    const candles = history()
    candles[20] = bar(20, { high: 103, close: 102, isClosed: false })
    expect(contextAt(candles, 20)).toBeNull()
    expect(contextAt(candles, 20, true)).toMatchObject({ label: 'Above range', isClosed: false })
    candles[20] = bar(20, { low: 97, close: 98, isClosed: false })
    expect(contextAt(candles, 20, true)?.label).toBe('Below range')
    candles[20] = bar(20, { high: 103, isClosed: false })
    expect(contextAt(candles, 20, true)?.description).toContain('Provisional')
    expect(getChartContexts(candles).at(-1)).toBeNull()
    expect(getChartContexts(candles, true).at(-1)?.isClosed).toBe(false)
  })

  it('rejects incomplete history, gaps, open prior bars and invalid candles', () => {
    expect(contextAt(history(), 19)).toBeNull()
    expect(contextAt(history(), -1)).toBeNull()
    expect(contextAt(history(), 1.5)).toBeNull()
    expect(contextAt(history(), 21)).toBeNull()
    for (const changed of [bar(10, { time: 601 }), bar(10, { isClosed: false }), bar(10, { low: 110 })]) {
      const candles = history()
      candles[10] = changed
      expect(contextAt(candles, 20, true)).toBeNull()
    }
    const candles = history()
    candles[20] = bar(20, { close: Number.NaN })
    expect(contextAt(candles, 20, true)).toBeNull()
  })

  it('calculates known monotonic CHOP from fourteen true ranges', () => {
    const candles = Array.from({ length: 21 }, (_, i) => bar(i, { open: 100 + i, high: 101 + i, low: 99 + i, close: 100 + i }))
    // TR=2 on every bar; latest fourteen highs/lows span 15 points.
    expect(contextAt(candles, 20)?.chop14).toBeCloseTo(100 * Math.log10(28 / 15) / Math.log10(14), 12)
  })

  it('reports undefined range location and CHOP for a constant series', () => {
    const candles = Array.from({ length: 21 }, (_, i) => bar(i, { high: 100, low: 100 }))
    expect(contextAt(candles, 20)).toMatchObject({ locationPct: null, chop14: null, label: 'Inside range' })
  })
})
