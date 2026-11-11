import { MAX_TICK_GAP_SECONDS, type MarketState } from '../deriv/marketFeed'
import { isValidCandle } from '../deriv/candles'
import { C5_REQUIRED, evaluateC5LatestClosed } from './c5Strategy'
import type { LiveSetupConfidence } from './liveConfidence'
import type { SignalEvaluation } from '../types'

type Side = 'RISE' | 'FALL'

export interface EntryDisplay {
  state: 'WAIT' | 'FORMING' | 'CONFIRMED'
  side: Side | null
  watchSide: Side | null
  reason: string
  expiresInSeconds: number
  /** Moving market reference, never a broker proposal or execution price. */
  entryReference: number | null
  target: number | null
  signalPrice: number | null
  /** Current market price minus price at signal detection. */
  drift: number | null
  durationLabel: string
  tickAgeSeconds: number | null
}

export interface EntryDisplayInput {
  market: MarketState | null
  liveSignal: SignalEvaluation | null
  liveSetup: LiveSetupConfidence
  signalMeta: { candleCloseEpoch: number; expiresAt: number; remainingSec: number } | null
  nowSec: number
  barrierAbs: number
  duration: number
  durationUnit: string
}

function qualified(signal: SignalEvaluation | null): signal is SignalEvaluation & { final: Side } {
  return !!signal && signal.strategyId === 'C5' && signal.isQualified
    && (signal.final === 'RISE' || signal.final === 'FALL')
    && C5_REQUIRED.every(key => signal.confirmations[key])
    && Number.isFinite(signal.close) && signal.close > 0
}

/** Display only. This function never authorizes, quotes, or places a trade. */
export function deriveEntryDisplay(input: EntryDisplayInput): EntryDisplay {
  const { market, liveSignal, liveSetup, signalMeta, nowSec, barrierAbs, duration, durationUnit } = input
  const tickAge = market?.lastTickEpoch != null && Number.isFinite(market.lastTickEpoch) && Number.isFinite(nowSec)
    ? nowSec - market.lastTickEpoch : null
  const unitNames: Record<string, string> = { s: 'seconds', m: 'minutes', h: 'hours', d: 'days' }
  const durationLabel = Number.isFinite(duration) && duration > 0 && unitNames[durationUnit]
    ? `${duration} ${unitNames[durationUnit]}` : 'Check duration'
  const base: EntryDisplay = {
    state: 'WAIT', side: null,
    watchSide: liveSetup.ready && liveSetup.lean !== 'NONE' ? liveSetup.lean : null,
    reason: 'Waiting for a fresh C5 signal.', expiresInSeconds: 0,
    entryReference: null, target: null, signalPrice: null, drift: null,
    durationLabel, tickAgeSeconds: tickAge,
  }
  const wait = (reason: string): EntryDisplay => ({ ...base, reason })
  if (!market || market.status !== 'CONNECTED') return wait('Waiting for the market connection.')
  if (!market.ready || !liveSetup.ready) return wait('Market history is syncing. Wait for the feed to be ready.')
  if (tickAge == null || tickAge < -2 || tickAge > MAX_TICK_GAP_SECONDS) return wait('Price feed is stale or its clock is out of sync. Wait for a fresh tick.')
  if (market.lastPrice == null || !Number.isFinite(market.lastPrice) || market.lastPrice <= 0) return wait('Waiting for a valid live price.')
  if (!qualified(liveSignal)) return base
  if (!signalMeta || !Number.isFinite(signalMeta.expiresAt)
    || signalMeta.expiresAt <= Math.max(nowSec, market.lastTickEpoch!)) {
    return wait('The signal entry window has ended. Wait for a new C5 signal.')
  }
  if (!Number.isFinite(barrierAbs) || barrierAbs <= 0 || !Number.isFinite(duration) || duration <= 0 || !unitNames[durationUnit]) {
    return wait('Check the barrier and duration in Trade settings.')
  }
  const signalBar = market.candles.find(c => c.time === liveSignal.time)
  if (!signalBar || !isValidCandle(signalBar)) return wait('The signal candle is unavailable. Wait for fresh data.')

  let state: EntryDisplay['state']
  if (!signalBar.isClosed) {
    const currentSide = liveSignal.final === 'RISE' ? liveSetup.rise : liveSetup.fall
    if (market.lastTickEpoch! < signalBar.time || market.lastTickEpoch! >= signalBar.time + 60
      || market.candles.at(-1)?.time !== signalBar.time || !liveSetup.forming || liveSetup.candleTime !== signalBar.time
      || currentSide.passed !== 4 || currentSide.total !== 4 || !C5_REQUIRED.every(key => currentSide.flags[key])) {
      return wait('Setup changed: the forming candle no longer meets all four C5 conditions.')
    }
    state = 'FORMING'
  } else {
    // A latched intrabar alert must not become "confirmed" just because time passed.
    if (market.lastTickEpoch! < signalBar.time + 60 || nowSec < signalBar.time + 60) {
      return wait('The candle close has not been observed yet. Wait for fresh data.')
    }
    const closed = evaluateC5LatestClosed(market.candles)
    if (!qualified(closed) || closed.time !== signalBar.time || closed.final !== liveSignal.final) {
      return wait('The completed candle did not confirm this C5 signal. Wait for a new setup.')
    }
    state = 'CONFIRMED'
  }
  const target = market.lastPrice + (liveSignal.final === 'RISE' ? barrierAbs : -barrierAbs)
  if (!Number.isFinite(target) || target <= 0) return wait('The target is invalid. Check the barrier in Trade settings.')
  return {
    ...base, state, side: liveSignal.final,
    reason: state === 'FORMING'
      ? 'All four C5 conditions currently agree. This candle is still forming and can change.'
      : 'All four C5 conditions agreed at the candle close. The signal window is still open.',
    expiresInSeconds: Math.max(0, signalMeta.expiresAt - Math.max(nowSec, market.lastTickEpoch!)),
    entryReference: market.lastPrice, target, signalPrice: liveSignal.close,
    drift: market.lastPrice - liveSignal.close,
  }
}
