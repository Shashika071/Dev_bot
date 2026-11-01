import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import { computeLiveSetupConfidence } from './liveConfidence'

function candles(n: number, close = 100): Candle[] {
  const out: Candle[] = []
  // Must be minute-aligned (time % 60 === 0) for closedSegments validation.
  let t = 1_700_000_040
  let c = close
  for (let i = 0; i < n; i++) {
    const open = c
    c = c + (i % 3 === 0 ? 0.2 : -0.05)
    out.push({
      time: t,
      open,
      high: Math.max(open, c) + 0.1,
      low: Math.min(open, c) - 0.1,
      close: c,
      isClosed: i < n - 1,
    })
    t += 60
  }
  return out
}

describe('computeLiveSetupConfidence', () => {
  it('returns warmup empty state for short history', () => {
    const live = computeLiveSetupConfidence(candles(10))
    expect(live.ready).toBe(false)
    expect(live.rise.confidence).toBe(0)
    expect(live.fall.confidence).toBe(0)
  })

  it('scores forming candle in real time with 0–5 conditions', () => {
    const live = computeLiveSetupConfidence(candles(120))
    expect(live.ready).toBe(true)
    expect(live.forming).toBe(true)
    expect(live.rise.total).toBe(5)
    expect(live.fall.total).toBe(5)
    expect(live.rise.passed).toBeGreaterThanOrEqual(0)
    expect(live.rise.passed).toBeLessThanOrEqual(5)
    expect(live.rise.confidence).toBe(live.rise.passed / 5)
  })
})
