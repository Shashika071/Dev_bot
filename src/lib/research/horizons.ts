import type { Tick } from '../types'
import type { HorizonMin, HorizonResult, MoveClass, ResearchObservation, ResearchSide } from './strategies/types'

export const HORIZON_MINUTES: HorizonMin[] = [1, 3, 5, 7]
export const MAX_HORIZON_DELAY_SEC = 5
export const MAX_TICK_GAP_SEC = 10
export const STRATEGY_COOLDOWN_SEC = 420

function sign(side: ResearchSide): 1 | -1 {
  return side === 'RISE' ? 1 : -1
}

function classify(signed: number): Exclude<MoveClass, 'pending' | 'unknown'> {
  if (signed > 0) return 'continuation'
  if (signed < 0) return 'reversal'
  return 'unchanged'
}

export function makeHorizons(entryEpoch: number): HorizonResult[] {
  return HORIZON_MINUTES.map((minutes) => ({
    minutes,
    targetEpoch: entryEpoch + minutes * 60,
    status: 'pending' as const,
  }))
}

function settle(
  h: HorizonResult,
  tick: Tick,
  side: ResearchSide,
  entry: number,
  atr14: number | null,
  randomSide: ResearchSide,
): HorizonResult {
  const signedMove = sign(side) * (tick.quote - entry)
  const randomSignedMove = sign(randomSide) * (tick.quote - entry)
  return {
    ...h,
    status: classify(signedMove),
    observedEpoch: tick.epoch,
    observedPrice: tick.quote,
    delaySec: tick.epoch - h.targetEpoch,
    signedMove,
    signedMoveInAtr: atr14 != null && atr14 > 0 ? signedMove / atr14 : null,
    randomSignedMove,
    randomClass: classify(randomSignedMove),
    reason: undefined,
  }
}

function markUnknown(h: HorizonResult, reason: string): HorizonResult {
  if (h.status !== 'pending') return h
  return { ...h, status: 'unknown', reason }
}

export function advanceObservationHorizons(
  obs: ResearchObservation,
  tick: Tick | null,
  healthy: boolean,
): ResearchObservation {
  if (!obs.pendingHorizons) return obs
  if (!healthy) {
    const horizons = obs.horizons.map((h) => markUnknown(h, 'Feed interrupted or reload'))
    return { ...obs, horizons, pendingHorizons: false }
  }
  if (!tick || tick.epoch <= obs.lastTickEpoch) return obs
  if (tick.epoch - obs.lastTickEpoch > MAX_TICK_GAP_SEC) {
    const horizons = obs.horizons.map((h) => markUnknown(h, 'Missing ticks during observation'))
    return { ...obs, lastTickEpoch: tick.epoch, horizons, pendingHorizons: false }
  }
  const horizons = obs.horizons.map((h) => {
    if (h.status !== 'pending') return h
    if (tick.epoch < h.targetEpoch) return h
    if (tick.epoch > h.targetEpoch + MAX_HORIZON_DELAY_SEC) {
      return markUnknown(h, 'No timely horizon tick (max delay 5s)')
    }
    return settle(h, tick, obs.side, obs.entryReferencePrice, obs.atr.atr14, obs.randomSide)
  })
  return {
    ...obs,
    lastTickEpoch: tick.epoch,
    horizons,
    pendingHorizons: horizons.some((h) => h.status === 'pending'),
  }
}
