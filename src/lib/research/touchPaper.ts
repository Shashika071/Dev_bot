/**
 * Quote-based / tick-path ONETOUCH paper simulation.
 * Never sends buy. Proposals are optional and may be unavailable.
 */
import type { Tick } from '../types'
import type { FrozenContractSettings } from './contractSettings'
import type { ResearchObservation, TouchPaperState } from './strategies/types'

export function formatRelativeBarrier(abs: number, sign: 1 | -1): string {
  const mag = Math.abs(abs)
  const body = Number.isInteger(mag) ? String(mag) : String(mag)
  return sign > 0 ? `+${body}` : `-${body}`
}

/**
 * Absolute barrier from paper entry price.
 * RISE uses +barrierAbs; FALL uses -barrierAbs (touch that level).
 */
export function absoluteBarrier(
  entryPrice: number,
  side: 'RISE' | 'FALL',
  barrierAbs: number,
): number {
  return side === 'RISE' ? entryPrice + barrierAbs : entryPrice - barrierAbs
}

export function maybeStartTouchPaper(
  obs: ResearchObservation,
  contract: FrozenContractSettings,
  entryTick: Tick,
): ResearchObservation {
  const touch: TouchPaperState = {
    barrierAbs: contract.barrierAbs,
    durationMin: contract.duration,
    stake: contract.paperStake,
    // Causal paper entry = same as signal reference tick for this build
    // (quote may arrive later; when quote unavailable we still track tick path)
    paperEntryEpoch: entryTick.epoch,
    paperEntryPrice: entryTick.quote,
    absoluteBarrier: absoluteBarrier(entryTick.quote, obs.side, contract.barrierAbs),
    status: 'pending',
    quoteAvailable: false,
    proposalAsk: null,
    proposalPayout: null,
    proposalAgeSec: null,
    paperPnl: null,
  }
  return { ...obs, touch }
}

/** Attach a contemporaneous proposal quote if age is acceptable. Never buys. */
export function attachProposalQuote(
  obs: ResearchObservation,
  quote: { ask: number; payout: number; responseEpoch: number },
  contract: FrozenContractSettings,
): ResearchObservation {
  if (!obs.touch || obs.touch.paperEntryEpoch == null) return obs
  const age = quote.responseEpoch - obs.touch.paperEntryEpoch
  if (age < 0 || age > contract.maxQuoteAgeSec) {
    return {
      ...obs,
      touch: {
        ...obs.touch,
        quoteAvailable: false,
        reason: obs.touch.reason ?? `Proposal age ${age}s outside max ${contract.maxQuoteAgeSec}s`,
      },
    }
  }
  return {
    ...obs,
    touch: {
      ...obs.touch,
      quoteAvailable: true,
      proposalAsk: quote.ask,
      proposalPayout: quote.payout,
      proposalAgeSec: age,
    },
  }
}

function touched(side: 'RISE' | 'FALL', price: number, barrier: number): boolean {
  // Consistent with typical touch: RISE touches at/above upper; FALL at/below lower
  if (side === 'RISE') return price >= barrier
  return price <= barrier
}

export function advanceTouchPaper(
  obs: ResearchObservation,
  tick: Tick | null,
  healthy: boolean,
  _contract: FrozenContractSettings,
): ResearchObservation {
  const t = obs.touch
  if (!t || t.status !== 'pending') return obs
  if (t.paperEntryEpoch == null || t.paperEntryPrice == null || t.absoluteBarrier == null) {
    return { ...obs, touch: { ...t, status: 'unknown', reason: 'Missing paper entry' } }
  }

  if (!healthy) {
    return { ...obs, touch: { ...t, status: 'unknown', reason: 'Feed interrupted during paper window' } }
  }
  if (!tick) return obs
  if (tick.epoch < t.paperEntryEpoch) return obs // ignore pre-entry

  const expiry = t.paperEntryEpoch + t.durationMin * 60
  if (tick.epoch > expiry + 5) {
    // No touch observed; if we had continuous coverage treat as miss, else unknown
    // Conservative: mark miss only if lastTick was recent through expiry
    const covered = obs.lastTickEpoch >= expiry - 10
    if (!covered) {
      return { ...obs, touch: { ...t, status: 'unknown', reason: 'Incomplete tick coverage in contract window' } }
    }
    const pnl = t.quoteAvailable && t.proposalAsk != null ? -t.proposalAsk : null
    return {
      ...obs,
      touch: { ...t, status: 'miss', paperPnl: pnl, reason: 'No touch before expiry' },
    }
  }

  if (touched(obs.side, tick.quote, t.absoluteBarrier)) {
    const pnl = t.quoteAvailable && t.proposalAsk != null && t.proposalPayout != null
      ? t.proposalPayout - t.proposalAsk
      : null
    return {
      ...obs,
      touch: {
        ...t,
        status: 'hit',
        firstTouchEpoch: tick.epoch,
        paperPnl: pnl,
      },
    }
  }
  return obs
}

/** Research must never call buy — exported for tests. */
export const RESEARCH_FORBIDDEN_METHODS = ['buy', 'buyOneTouch', 'placeTouchTrades'] as const
