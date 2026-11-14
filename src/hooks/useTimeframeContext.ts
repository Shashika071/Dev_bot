/**
 * M5/M15 context over an HTF-boundary-anchored history window. Completed facts
 * are therefore stable until the next HTF close; direction and live distances
 * can be recomputed on every render without caching a stale M1 answer.
 */
import { useMemo } from 'react'
import type { MarketState } from '../lib/deriv/marketFeed'
import { MAX_TICK_GAP_SECONDS } from '../lib/deriv/marketFeed'
import {
  computeMultiTimeframeContext,
  emptyMultiTimeframeContext,
  type MultiTimeframeContext,
} from '../lib/timeframes/timeframeContext'

function feedIsLive(market: MarketState | null, nowSec: number): boolean {
  if (!market) return false
  if (market.status !== 'CONNECTED' || !market.ready) return false
  if (market.lastTickEpoch == null) return false
  const age = nowSec - market.lastTickEpoch
  return age <= MAX_TICK_GAP_SECONDS && age >= -2
}

export function useTimeframeContext(
  market: MarketState | null,
  m1Side: 'RISE' | 'FALL' | null,
  nowSec: number,
): MultiTimeframeContext {
  return useMemo(() => {
    const ready = feedIsLive(market, nowSec)
    if (!market) return emptyMultiTimeframeContext(m1Side)
    return computeMultiTimeframeContext({
      candles: market.candles,
      m1Side,
      livePrice: market.lastPrice,
      nowEpoch: nowSec,
      feedReady: ready,
    })
  }, [market, m1Side, nowSec])
}
