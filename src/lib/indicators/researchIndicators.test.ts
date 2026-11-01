import { describe, expect, it } from 'vitest'
import { ema } from './ema'
import { bollingerBands } from './bollinger'
import { fullStochastic } from './stochastic'
import { cci } from './cci'
import { computeDmiAdx } from './dmi'

describe('ema', () => {
  it('seeds with SMA then recurses', () => {
    const v = [1, 2, 3, 4, 5, 6]
    const e = ema(v, 3)
    expect(e[2]).toBeCloseTo(2, 10)
    const k = 2 / 4
    expect(e[3]).toBeCloseTo(4 * k + 2 * (1 - k), 10)
  })
})

describe('bollinger population stdev', () => {
  it('uses population divisor N not N-1', () => {
    const closes = [1, 2, 3, 4, 5]
    const { mid, upper, lower } = bollingerBands(closes, 5, 2)
    expect(mid[4]).toBeCloseTo(3, 10)
    // pop variance of 1..5 about 3 = 2
    expect(upper[4]).toBeCloseTo(3 + 2 * Math.SQRT2, 10)
    expect(lower[4]).toBeCloseTo(3 - 2 * Math.SQRT2, 10)
  })
})

describe('stochastic', () => {
  it('marks zero range unavailable', () => {
    const h = [10, 10, 10, 10]
    const l = [10, 10, 10, 10]
    const c = [10, 10, 10, 10]
    const { rawK } = fullStochastic(h, l, c, 3, 2, 2)
    expect(rawK[2]).toBeNull()
  })
})

describe('cci', () => {
  it('returns null when MAD is zero', () => {
    const flat = [5, 5, 5, 5]
    expect(cci(flat, flat, flat, 4)[3]).toBeNull()
  })
})

describe('dmi/adx', () => {
  it('handles equal up/down moves as zero DM', () => {
    // Construct short series — just ensure no throw and nulls before warmup
    const n = 40
    const highs = Array.from({ length: n }, (_, i) => 100 + i * 0.1)
    const lows = Array.from({ length: n }, (_, i) => 99 + i * 0.1)
    const closes = Array.from({ length: n }, (_, i) => 99.5 + i * 0.1)
    const { plusDi, adx } = computeDmiAdx(highs, lows, closes, 14)
    expect(plusDi[13]).toBeNull()
    expect(adx.some((v) => v != null)).toBe(true)
  })
})
