import { describe, expect, it } from 'vitest'
import type { Candle } from '../../types'
import { enrich } from '../../strategy/signalEngine'
import { buildResearchExtras } from './buildExtras'
import { evalS2, evalS5, evalS6, evalS7, threeReturnSide } from './patternStrategies'
import type { StrategyContext } from './types'

function ctxFrom(candles: Candle[]): StrategyContext {
  const closed = candles.map((c) => ({ ...c, isClosed: true }))
  return {
    candles: closed,
    closedCount: closed.length,
    index: closed.length - 1,
    series: enrich(closed),
    extras: buildResearchExtras(closed),
    feedHealthy: true,
    nowEpoch: closed[closed.length - 1]!.time + 60,
  }
}

function candle(time: number, o: number, h: number, l: number, c: number): Candle {
  return { time, open: o, high: h, low: l, close: c, isClosed: true }
}

describe('S2/S5 prior range excludes signal candle', () => {
  it('S2 breakout uses previous 20 highs/lows only', () => {
    const start = 1_700_000_040
    const candles: Candle[] = []
    for (let i = 0; i < 25; i++) {
      candles.push(candle(start + i * 60, 100, 101, 99, 100))
    }
    // prior high max is 101; signal closes at 102 → RISE
    candles[24] = candle(start + 24 * 60, 100, 103, 99, 102)
    const hit = evalS2(ctxFrom(candles))
    expect(hit?.side).toBe('RISE')
  })

  it('S5 rejects zero-body candles', () => {
    const start = 1_700_000_040
    const candles: Candle[] = []
    for (let i = 0; i < 25; i++) {
      candles.push(candle(start + i * 60, 100, 101, 99, 100.5))
    }
    candles[24] = candle(start + 24 * 60, 100, 100, 100, 100)
    expect(evalS5(ctxFrom(candles))).toBeNull()
  })
})

describe('S6/S7 paired opportunities', () => {
  it('S7 is opposite of S6 on the same streak', () => {
    const start = 1_700_000_040
    const candles: Candle[] = []
    let px = 100
    for (let i = 0; i < 10; i++) {
      const open = px
      px += 1
      candles.push(candle(start + i * 60, open, px + 0.2, open - 0.2, px))
    }
    const c = ctxFrom(candles)
    expect(threeReturnSide(c)).toBe('RISE')
    expect(evalS6(c)?.side).toBe('RISE')
    expect(evalS7(c)?.side).toBe('FALL')
  })
})
