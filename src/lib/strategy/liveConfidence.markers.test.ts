import { describe, expect, it } from 'vitest'
import { computeSetupChartMarkers } from './liveConfidence'
import { fixtureCandles } from '../testFixtures'

describe('computeSetupChartMarkers', () => {
  it('returns markers only at/above min confidence and tags full vs partial', () => {
    const candles = fixtureCandles(200)
    const markers = computeSetupChartMarkers(candles, 0.8, 200)
    for (const m of markers) {
      expect(m.confidence).toBeGreaterThanOrEqual(0.8 - 1e-9)
      expect(m.full).toBe(m.passed >= m.total)
      if (m.full) expect(m.confidence).toBe(1)
    }
  })
})
