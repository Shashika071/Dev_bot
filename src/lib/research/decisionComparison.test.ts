import { describe, expect, it } from 'vitest'
import { setupKey, type DecisionJournalState, type MilestoneSnapshot } from '../journal/decisionJournal'
import { compareC5Decisions, type C5Baseline } from './decisionComparison'

function skipSnapshot(): MilestoneSnapshot {
  const tf = (timeframe: 5 | 15) => ({
    timeframe,
    trend: 'Down' as const,
    support: null,
    resistance: null,
    behavior: 'no_clear_reaction' as const,
    behaviorText: 'No clear reaction.',
    agreement: 'Opposes' as const,
    agreementReason: 'M15 resistance',
    candleTime: 900,
    forming: false,
  })
  return {
    kind: 'skip',
    frozenAtEpoch: 1_001,
    receiptEpoch: 1_001,
    price: 100,
    skipReason: 'RISE near M15 resistance',
    m1: {
      side: 'RISE',
      candleTime: 960,
      passed: 4,
      total: 4,
      forming: false,
      state: 'CONFIRMED',
      flags: { ut: true, supertrend: true, sma: true, rsi: true, ao: false },
      missingCondition: null,
    },
    m5: tf(5),
    m15: tf(15),
  }
}

describe('compareC5Decisions', () => {
  it('matches a browser skip to the research candle key and reports an avoided miss', () => {
    const snapshot = skipSnapshot()
    const id = setupKey(snapshot.m1.candleTime, snapshot.m1.side)
    const journal: DecisionJournalState = {
      version: 2,
      storageError: false,
      setups: [{
        id,
        side: 'RISE',
        candleTime: 960,
        createdAt: 1_001,
        disposition: 'skipped',
        legacy: false,
        milestones: [snapshot],
      }],
    }
    const baselines: C5Baseline[] = [{
      id,
      quoted: 'miss',
      paperPnl: -1,
      resolvedAt: 1_500,
    }]

    const result = compareC5Decisions(baselines, journal)
    expect(result.unanswered).toBe(0)
    expect(result.groups[0]).toMatchObject({
      reason: 'RISE near M15 resistance',
      taken: false,
      setups: 1,
      misses: 1,
      paperLosses: 1,
    })
  })
})
