import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import { ResearchQuoteScheduler } from './quoteScheduler'
import { maybeStartTouchPaper } from './touchPaper'
import type { ResearchObservation } from './strategies/types'

function waitingObs(id: string, side: 'RISE' | 'FALL', signalEpoch: number): ResearchObservation {
  const tick = { epoch: signalEpoch, quote: 100, symbol: 'R_100' }
  const obs: ResearchObservation = {
    id,
    strategyId: id.split(':')[0] as ResearchObservation['strategyId'],
    side,
    candleTime: signalEpoch - 60,
    signalEpoch,
    receiptEpoch: signalEpoch,
    entryReferencePrice: 100,
    entryTick: tick,
    forming: false,
    reason: 'test',
    atr: 1,
    lastTickEpoch: signalEpoch,
    horizons: [],
    pendingHorizons: false,
  }
  return maybeStartTouchPaper(obs, DEFAULT_FROZEN_CONTRACT, tick)
}

describe('ResearchQuoteScheduler', () => {
  it('quotes every same-tick same-side observation, not only the last one', async () => {
    const request = vi.fn(async () => ({
      ask: 0.5,
      payout: 0.95,
      responseEpoch: 1_700_000_001,
    }))
    const delivered: string[][] = []
    const scheduler = new ResearchQuoteScheduler(
      request,
      (ids) => { delivered.push([...ids]) },
      () => 1_700_000_000,
      2,
    )

    const rows = [
      waitingObs('C1:1:RISE', 'RISE', 1_700_000_000),
      waitingObs('C2:1:RISE', 'RISE', 1_700_000_000),
      waitingObs('C3:1:RISE', 'RISE', 1_700_000_000),
      waitingObs('S7:1:FALL', 'FALL', 1_700_000_000),
    ]
    scheduler.enqueue(rows, DEFAULT_FROZEN_CONTRACT)

    await vi.waitFor(() => expect(delivered.length).toBe(2))
    expect(request).toHaveBeenCalledTimes(2)
    const riseBatch = delivered.find((ids) => ids.includes('C1:1:RISE'))
    expect(riseBatch).toEqual(['C1:1:RISE', 'C2:1:RISE', 'C3:1:RISE'])
    expect(delivered.some((ids) => ids.includes('S7:1:FALL'))).toBe(true)
  })
})
