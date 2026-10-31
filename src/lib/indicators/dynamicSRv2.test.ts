import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import { computeDynamicSRv2, DYNAMIC_SR_V2_DEFAULTS } from './dynamicSRv2'

function makeCandles(n: number): Candle[] {
  const out: Candle[] = []
  let t = 1_700_000_000
  let price = 100
  for (let i = 0; i < n; i++) {
    // Create occasional swing highs/lows so pivots form
    const swing = i % 21 === 10 ? 8 : i % 21 === 0 ? -6 : Math.sin(i / 3) * 0.8
    const open = price
    const close = price + swing * 0.15
    const high = Math.max(open, close) + Math.abs(swing) * 0.2 + 0.2
    const low = Math.min(open, close) - Math.abs(swing) * 0.2 - 0.2
    out.push({ time: t, open, high, low, close, isClosed: true })
    price = close
    t += 60
  }
  return out
}

describe('Dynamic S/R v2', () => {
  it('uses project defaults matching user settings', () => {
    expect(DYNAMIC_SR_V2_DEFAULTS).toMatchObject({
      pivotPeriod: 7,
      source: 'High/Low',
      maxPivots: 30,
      channelWidthPct: 5,
      maxSR: 4,
      minStrength: 3,
      labelLocation: 20,
      lineStyle: 'Solid',
      lineWidth: 2,
      showPivotPoints: false,
    })
  })

  it('returns at most maxSR levels with strength >= minStrength', () => {
    const { levels } = computeDynamicSRv2(makeCandles(400))
    expect(levels.length).toBeLessThanOrEqual(DYNAMIC_SR_V2_DEFAULTS.maxSR)
    for (const lvl of levels) {
      expect(lvl.strength).toBeGreaterThanOrEqual(DYNAMIC_SR_V2_DEFAULTS.minStrength)
      expect(lvl.type === 'support' || lvl.type === 'resistance').toBe(true)
      expect(lvl.price).toBeGreaterThan(0)
    }
  })

  it('colors resistance above close and support below', () => {
    const candles = makeCandles(400)
    const close = candles.at(-1)!.close
    const { levels } = computeDynamicSRv2(candles)
    for (const lvl of levels) {
      if (lvl.price >= close) {
        expect(lvl.type).toBe('resistance')
        expect(lvl.color).toBe(DYNAMIC_SR_V2_DEFAULTS.resistanceColor)
      } else {
        expect(lvl.type).toBe('support')
        expect(lvl.color).toBe(DYNAMIC_SR_V2_DEFAULTS.supportColor)
      }
    }
  })

  it('does not look ahead: last prd bars cannot create a new confirmed pivot', () => {
    const candles = makeCandles(120)
    const prd = DYNAMIC_SR_V2_DEFAULTS.pivotPeriod
    const base = computeDynamicSRv2(candles)
    const spiked = candles.map((c, i) =>
      i === candles.length - 1 ? { ...c, high: 1e9, low: c.low } : c,
    )
    const next = computeDynamicSRv2(spiked)
    // Unconfirmed spike on last bar must not invent a brand-new resistance at 1e9
    expect(next.levels.some((l) => Math.abs(l.price - 1e9) < 1)).toBe(false)
    expect(next.pivots.some((p) => p.time === spiked.at(-1)!.time)).toBe(false)
    void base
    void prd
  })
})
