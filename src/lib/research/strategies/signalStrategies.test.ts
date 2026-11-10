import { describe, expect, it } from 'vitest'
import { fixtureCandles } from '../../testFixtures'
import { enrich } from '../../strategy/signalEngine'
import { buildResearchExtras } from './buildExtras'
import type { StrategyContext } from './types'
import {
  ema50Confirms,
  evalR1,
  evalR2,
  supertrendFlipSide,
  utSignalSide,
  wickSignalSide,
} from './signalStrategies'

function ctxAt(index: number): StrategyContext {
  const candles = fixtureCandles(300).map((c) => ({ ...c, isClosed: true }))
  const series = enrich(candles)
  const extras = buildResearchExtras(candles)
  return {
    candles,
    closedCount: candles.length,
    index,
    series,
    extras,
    feedHealthy: true,
    nowEpoch: candles[index]!.time + 60,
  }
}

describe('signalStrategies helpers', () => {
  it('scans fixture history for UT / ST / wick without throwing', () => {
    const ctx = ctxAt(299)
    for (let i = 60; i < ctx.candles.length; i++) {
      const c = { ...ctx, index: i }
      expect(() => utSignalSide(c)).not.toThrow()
      expect(() => supertrendFlipSide(c)).not.toThrow()
      expect(() => wickSignalSide(c)).not.toThrow()
      expect(() => ema50Confirms(c, 'RISE')).not.toThrow()
      expect(() => evalR1(c)).not.toThrow()
      expect(() => evalR2(c)).not.toThrow()
    }
  })

  it('R1 only fires on Supertrend direction flip', () => {
    const base = ctxAt(200)
    let found = false
    for (let i = 60; i < base.candles.length; i++) {
      const c = { ...base, index: i }
      const flip = supertrendFlipSide(c)
      const hit = evalR1(c)
      if (flip) {
        found = true
        expect(hit?.side).toBe(flip)
      } else {
        expect(hit).toBeNull()
      }
    }
    // Fixture should contain at least one flip over 300 bars.
    expect(found).toBe(true)
  })

  it('R2 only fires on fresh UT buy/sell', () => {
    const base = ctxAt(200)
    for (let i = 60; i < base.candles.length; i++) {
      const c = { ...base, index: i }
      const ut = utSignalSide(c)
      const hit = evalR2(c)
      if (ut) expect(hit?.side).toBe(ut)
      else expect(hit).toBeNull()
    }
  })
})
