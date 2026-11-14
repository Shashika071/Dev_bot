import { describe, expect, it } from 'vitest'
import { formatBreakEven, quoteEconomicsFromProposal } from './quoteEconomics'

describe('quote economics', () => {
  it('computes gain, loss and break-even without forecasting', () => {
    const q = quoteEconomicsFromProposal({
      ask: 1, payout: 1.85, barrier: '+0.05', responseEpoch: 1000, nowSec: 1001,
    })
    expect(q.available).toBe(true)
    if (!q.available) return
    expect(q.winGain).toBeCloseTo(0.85)
    expect(q.maxLoss).toBe(1)
    expect(q.breakEven).toBeCloseTo(1 / 1.85)
    expect(formatBreakEven(q.breakEven)).toContain('%')
  })

  it('returns Quote unavailable when stale or missing', () => {
    expect(quoteEconomicsFromProposal({
      ask: 1, payout: 1.85, barrier: '+0.05', responseEpoch: 1000, nowSec: 1010,
    })).toEqual({ available: false, reason: 'Quote unavailable' })
    expect(quoteEconomicsFromProposal({
      ask: null, payout: 1.85, barrier: '+0.05', responseEpoch: 1000, nowSec: 1001,
    })).toEqual({ available: false, reason: 'Quote unavailable' })
  })
})
