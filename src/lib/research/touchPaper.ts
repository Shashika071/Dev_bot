/** Public-tick reference touches and a separate, causal quote-based simulation. */
import type { Tick } from '../types'
import type { FrozenContractSettings } from './contractSettings'
import type { ResearchObservation, TouchPathState } from './strategies/types'
import { MAX_TICK_GAP_SEC } from './horizons'

export function formatRelativeBarrier(abs: number, sign: 1 | -1): string {
  return `${sign > 0 ? '+' : '-'}${Math.abs(abs)}`
}

export function absoluteBarrier(entry: number, side: 'RISE' | 'FALL', distance: number): number {
  return side === 'RISE' ? entry + distance : entry - distance
}

function startPath(tick: Tick, side: 'RISE' | 'FALL', distance: number): TouchPathState {
  return {
    paperEntryEpoch: tick.epoch, paperEntryPrice: tick.quote,
    absoluteBarrier: absoluteBarrier(tick.quote, side, distance),
    status: 'pending', lastTickEpoch: tick.epoch, coverageLost: false,
  }
}

export function maybeStartTouchPaper(
  obs: ResearchObservation, contract: FrozenContractSettings, tick: Tick,
): ResearchObservation {
  return { ...obs, touch: {
    ...startPath(tick, obs.side, contract.barrierAbs),
    barrierAbs: contract.barrierAbs, durationMin: contract.duration, stake: contract.paperStake,
    quoteAvailable: false, quoteStatus: 'waiting', proposalAsk: null,
    proposalPayout: null, proposalAgeSec: null, paperPnl: null,
  } }
}

export interface PaperQuote {
  ask: number
  payout: number
  requestEpoch?: number
  responseEpoch: number
  rawError?: string
}

export function attachProposalQuote(
  obs: ResearchObservation, quote: PaperQuote, contract: FrozenContractSettings,
): ResearchObservation {
  const t = obs.touch
  if (!t || t.quoteStatus !== 'waiting') return obs
  const age = quote.responseEpoch - obs.receiptEpoch
  const invalid = quote.rawError || (!Number.isFinite(quote.ask) || quote.ask <= 0
    || !Number.isFinite(quote.payout) || quote.payout < quote.ask ? 'Invalid proposal prices' : '')
  if (invalid || !Number.isFinite(age) || age < 0 || age > contract.maxQuoteAgeSec) {
    return { ...obs, touch: { ...t, quoteStatus: 'unavailable', quoteAvailable: false,
      quoteReason: invalid || 'Proposal arrived outside the entry quote deadline',
      quoteRequestEpoch: quote.requestEpoch, quoteResponseEpoch: quote.responseEpoch } }
  }
  // A later quote never prices an already observed reference touch.
  // The separate quotedPath starts on a subsequent fresh tick.
  return { ...obs, touch: { ...t, quoteAvailable: true, quoteStatus: 'available',
    proposalAsk: quote.ask, proposalPayout: quote.payout, proposalAgeSec: age,
    quoteRequestEpoch: quote.requestEpoch, quoteResponseEpoch: quote.responseEpoch,
    paperPnl: null } }
}

function advancePath(
  path: TouchPathState, side: 'RISE' | 'FALL', durationMin: number,
  tick: Tick | null, healthy: boolean,
): TouchPathState {
  if (path.status !== 'pending') return path
  const entry = path.paperEntryEpoch
  const barrier = path.absoluteBarrier
  if (entry == null || barrier == null) return { ...path, status: 'unknown', reason: 'Missing entry' }
  if (!healthy) return { ...path, coverageLost: true, reason: 'Tick coverage interrupted' }
  if (!tick || tick.epoch <= (path.lastTickEpoch ?? entry)) return path
  const coverageLost = !!path.coverageLost || tick.epoch - (path.lastTickEpoch ?? entry) > MAX_TICK_GAP_SEC
  const next = { ...path, lastTickEpoch: tick.epoch, coverageLost }
  const expiry = entry + durationMin * 60
  // Directional endpoint tolerance must never extend the Touch contract.
  if (tick.epoch <= expiry && (side === 'RISE' ? tick.quote >= barrier : tick.quote <= barrier)) {
    return { ...next, status: 'hit', firstTouchEpoch: tick.epoch, reason: undefined }
  }
  if (tick.epoch >= expiry) {
    return { ...next, status: coverageLost ? 'unknown' : 'miss',
      reason: coverageLost ? 'Incomplete tick coverage during contract window' : 'No touch by expiry' }
  }
  return next
}

function expirePath(path: TouchPathState, durationMin: number, now: number): TouchPathState {
  if (path.status !== 'pending' || path.paperEntryEpoch == null) return path
  if (now <= path.paperEntryEpoch + durationMin * 60 + MAX_TICK_GAP_SEC) return path
  return { ...path, status: 'unknown', coverageLost: true, reason: 'No complete tick coverage through expiry' }
}

function quotedPnl(obs: ResearchObservation): ResearchObservation {
  const t = obs.touch
  if (!t) return obs
  const p = t.quotedPath
  const pnl = t.quoteAvailable && t.proposalAsk != null && t.proposalPayout != null
    ? p?.status === 'hit' ? t.proposalPayout - t.proposalAsk
      : p?.status === 'miss' ? -t.proposalAsk : null
    : null
  return { ...obs, touch: { ...t, paperPnl: pnl } }
}

export function advanceTouchPaper(
  obs: ResearchObservation, tick: Tick | null, healthy: boolean,
  contract: FrozenContractSettings, receiptEpoch = tick?.epoch ?? obs.receiptEpoch,
): ResearchObservation {
  const t = obs.touch
  if (!t) return obs
  const reference = advancePath(t, obs.side, t.durationMin, tick, healthy)
  let next = { ...t, ...reference }
  if (t.quotedPath) {
    next.quotedPath = advancePath(t.quotedPath, obs.side, t.durationMin, tick, healthy)
  } else if (t.quoteAvailable && t.quoteResponseEpoch != null && tick && healthy) {
    if (receiptEpoch - t.quoteResponseEpoch > contract.maxQuoteAgeSec) {
      next = { ...next, quoteAvailable: false, quoteStatus: 'expired',
        quoteReason: 'No timely fresh entry tick after proposal receipt' }
    } else if (tick.epoch >= t.quoteResponseEpoch && receiptEpoch >= t.quoteResponseEpoch) {
      next.quotedPath = startPath(tick, obs.side, t.barrierAbs)
    }
  }
  return quotedPnl({ ...obs, touch: next })
}

export function expireTouchPaper(
  obs: ResearchObservation, now: number, contract: FrozenContractSettings,
): ResearchObservation {
  const t = obs.touch
  if (!t) return obs
  let next = { ...t, ...expirePath(t, t.durationMin, now) }
  if (t.quotedPath) next.quotedPath = expirePath(t.quotedPath, t.durationMin, now)
  if (t.quoteStatus === 'waiting' && now > obs.receiptEpoch + contract.maxQuoteAgeSec) {
    next = { ...next, quoteStatus: 'unavailable', quoteReason: 'No timely proposal received' }
  }
  if (t.quoteAvailable && !t.quotedPath && t.quoteResponseEpoch != null
    && now > t.quoteResponseEpoch + contract.maxQuoteAgeSec) {
    next = { ...next, quoteAvailable: false, quoteStatus: 'expired', quoteReason: 'No timely post-quote entry' }
  }
  return quotedPnl({ ...obs, touch: next })
}

export function hasPendingTouch(obs: ResearchObservation): boolean {
  const t = obs.touch
  return !!t && (t.status === 'pending' || t.quoteStatus === 'waiting'
    || (t.quoteAvailable && (!t.quotedPath || t.quotedPath.status === 'pending')))
}

export const RESEARCH_FORBIDDEN_METHODS = ['buy', 'buyOneTouch', 'placeTouchTrades'] as const
