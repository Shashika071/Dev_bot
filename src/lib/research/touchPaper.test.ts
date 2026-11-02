import { describe, expect, it } from 'vitest'
import { DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import {
  advanceTouchPaper,
  attachProposalQuote,
  maybeStartTouchPaper,
} from './touchPaper'
import type { ResearchObservation } from './strategies/types'

function baseObs(epoch = 1_700_000_000): ResearchObservation {
  const tick = { epoch, quote: 100, symbol: 'R_100' }
  const obs: ResearchObservation = {
    id: 'S1:1:RISE',
    strategyId: 'S1',
    side: 'RISE',
    candleTime: epoch - 60,
    signalEpoch: epoch,
    receiptEpoch: epoch,
    entryReferencePrice: 100,
    entryTick: tick,
    forming: false,
    reason: 'test',
    atr: 1,
    lastTickEpoch: epoch,
    horizons: [],
    pendingHorizons: false,
  }
  return maybeStartTouchPaper(obs, DEFAULT_FROZEN_CONTRACT, tick)
}

describe('touch paper regressions', () => {
  it('does not count a touch after contract expiry', () => {
    let obs = baseObs(1000)
    const expiry = 1000 + 7 * 60
    // Walk ticks through the window so coverage stays intact
    for (let e = 1001; e <= expiry - 1; e += 5) {
      obs = advanceTouchPaper(
        obs,
        { epoch: e, quote: 100.01, symbol: 'R_100' },
        true,
        DEFAULT_FROZEN_CONTRACT,
      )
    }
    obs = advanceTouchPaper(
      obs,
      { epoch: expiry + 2, quote: 100.2, symbol: 'R_100' },
      true,
      DEFAULT_FROZEN_CONTRACT,
    )
    expect(obs.touch?.status).toBe('miss')
    expect(obs.touch?.firstTouchEpoch).toBeUndefined()
  })

  it('counts a touch at exact expiry', () => {
    let obs = baseObs(1000)
    const expiry = 1000 + 7 * 60
    for (let e = 1001; e <= expiry - 1; e += 5) {
      obs = advanceTouchPaper(
        obs,
        { epoch: e, quote: 100.01, symbol: 'R_100' },
        true,
        DEFAULT_FROZEN_CONTRACT,
      )
    }
    obs = advanceTouchPaper(
      obs,
      { epoch: expiry, quote: 100.2, symbol: 'R_100' },
      true,
      DEFAULT_FROZEN_CONTRACT,
    )
    expect(obs.touch?.status).toBe('hit')
  })

  it('does not award P&L from a reference hit that occurred before the quote', () => {
    let obs = baseObs(1000)
    // Reference path hits before any quote arrives
    obs = advanceTouchPaper(
      obs,
      { epoch: 1002, quote: 100.2, symbol: 'R_100' },
      true,
      DEFAULT_FROZEN_CONTRACT,
    )
    expect(obs.touch?.status).toBe('hit')
    expect(obs.touch?.paperPnl).toBeNull()

    obs = attachProposalQuote(
      obs,
      { ask: 0.55, payout: 1.0, requestEpoch: 1001, responseEpoch: 1003 },
      DEFAULT_FROZEN_CONTRACT,
    )
    expect(obs.touch?.quoteAvailable).toBe(true)
    expect(obs.touch?.paperPnl).toBeNull()
    expect(obs.touch?.quotedPath).toBeUndefined()

    // Quoted path starts only on a fresh post-quote tick
    obs = advanceTouchPaper(
      obs,
      { epoch: 1004, quote: 100.01, symbol: 'R_100' },
      true,
      DEFAULT_FROZEN_CONTRACT,
      1004,
    )
    expect(obs.touch?.quotedPath?.status).toBe('pending')
    expect(obs.touch?.paperPnl).toBeNull()

    obs = advanceTouchPaper(
      obs,
      { epoch: 1005, quote: 100.2, symbol: 'R_100' },
      true,
      DEFAULT_FROZEN_CONTRACT,
      1005,
    )
    expect(obs.touch?.quotedPath?.status).toBe('hit')
    expect(obs.touch?.paperPnl).toBeCloseTo(1.0 - 0.55)
  })
})
