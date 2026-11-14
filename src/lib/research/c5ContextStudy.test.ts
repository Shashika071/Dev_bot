import { describe, expect, it } from 'vitest'
import { c5StudyContract, DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import { maybeStartTouchPaper } from './touchPaper'
import {
  advanceC5TouchPaper,
  attachC5ProposalQuote,
  c5ContextId,
  summarizeC5CohortBundle,
  type C5ContextObservation,
} from './c5ContextStudy'

function baseObs(agreementM5: 'Supports' | 'Opposes' | 'Mixed' | null, agreementM15: 'Supports' | 'Opposes' | null): C5ContextObservation {
  const tick = { epoch: 1_700_000_100, quote: 100.05, symbol: 'R_100' }
  let obs: C5ContextObservation = {
    id: c5ContextId(1_700_000_040, 'RISE'),
    side: 'RISE',
    candleTime: 1_700_000_040,
    signalEpoch: 1_700_000_100,
    receiptEpoch: 1_700_000_100,
    entryReferencePrice: 100.05,
    entryTick: tick,
    reason: 'test',
    passed: 4,
    total: 4,
    confirmations: { ut: true, supertrend: true, sma: true, ao: false, rsi: true },
    m5: {
      timeframe: 5,
      trend: 'Up',
      support: null,
      resistance: null,
      behavior: 'inside_range',
      behaviorText: 'x',
      agreement: agreementM5,
      agreementReason: 'm5',
      candleTime: 1_700_000_000,
      forming: false,
    },
    m15: {
      timeframe: 15,
      trend: 'Up',
      support: null,
      resistance: null,
      behavior: 'inside_range',
      behaviorText: 'x',
      agreement: agreementM15,
      agreementReason: 'm15',
      candleTime: 1_699_999_800,
      forming: false,
    },
    lastTickEpoch: tick.epoch,
  }
  const withTouch = maybeStartTouchPaper(obs as never, DEFAULT_FROZEN_CONTRACT, tick)
  obs = { ...obs, touch: withTouch.touch }
  return obs
}

describe('c5ContextStudy', () => {
  it('uses the manual ±0.01 nine-minute contract independently of discovery defaults', () => {
    const contract = c5StudyContract(DEFAULT_FROZEN_CONTRACT)
    const obs = baseObs('Supports', 'Supports')
    const restarted = maybeStartTouchPaper(
      { ...obs, touch: undefined } as never,
      contract,
      obs.entryTick,
    )
    expect(contract).toMatchObject({ barrierAbs: 0.01, duration: 9, durationUnit: 'm' })
    expect(restarted.touch).toMatchObject({ barrierAbs: 0.01, durationMin: 9 })
  })

  it('builds cohort overlaps and exclusion metrics', () => {
    const rows = [
      { ...baseObs('Supports', 'Supports'), touch: { ...baseObs('Supports', 'Supports').touch!, status: 'hit' as const } },
      { ...baseObs('Supports', 'Opposes'), touch: { ...baseObs('Supports', 'Opposes').touch!, status: 'miss' as const } },
      { ...baseObs('Opposes', 'Supports'), touch: { ...baseObs('Opposes', 'Supports').touch!, status: 'hit' as const } },
    ]
    const bundle = summarizeC5CohortBundle(rows)
    const m5 = bundle.cohorts.find((c) => c.id === 'm5_support')!
    expect(bundle.observationCount).toBe(3)
    expect(m5.sampleSize).toBe(2)
    expect(m5.hits).toBe(1)
    expect(m5.misses).toBe(1)
    expect(m5.excludedCount).toBe(1)
    expect(m5.missesExcluded).toBe(0)
    expect(m5.successfulTouchesExcluded).toBe(1)
    expect(m5.excludedHitsShareOfAllHits).toBeCloseTo(1 / 2)
    const both = bundle.cohorts.find((c) => c.id === 'both_support')!
    expect(both.sampleSize).toBe(1)
  })

  it('advances touch paper and attaches quotes by id', () => {
    const obs = baseObs('Supports', 'Supports')
    const hitTick = { epoch: obs.signalEpoch + 30, quote: obs.entryReferencePrice + 10, symbol: 'R_100' }
    const adv = advanceC5TouchPaper(obs, hitTick, true, DEFAULT_FROZEN_CONTRACT)
    expect(adv.touch?.status).toBe('hit')
    const quoted = attachC5ProposalQuote(obs, {
      ask: 1.2,
      payout: 2.1,
      responseEpoch: obs.receiptEpoch + 0.5,
    }, DEFAULT_FROZEN_CONTRACT)
    expect(quoted.touch?.quoteAvailable).toBe(true)
    expect(quoted.id).toBe(c5ContextId(1_700_000_040, 'RISE'))
  })
})