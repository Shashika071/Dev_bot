import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import {
  aggregateClosedBars,
  aggregateThroughLive,
  formatHtfCountdown,
  htfBucket,
  secondsUntilHtfClose,
} from './candleAggregation'

function m1(time: number, close: number, opts?: Partial<Candle>): Candle {
  const open = opts?.open ?? close
  const high = opts?.high ?? Math.max(open, close) + 0.1
  const low = opts?.low ?? Math.min(open, close) - 0.1
  return {
    time,
    open,
    high,
    low,
    close,
    isClosed: opts?.isClosed ?? true,
  }
}

/** Contiguous M1 from a UTC-aligned start. */
function series(start: number, n: number, base = 100): Candle[] {
  const out: Candle[] = []
  for (let i = 0; i < n; i++) {
    out.push(m1(start + i * 60, base + i * 0.01))
  }
  return out
}

describe('htfBucket', () => {
  it('aligns M5 and M15 to UTC boundaries', () => {
    // 1970-01-01 00:03 → M5 open 00:00, M15 open 00:00
    expect(htfBucket(180, 5)).toBe(0)
    expect(htfBucket(180, 15)).toBe(0)
    // 00:05 → M5 open 00:05
    expect(htfBucket(300, 5)).toBe(300)
    // 00:14 → M15 still 00:00
    expect(htfBucket(14 * 60, 15)).toBe(0)
    // 00:15 → M15 open 00:15
    expect(htfBucket(15 * 60, 15)).toBe(900)
  })
})

describe('aggregateClosedBars', () => {
  it('emits only complete contiguous M5 buckets', () => {
    // Start at a clean M5 boundary with 12 minutes → 2 closed M5
    const bars = series(0, 12)
    const m5 = aggregateClosedBars(bars, 5)
    expect(m5).toHaveLength(2)
    expect(m5[0]!.time).toBe(0)
    expect(m5[0]!.isClosed).toBe(true)
    expect(m5[0]!.sourceCount).toBe(5)
    expect(m5[0]!.open).toBe(bars[0]!.open)
    expect(m5[0]!.close).toBe(bars[4]!.close)
    expect(m5[1]!.time).toBe(300)
  })

  it('skips incomplete M5 when a minute is missing', () => {
    const bars = series(0, 10)
    // Drop minute 3 (180) inside first M5
    const gapped = bars.filter((c) => c.time !== 180)
    // closedSegments resets at the gap, so trailing segment starts at 240
    const m5 = aggregateClosedBars(gapped, 5)
    // Trailing contiguous: 240..540 = 6 bars → only one full M5 at 300 if 300..540 present
    // 300,360,420,480,540 = 5 bars → one M5 at 300
    expect(m5.every((b) => b.time !== 0)).toBe(true)
    expect(m5.some((b) => b.time === 300)).toBe(true)
  })

  it('emits complete M15 bars only', () => {
    const bars = series(0, 45)
    const m15 = aggregateClosedBars(bars, 15)
    expect(m15).toHaveLength(3)
    expect(m15[0]!.time).toBe(0)
    expect(m15[0]!.sourceCount).toBe(15)
    expect(m15[1]!.time).toBe(900)
    expect(m15[2]!.time).toBe(1800)
  })

  it('ignores a trailing forming M1 for closed aggregation', () => {
    const bars = [...series(0, 5), m1(300, 101, { isClosed: false })]
    const m5 = aggregateClosedBars(bars, 5)
    expect(m5).toHaveLength(1)
    expect(m5[0]!.time).toBe(0)
  })
})

describe('aggregateThroughLive', () => {
  it('exposes a forming M5 while minutes accumulate', () => {
    const closed = series(0, 5)
    const formingMins = [
      m1(300, 101),
      m1(360, 102),
      m1(420, 103, { isClosed: false }),
    ]
    const { completed, forming } = aggregateThroughLive([...closed, ...formingMins], 5)
    expect(completed).toHaveLength(1)
    expect(forming).not.toBeNull()
    expect(forming!.time).toBe(300)
    expect(forming!.isClosed).toBe(false)
    expect(forming!.sourceCount).toBe(3)
    expect(forming!.high).toBeGreaterThanOrEqual(forming!.close)
  })

  it('does not treat a fully closed bucket as forming', () => {
    const bars = series(0, 10)
    const { completed, forming } = aggregateThroughLive(bars, 5)
    expect(completed).toHaveLength(2)
    expect(forming).toBeNull()
  })
})

describe('countdown helpers', () => {
  it('counts seconds until HTF close', () => {
    expect(secondsUntilHtfClose(0, 5, 120)).toBe(180)
    expect(secondsUntilHtfClose(0, 15, 60)).toBe(840)
    expect(formatHtfCountdown(252)).toBe('4m 12s')
    expect(formatHtfCountdown(0)).toBe('0m 00s')
  })
})
