import { describe, expect, it } from 'vitest'
import { computeSetupChartMarkers } from './liveConfidence'
import { fixtureCandles } from '../testFixtures'

describe('computeSetupChartMarkers (C5)', () => {
  it('emits only full 4/4 C5 markers; old minConfidence arg cannot weaken rules', () => {
    const candles = fixtureCandles(400)
    // Even with a low threshold arg, markers are still full C5 only.
    const markers = computeSetupChartMarkers(candles, 0.01, 400)
    for (const m of markers) {
      expect(m.strategyId).toBe('C5')
      expect(m.full).toBe(true)
      expect(m.passed).toBe(4)
      expect(m.total).toBe(4)
      expect(m.confidence).toBe(1)
      expect(m.side === 'RISE' || m.side === 'FALL').toBe(true)
    }
  })
})
