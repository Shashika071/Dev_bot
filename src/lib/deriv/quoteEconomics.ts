/**
 * Live ONETOUCH quote display helpers.
 * Never converts indicator agreement counts into a win probability.
 */

export type QuoteEconomics = {
  ask: number
  payout: number
  barrier: string
  /** Gross payout − ask */
  winGain: number
  /** ask (maximum loss if miss) */
  maxLoss: number
  /** ask / payout — break-even win rate, not a forecast */
  breakEven: number
  responseEpoch: number
  available: true
}

export type QuoteDisplay =
  | QuoteEconomics
  | { available: false; reason: 'Quote unavailable' }

export const QUOTE_MAX_AGE_SEC = 5

export function quoteEconomicsFromProposal(opts: {
  ask: number | null
  payout: number | null
  barrier: string
  responseEpoch: number
  nowSec: number
}): QuoteDisplay {
  const { ask, payout, barrier, responseEpoch, nowSec } = opts
  if (
    ask == null || payout == null
    || !Number.isFinite(ask) || !Number.isFinite(payout) || !(ask > 0) || payout < ask
    || !Number.isFinite(responseEpoch) || !Number.isFinite(nowSec) || responseEpoch > nowSec
    || nowSec - responseEpoch > QUOTE_MAX_AGE_SEC
  ) {
    return { available: false, reason: 'Quote unavailable' }
  }
  return {
    available: true,
    ask,
    payout,
    barrier,
    winGain: payout - ask,
    maxLoss: ask,
    breakEven: ask / payout,
    responseEpoch,
  }
}

export function formatBreakEven(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}
