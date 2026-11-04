import { describe, expect, it } from 'vitest'
import { assertRegistryComplete, STRATEGY_REGISTRY } from './registry'
import { MASKS } from './maskStrategies'
import { fixtureCandles } from '../../testFixtures'
import { enrich } from '../../strategy/signalEngine'
import { buildResearchExtras } from './buildExtras'
import type { StrategyContext } from './types'

describe('strategy registry', () => {
  it('has exactly 23 unique IDs covering B0..N2 and H1', () => {
    assertRegistryComplete()
    expect(STRATEGY_REGISTRY.map((s) => s.id).sort()).toEqual([
      'B0', 'B1',
      'C1', 'C2', 'C3', 'C4', 'C5', 'C6',
      'H1',
      'N1', 'N2',
      'O1', 'O2',
      'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7',
      'T1', 'T2', 'T3',
    ].sort())
  })

  it('pairs S6/S7 and T1/T2', () => {
    expect(STRATEGY_REGISTRY.find((s) => s.id === 'S6')!.pairedWith).toBe('S7')
    expect(STRATEGY_REGISTRY.find((s) => s.id === 'S7')!.pairedWith).toBe('S6')
    expect(STRATEGY_REGISTRY.find((s) => s.id === 'T1')!.pairedWith).toBe('T2')
    expect(STRATEGY_REGISTRY.find((s) => s.id === 'T2')!.pairedWith).toBe('T1')
  })

  it('C2 mask ignores UT (does not require UT false)', () => {
    expect(MASKS.C2).not.toContain('ut')
    expect(MASKS.C2).toEqual(['supertrend', 'sma', 'ao', 'rsi'])
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
