import { describe, expect, it } from 'vitest'
import {
  appendMilestone,
  emptyJournal,
  journalCandleTime,
  markUnanswered,
  setupKey,
  summarizeJournal,
  type MilestoneSnapshot,
  type TfSnapshot,
} from './decisionJournal'

const emptyTf = (tf: 5 | 15): TfSnapshot => ({
  timeframe: tf,
  trend: 'Up',
  support: { mid: 99, hi: 99.2, lo: 98.8, distance: 'Near' },
  resistance: { mid: 101, hi: 101.2, lo: 100.8, distance: 'Away' },
  behavior: 'rejection_support',
  behaviorText: 'Last candle rejected support.',
  agreement: 'Supports',
  agreementReason: 'Uptrend. Last candle rejected support.',
  candleTime: 900,
  forming: true,
})

function snap(kind: MilestoneSnapshot['kind'], over: Partial<MilestoneSnapshot> = {}): MilestoneSnapshot {
  return {
    kind,
    frozenAtEpoch: 1000,
    receiptEpoch: 1000,
    price: 100.5,
    m1: {
      side: 'RISE',
      candleTime: 960,
      passed: kind === 'near_3_4' ? 3 : 4,
      total: 4,
      forming: kind !== 'qualify_4_4_confirmed',
      state: kind === 'near_3_4' ? 'WATCHING' : kind === 'qualify_4_4_confirmed' ? 'CONFIRMED' : 'FORMING',
      flags: { ut: true, supertrend: true, sma: true, rsi: kind !== 'near_3_4', ao: false },
      missingCondition: kind === 'near_3_4' ? 'RSI' : null,
    },
    m5: emptyTf(5),
    m15: { ...emptyTf(15), agreement: 'Mixed', agreementReason: 'Uptrend, but price is near resistance.' },
    ...over,
  }
}

describe('decision journal', () => {
  it('keeps a confirmed signal under its original candle after live setup advances', () => {
    expect(journalCandleTime('CONFIRMED', 960, 1020)).toBe(960)
    expect(journalCandleTime('FORMING', 1020, 1020)).toBe(1020)
    expect(journalCandleTime('WAIT', null, 1080)).toBe(1080)
  })

  it('freezes the first 3/4 snapshot and does not rewrite it', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('near_3_4', { price: 100.5 }))
    const first = state.setups[0]!.milestones[0]!
    state = appendMilestone(state, snap('near_3_4', { price: 999, frozenAtEpoch: 2000 }))
    expect(state.setups).toHaveLength(1)
    expect(state.setups[0]!.milestones).toHaveLength(1)
    expect(state.setups[0]!.milestones[0]!.price).toBe(100.5)
    expect(state.setups[0]!.milestones[0]).toEqual(first)
  })

  it('links 3/4, 4/4, skip and buy to the same setup key', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('near_3_4'))
    state = appendMilestone(state, snap('qualify_4_4_forming'))
    state = appendMilestone(state, snap('skip', { skipReason: 'M15 resistance' }))
    expect(state.setups).toHaveLength(1)
    expect(state.setups[0]!.id).toBe(setupKey(960, 'RISE'))
    expect(state.setups[0]!.disposition).toBe('skipped')
    expect(state.setups[0]!.milestones.map((m) => m.kind)).toEqual([
      'near_3_4', 'qualify_4_4_forming', 'skip',
    ])
  })

  it('marks unresolved prior setups as unanswered on candle rollover', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('near_3_4'))
    state = markUnanswered(state, 1020)
    expect(state.setups[0]!.disposition).toBe('unanswered')
    const summary = summarizeJournal(state)
    expect(summary.unanswered).toBe(1)
    expect(summary.taken).toBe(0)
  })

  it('records buy economics and settlement profit separately from paper', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('manual_click'))
    state = appendMilestone(state, snap('buy', {
      execution: {
        contractId: 42, entrySpot: 100.4, barrier: '+0.05',
        buyPrice: 1, payout: 1.85, profit: null, status: 'open', currency: 'USD',
      },
    }))
    state = appendMilestone(state, snap('settlement', {
      execution: {
        contractId: 42, entrySpot: 100.4, barrier: '+0.05',
        buyPrice: 1, payout: 1.85, profit: 0.85, status: 'sold', currency: 'USD',
      },
    }))
    expect(state.setups[0]!.disposition).toBe('taken')
    const summary = summarizeJournal(state)
    expect(summary.taken).toBe(1)
    expect(summary.settledPnl).toBeCloseTo(0.85)
    expect(summary.pendingContracts).toBe(0)
  })

  it('does not count a rejected manual click as taken', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('manual_click', { attemptId: 'attempt-rejected' }))
    state = appendMilestone(state, snap('manual_rejected', {
      attemptId: 'attempt-rejected',
      frozenAtEpoch: 1001,
    }))
    expect(state.setups[0]!.disposition).toBe('open')
    expect(summarizeJournal(state).taken).toBe(0)
  })

  it('routes settlement to the original click by attempt and counts its P/L once', () => {
    let state = emptyJournal()
    state = appendMilestone(state, snap('manual_click', { attemptId: 'attempt-9m' }))
    const laterM1 = {
      ...snap('buy').m1,
      side: 'FALL' as const,
      candleTime: 1020,
    }
    state = appendMilestone(state, snap('buy', {
      attemptId: 'attempt-9m',
      m1: laterM1,
      execution: {
        contractId: 9001, entrySpot: 100.4, barrier: '+0.01',
        buyPrice: 1, payout: 1.85, profit: null, status: 'open', currency: 'USD',
      },
    }))
    const settlement = snap('settlement', {
      attemptId: 'attempt-9m',
      m1: laterM1,
      execution: {
        contractId: 9001, entrySpot: 100.4, barrier: '+0.01',
        buyPrice: 1, payout: 1.85, profit: 0.85, status: 'won', currency: 'USD',
      },
    })
    state = appendMilestone(state, settlement)
    state = appendMilestone(state, settlement)

    expect(state.setups).toHaveLength(1)
    expect(state.setups[0]!.id).toBe(setupKey(960, 'RISE'))
    expect(state.setups[0]!.milestones.filter((m) => m.kind === 'settlement')).toHaveLength(1)
    expect(summarizeJournal(state).settledPnl).toBe(0.85)
  })
})
