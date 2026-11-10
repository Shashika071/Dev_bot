import { describe, expect, it } from 'vitest'
import { assertRegistryComplete, STRATEGY_REGISTRY } from './registry'
import { fixtureCandles } from '../../testFixtures'
import { enrich } from '../../strategy/signalEngine'
import { buildResearchExtras } from './buildExtras'
import type { StrategyContext } from './types'

describe('strategy registry', () => {
  it('has exactly 8 unique R1–R8 IDs', () => {
    assertRegistryComplete()
    expect(STRATEGY_REGISTRY.map((s) => s.id)).toEqual([
      'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8',
    ])
  })

  it('all strategies evaluate completed candles only', () => {
    for (const s of STRATEGY_REGISTRY) {
      expect(s.usesFormingCandle).toBe(false)
    }
  })

  it('evaluators run without throwing on fixture history', () => {
    const candles = fixtureCandles(400).map((c) => ({ ...c, isClosed: true }))
    const series = enrich(candles)
    const extras = buildResearchExtras(candles)
    const index = candles.length - 1
    const ctx: StrategyContext = {
      candles, closedCount: candles.length, index, series, extras,
      feedHealthy: true, nowEpoch: candles[index]!.time + 60,
    }
    for (const s of STRATEGY_REGISTRY) {
      expect(() => s.evaluate(ctx)).not.toThrow()
    }
  })
})
