/**
 * Live signal observations for research only.
 * Records ≥threshold liveConfidence setups (forming OK); does not place trades.
 */
import type { Candle, ConfirmationFlags, Tick } from '../types'
import { atrRma } from '../indicators/atr'
import type { LiveSetupConfidence, SideSetup } from '../strategy/liveConfidence'

export const OBSERVATION_HORIZONS_MIN = [1, 3, 5, 7] as const
export type ObservationHorizonMin = (typeof OBSERVATION_HORIZONS_MIN)[number]

/** Same bound as paper tracker: first tick at/after target, at most 5s late. */
export const MAX_HORIZON_DELAY_SEC = 5
/** Same feed-gap rule as paper tracker / market feed. */
export const MAX_OBSERVATION_TICK_GAP_SEC = 10

export const ATR_PERIOD = 14
export const MAX_OBSERVATIONS = 2000

export type HorizonClass = 'continuation' | 'reversal' | 'unchanged' | 'unknown' | 'pending'

export type CandleSizeBucket = 'below_1_atr' | '1_to_2_atr' | 'above_2_atr' | 'unknown'

export interface FrozenAtrSnapshot {
  /** Wilder ATR14 from completed candles only at detection. */
  atr14: number | null
  /** 100 × ATR14 / last completed close. */
  atrPct: number | null
  /** Last completed candle high − low. */
  candleRange: number | null
  /** candleRange / ATR14. */
  rangeOverAtr: number | null
  lastCompletedClose: number | null
  lastCompletedTime: number | null
  sizeBucket: CandleSizeBucket
}

export interface HorizonObservation {
  minutes: ObservationHorizonMin
  targetEpoch: number
  status: HorizonClass
  observedEpoch?: number
  observedPrice?: number
  /** observedEpoch − targetEpoch (seconds). */
  delaySec?: number
  signedMove?: number
  signedMoveInAtr?: number | null
  /** Matched 50/50 random-direction signed move at the same price. */
  randomSignedMove?: number
  randomSignedMoveInAtr?: number | null
  randomClass?: Exclude<HorizonClass, 'pending'>
  reason?: string
}

export interface SignalObservation {
  /** `${candleTime}:${direction}` — one per candle and direction. */
  id: string
  symbol: string
  candleTime: number
  signalTickEpoch: number
  receiptEpoch: number
  direction: 'RISE' | 'FALL'
  /** Public tick price at detection — not a broker fill. */
  entryReferencePrice: number
  score: number
  passed: number
  total: number
  flags: ConfirmationFlags
  /** e.g. "ut+supertrend+sma+ao|miss:rsi" */
  conditionKey: string
  missingConditions: (keyof ConfirmationFlags)[]
  forming: boolean
  atr: FrozenAtrSnapshot
  /** Fixed at creation for matched random reference. */
  randomDirection: 'RISE' | 'FALL'
  lastTickEpoch: number
  horizons: HorizonObservation[]
  /** True while any horizon is still pending. */
  pending: boolean
}

export function conditionKeyFromFlags(flags: ConfirmationFlags): {
  key: string
  missing: (keyof ConfirmationFlags)[]
} {
  const order: (keyof ConfirmationFlags)[] = ['ut', 'supertrend', 'sma', 'ao', 'rsi']
  const passed = order.filter((k) => flags[k])
  const missing = order.filter((k) => !flags[k])
  const key =
    `${passed.join('+') || 'none'}|miss:${missing.join('+') || 'none'}`
  return { key, missing }
}

export function sizeBucketFromRatio(rangeOverAtr: number | null): CandleSizeBucket {
  if (rangeOverAtr == null || !Number.isFinite(rangeOverAtr) || rangeOverAtr < 0) return 'unknown'
  if (rangeOverAtr < 1) return 'below_1_atr'
  if (rangeOverAtr <= 2) return '1_to_2_atr'
  return 'above_2_atr'
}

/**
 * ATR from completed candles only. Does not use the forming candle's eventual range.
 */
export function freezeAtrAtSignal(candles: Candle[], period = ATR_PERIOD): FrozenAtrSnapshot {
  const closed = candles.filter((c) => c.isClosed)
  const empty: FrozenAtrSnapshot = {
    atr14: null,
    atrPct: null,
    candleRange: null,
    rangeOverAtr: null,
    lastCompletedClose: null,
    lastCompletedTime: null,
    sizeBucket: 'unknown',
  }
  if (closed.length === 0) return empty

  const last = closed[closed.length - 1]!
  const candleRange = last.high - last.low
  if (closed.length < period) {
    return {
      ...empty,
      candleRange,
      lastCompletedClose: last.close,
      lastCompletedTime: last.time,
    }
  }

  const highs = closed.map((c) => c.high)
  const lows = closed.map((c) => c.low)
  const closes = closed.map((c) => c.close)
  const atrSeries = atrRma(highs, lows, closes, period)
  const atr14 = atrSeries[closed.length - 1]
  if (atr14 == null || !Number.isFinite(atr14) || atr14 <= 0) {
    return {
      atr14: atr14 != null && Number.isFinite(atr14) ? atr14 : null,
      atrPct: null,
      candleRange,
      rangeOverAtr: null,
      lastCompletedClose: last.close,
      lastCompletedTime: last.time,
      sizeBucket: 'unknown',
    }
  }

  const atrPct = last.close !== 0 ? (100 * atr14) / last.close : null
  const rangeOverAtr = candleRange / atr14
  return {
    atr14,
    atrPct,
    candleRange,
    rangeOverAtr,
    lastCompletedClose: last.close,
    lastCompletedTime: last.time,
    sizeBucket: sizeBucketFromRatio(rangeOverAtr),
  }
}

function directionSign(dir: 'RISE' | 'FALL'): 1 | -1 {
  return dir === 'RISE' ? 1 : -1
}

function classifySignedMove(signedMove: number): Exclude<HorizonClass, 'pending' | 'unknown'> {
  if (signedMove > 0) return 'continuation'
  if (signedMove < 0) return 'reversal'
  return 'unchanged'
}

function settleHorizon(
  h: HorizonObservation,
  tick: Tick,
  direction: 'RISE' | 'FALL',
  entry: number,
  atr14: number | null,
  randomDirection: 'RISE' | 'FALL',
): HorizonObservation {
  const delaySec = tick.epoch - h.targetEpoch
  const signedMove = directionSign(direction) * (tick.quote - entry)
  const randomSignedMove = directionSign(randomDirection) * (tick.quote - entry)
  const signedMoveInAtr =
    atr14 != null && atr14 > 0 && Number.isFinite(atr14) ? signedMove / atr14 : null
  const randomSignedMoveInAtr =
    atr14 != null && atr14 > 0 && Number.isFinite(atr14) ? randomSignedMove / atr14 : null
  return {
    ...h,
    status: classifySignedMove(signedMove),
    observedEpoch: tick.epoch,
    observedPrice: tick.quote,
    delaySec,
    signedMove,
    signedMoveInAtr,
    randomSignedMove,
    randomSignedMoveInAtr,
    randomClass: classifySignedMove(randomSignedMove),
    reason: undefined,
  }
}

function markUnknown(h: HorizonObservation, reason: string): HorizonObservation {
  if (h.status !== 'pending') return h
  return { ...h, status: 'unknown', reason }
}

function anyPending(horizons: HorizonObservation[]): boolean {
  return horizons.some((h) => h.status === 'pending')
}

export function createSignalObservation(input: {
  symbol: string
  candleTime: number
  direction: 'RISE' | 'FALL'
  side: SideSetup
  forming: boolean
  tick: Tick
  receiptEpoch: number
  candles: Candle[]
  randomDirection?: 'RISE' | 'FALL'
}): SignalObservation {
  const { key, missing } = conditionKeyFromFlags(input.side.flags)
  const atr = freezeAtrAtSignal(input.candles)
  const randomDirection =
    input.randomDirection
    ?? (Math.random() < 0.5 ? 'RISE' : 'FALL')
  const horizons: HorizonObservation[] = OBSERVATION_HORIZONS_MIN.map((minutes) => ({
    minutes,
    targetEpoch: input.tick.epoch + minutes * 60,
    status: 'pending' as const,
  }))
  return {
    id: `${input.candleTime}:${input.direction}`,
    symbol: input.symbol,
    candleTime: input.candleTime,
    signalTickEpoch: input.tick.epoch,
    receiptEpoch: input.receiptEpoch,
    direction: input.direction,
    entryReferencePrice: input.tick.quote,
    score: input.side.confidence,
    passed: input.side.passed,
    total: input.side.total,
    flags: { ...input.side.flags },
    conditionKey: key,
    missingConditions: missing,
    forming: input.forming,
    atr,
    randomDirection,
    lastTickEpoch: input.tick.epoch,
    horizons,
    pending: true,
  }
}

/**
 * When live setup first reaches minConfidence for a side, append one observation.
 * Dedupes by candleTime + direction. Does not alter trading.
 */
export function collectThresholdObservations(
  existing: SignalObservation[],
  opts: {
    setup: LiveSetupConfidence
    minConfidence: number
    tick: Tick
    receiptEpoch: number
    symbol: string
    candles: Candle[]
    seenIds?: Set<string>
    randomDirectionFor?: (id: string) => 'RISE' | 'FALL'
  },
): SignalObservation[] {
  if (!opts.setup.ready || opts.setup.candleTime == null) return existing
  const known = opts.seenIds ?? new Set(existing.map((o) => o.id))
  let next = existing
  for (const direction of ['RISE', 'FALL'] as const) {
    const side = direction === 'RISE' ? opts.setup.rise : opts.setup.fall
    if (side.confidence + 1e-12 < opts.minConfidence) continue
    const id = `${opts.setup.candleTime}:${direction}`
    if (known.has(id)) continue
    known.add(id)
    const obs = createSignalObservation({
      symbol: opts.symbol,
      candleTime: opts.setup.candleTime,
      direction,
      side,
      forming: opts.setup.forming,
      tick: opts.tick,
      receiptEpoch: opts.receiptEpoch,
      candles: opts.candles,
      randomDirection: opts.randomDirectionFor?.(id),
    })
    next = [...next, obs].slice(-MAX_OBSERVATIONS)
  }
  return next
}

/**
 * Advance pending horizons from a public tick. Completed horizons are never rewritten.
 */
export function advanceObservations(
  rows: SignalObservation[],
  tick: Tick | null,
  healthy: boolean,
): SignalObservation[] {
  let changed = false
  const next = rows.map((row) => {
    if (!row.pending) return row

    if (!healthy) {
      changed = true
      const horizons = row.horizons.map((h) =>
        markUnknown(h, 'Feed interrupted, stale, or page reloaded; outcome unknown'),
      )
      return { ...row, horizons, pending: false }
    }

    if (!tick) return row
    if (tick.epoch <= row.lastTickEpoch) return row

    if (tick.epoch - row.lastTickEpoch > MAX_OBSERVATION_TICK_GAP_SEC) {
      changed = true
      const horizons = row.horizons.map((h) =>
        markUnknown(h, 'Missing ticks during observation'),
      )
      return { ...row, lastTickEpoch: tick.epoch, horizons, pending: false }
    }

    changed = true
    const atr14 = row.atr.atr14
    const horizons = row.horizons.map((h) => {
      if (h.status !== 'pending') return h
      if (tick.epoch < h.targetEpoch) return h
      if (tick.epoch > h.targetEpoch + MAX_HORIZON_DELAY_SEC) {
        return markUnknown(h, 'No timely horizon tick (max delay 5s)')
      }
      return settleHorizon(h, tick, row.direction, row.entryReferencePrice, atr14, row.randomDirection)
    })

    return {
      ...row,
      lastTickEpoch: tick.epoch,
      horizons,
      pending: anyPending(horizons),
    }
  })
  return changed ? next : rows
}

/** Clear completed rows; keep any observation that still has a pending horizon. */
export function clearCompletedObservations(rows: SignalObservation[]): SignalObservation[] {
  return rows.filter((r) => r.pending)
}
