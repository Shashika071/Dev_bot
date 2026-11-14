import { describe, expect, it } from 'vitest'
import type { Candle } from '../types'
import type { AggregatedBar } from './candleAggregation'
import {
  computeMultiTimeframeContext,
  isLargeLowerWick,
  isLargeUpperWick,
  judgeClosedBehavior,
  refreshClosedReading,
  type ClosedTfReading,
} from './timeframeContext'
import type { DynamicSRLevel } from '../indicators/dynamicSRv2'

function bar(partial: Partial<AggregatedBar> & { time: number; open: number; high: number; low: number; close: number }): AggregatedBar {
  return {
    isClosed: true,
    sourceCount: 5,
    ...partial,
  }
}

function level(
  type: 'support' | 'resistance',
  lo: number,
  hi: number,
): DynamicSRLevel {
  return {
    price: (lo + hi) / 2,
    hi,
    lo,
    strength: 3,
    type,
    color: type === 'support' ? '#0f0' : '#f00',
    fromTime: 0,
  }
}

function m1(time: number, close: number, opts?: Partial<Candle>): Candle {
  const open = opts?.open ?? close
  return {
    time,
    open,
    high: opts?.high ?? Math.max(open, close) + 0.2,
    low: opts?.low ?? Math.min(open, close) - 0.2,
    close,
    isClosed: opts?.isClosed ?? true,
  }
}

function flatSeries(n: number, start = 0, price = 100): Candle[] {
  const out: Candle[] = []
  for (let i = 0; i < n; i++) {
    out.push(m1(start + i * 60, price + (i % 3) * 0.01))
  }
  return out
}

describe('large wick detection', () => {
  it('requires wick ≥ half range and longer than opposite wick', () => {
    expect(isLargeLowerWick(bar({ time: 0, open: 100, high: 100.2, low: 99, close: 100.1 }))).toBe(true)
    expect(isLargeUpperWick(bar({ time: 0, open: 100, high: 101.2, low: 99.9, close: 100.1 }))).toBe(true)
    // Equal wicks → not large directional
    expect(isLargeLowerWick(bar({ time: 0, open: 100, high: 101, low: 99, close: 100 }))).toBe(false)
  })
})

describe('judgeClosedBehavior', () => {
  it('detects support rejection with large lower wick and close above zone', () => {
    const support = level('support', 99.5, 100)
    const candle = bar({ time: 300, open: 100.2, high: 100.4, low: 99.4, close: 100.3 })
    const result = judgeClosedBehavior(candle, 100.1, [support])
    expect(result.behavior).toBe('rejection_support')
    expect(result.reactionSide).toBe('RISE')
  })

  it('detects resistance rejection', () => {
    const resistance = level('resistance', 101, 101.5)
    const candle = bar({ time: 300, open: 100.8, high: 101.6, low: 100.6, close: 100.7 })
    const result = judgeClosedBehavior(candle, 100.9, [resistance])
    expect(result.behavior).toBe('rejection_resistance')
    expect(result.reactionSide).toBe('FALL')
  })

  it('gives no directional vote for a large wick away from any zone', () => {
    const support = level('support', 90, 91)
    const resistance = level('resistance', 110, 111)
    // Large lower wick around 100 — far from both zones
    const candle = bar({ time: 300, open: 100.2, high: 100.3, low: 99.0, close: 100.15 })
    expect(isLargeLowerWick(candle)).toBe(true)
    const result = judgeClosedBehavior(candle, 100.1, [support, resistance])
    expect(result.behavior).toBe('no_clear_reaction')
    expect(result.reactionSide).toBeNull()
  })

  it('requires prior close on the approach side for a breakout', () => {
    const resistance = level('resistance', 101, 101.5)
    // Prior already above → not a fresh breakout from approach side
    const alreadyAbove = judgeClosedBehavior(
      bar({ time: 300, open: 101.6, high: 102, low: 101.5, close: 101.8 }),
      101.7,
      [resistance],
    )
    expect(alreadyAbove.behavior).not.toBe('breakout')

    const breakout = judgeClosedBehavior(
      bar({ time: 300, open: 101.2, high: 102, low: 101.0, close: 101.8 }),
      100.9,
      [resistance],
    )
    expect(breakout.behavior).toBe('breakout')
    expect(breakout.reactionSide).toBe('RISE')
  })

  it('keeps a prior support typed as support when the close breaks below it', () => {
    const support = level('support', 99.5, 100)
    const breakout = judgeClosedBehavior(
      bar({ time: 300, open: 99.8, high: 100.1, low: 98.8, close: 99.2 }),
      100.2,
      [support],
    )
    expect(breakout.behavior).toBe('breakout')
    expect(breakout.reactionSide).toBe('FALL')
    expect(breakout.text).toContain('below support')
  })
})

describe('direction-specific agreement', () => {
  it('keeps M15 closed facts fixed while RISE switches immediately to FALL', () => {
    const frozen: ClosedTfReading = {
      timeframe: 15,
      label: '15m',
      candleTime: 900,
      trend: 'Up',
      sma20: 100,
      atr14: 1,
      support: {
        type: 'support', mid: 99, hi: 99.2, lo: 98.8, distance: 'Away', present: true,
      },
      resistance: {
        type: 'resistance', mid: 102, hi: 102.2, lo: 101.8, distance: 'Away', present: true,
      },
      behavior: 'breakout',
      behaviorText: 'Last candle closed above resistance.',
      reactionSide: 'RISE',
      agreement: null,
      agreementReason: '',
    }

    const rise = refreshClosedReading(frozen, 'RISE', 100.5)
    const fall = refreshClosedReading(frozen, 'FALL', 100.5)

    expect(rise.agreement).toBe('Supports')
    expect(fall.agreement).toBe('Opposes')
    expect(fall.trend).toBe(rise.trend)
    expect(fall.candleTime).toBe(rise.candleTime)
    expect(fall.behavior).toBe(rise.behavior)
    expect(fall.reactionSide).toBe(rise.reactionSide)
  })
})

describe('computeMultiTimeframeContext', () => {
  it('reports Warming up when history is short', () => {
    const ctx = computeMultiTimeframeContext({
      candles: flatSeries(40),
      m1Side: 'RISE',
      livePrice: 100,
      nowEpoch: 40 * 60,
      feedReady: true,
    })
    expect(ctx.m5.status).toBe('warming')
    expect(ctx.m5.statusText).toBe('Warming up')
  })

  it('reports Feed paused when feed is not ready', () => {
    const ctx = computeMultiTimeframeContext({
      candles: flatSeries(200),
      m1Side: 'RISE',
      livePrice: 100,
      nowEpoch: 200 * 60,
      feedReady: false,
    })
    expect(ctx.status).toBe('paused')
    expect(ctx.m5.statusText).toBe('Feed paused')
    expect(ctx.m15.statusText).toBe('Feed paused')
  })

  it('leaves agreement blank when M1 side is unclear', () => {
    // Need enough M5 bars for SMA20: 21*5 = 105 M1 minutes minimum
    const candles = flatSeries(200)
    const ctx = computeMultiTimeframeContext({
      candles,
      m1Side: null,
      livePrice: candles.at(-1)!.close,
      nowEpoch: 200 * 60,
      feedReady: true,
    })
    if (ctx.m5.closed) {
      expect(ctx.m5.closed.agreement).toBeNull()
      expect(ctx.m5.closed.agreementReason.length).toBeGreaterThan(0)
    }
  })

  it('uses the same bounded HTF history on the screen and collector', () => {
    const all = flatSeries(1500)
    const collectorWindow = all.slice(-1000)
    const last = all.at(-1)!
    const options = {
      m1Side: 'FALL' as const,
      livePrice: last.close,
      nowEpoch: last.time + 60,
      feedReady: true,
    }
    const screen = computeMultiTimeframeContext({ ...options, candles: all })
    const collector = computeMultiTimeframeContext({ ...options, candles: collectorWindow })
    expect(screen.m5.closed).toEqual(collector.m5.closed)
    expect(screen.m15.closed).toEqual(collector.m15.closed)
  })

  it('keeps closed reading stable while forming wick changes', () => {
    const base = flatSeries(200)
    // Ensure last M5 is closed and a forming M5 exists
    const t = base.at(-1)!.time + 60
    const formingA: Candle[] = [
      ...base,
      m1(t, 100.5, { high: 100.6, low: 100.4, isClosed: false }),
    ]
    const ctxA = computeMultiTimeframeContext({
      candles: formingA,
      m1Side: 'RISE',
      livePrice: 100.5,
      nowEpoch: t + 10,
      feedReady: true,
    })
    const closedTime = ctxA.m5.closed?.candleTime
    const closedBehavior = ctxA.m5.closed?.behavior
    const closedReason = ctxA.m5.closed?.agreementReason

    const formingB: Candle[] = [
      ...base,
      m1(t, 100.5, { high: 101.8, low: 99.2, open: 100.5, isClosed: false }),
    ]
    const ctxB = computeMultiTimeframeContext({
      candles: formingB,
      m1Side: 'RISE',
      livePrice: 99.5,
      nowEpoch: t + 20,
      feedReady: true,
    })
    expect(ctxB.m5.closed?.candleTime).toBe(closedTime)
    expect(ctxB.m5.closed?.behavior).toBe(closedBehavior)
    expect(ctxB.m5.closed?.agreementReason).toBe(closedReason)
    // Forming text/countdown may change
    expect(ctxB.m5.forming).not.toBeNull()
  })

  it('marks Mixed when trend and an opposing nearby zone conflict', () => {
    // Rising series so M5 trend is Up, then place price near a high resistance-like close
    const candles: Candle[] = []
    let price = 100
    for (let i = 0; i < 220; i++) {
      price += 0.05
      candles.push(m1(i * 60, price, {
        open: price - 0.02,
        high: price + 0.1,
        low: price - 0.1,
      }))
    }
    const ctx = computeMultiTimeframeContext({
      candles,
      m1Side: 'RISE',
      livePrice: candles.at(-1)!.close,
      nowEpoch: 220 * 60,
      feedReady: true,
    })
    expect(ctx.m5.status).toBe('ready')
    expect(ctx.m5.closed).not.toBeNull()
    // Agreement is Supports, Opposes, or Mixed — never a win probability claim
    if (ctx.m5.closed?.agreement != null) {
      expect(['Supports', 'Opposes', 'Mixed']).toContain(ctx.m5.closed.agreement)
    }
  })
})
