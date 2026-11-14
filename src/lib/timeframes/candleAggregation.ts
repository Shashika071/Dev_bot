/**
 * UTC-aligned higher-timeframe candles derived from M1 history.
 * Gap-aware: never fill missing minutes; only contiguous complete buckets qualify as closed.
 */
import type { Candle } from '../types'
import { isValidCandle } from '../deriv/candles'

// Both the VPS and browser have this much M1 history at a fully warmed decision.
export const CONTEXT_M1_HISTORY = 1000
// Reserve the current partial M15 and the oldest partial M15 when anchoring the window.
const COMPLETE_CONTEXT_MINUTES = Math.floor((CONTEXT_M1_HISTORY - 15) / 15) * 15

export type TfMinutes = 5 | 15

export interface AggregatedBar {
  /** UTC open epoch seconds */
  time: number
  open: number
  high: number
  low: number
  close: number
  isClosed: boolean
  /** How many M1 bars contributed */
  sourceCount: number
}

function periodSeconds(tf: TfMinutes): number {
  return tf * 60
}

/** UTC-aligned HTF open for an M1 open time. */
export function htfBucket(time: number, tf: TfMinutes): number {
  const p = periodSeconds(tf)
  return Math.floor(time / p) * p
}

function aggregateBucket(bars: Candle[], isClosed: boolean): AggregatedBar {
  return {
    time: bars[0]!.time,
    open: bars[0]!.open,
    high: Math.max(...bars.map(c => c.high)),
    low: Math.min(...bars.map(c => c.low)),
    close: bars.at(-1)!.close,
    isClosed,
    sourceCount: bars.length,
  }
}

/** Split the most recent consecutive minutes at the last observed HTF close.
 * The partial bucket is valid only when observed from its first minute.
 */
export function timeframeMinutes(m1: Candle[], tf: TfMinutes, maxCompletedMinutes = Infinity) {
  const last = m1.at(-1)
  if (!last || !isValidCandle(last)) return { completed: [], forming: null }
  const boundary = htfBucket(last.time + (last.isClosed ? 60 : 0), tf)
  const earliest = boundary - maxCompletedMinutes * 60
  let start = m1.length - 1
  while (start > 0 && m1[start]!.time > earliest) {
    const previous = m1[start - 1]!
    if (!previous.isClosed || !isValidCandle(previous) || previous.time + 60 !== m1[start]!.time) break
    start--
  }
  let end = m1.length
  while (end > start && m1[end - 1]!.time >= boundary) end--
  const partial = m1.slice(end)
  const forming = partial[0]?.time === boundary ? aggregateBucket(partial, false) : null
  // Drop the incomplete *oldest* bucket after a gap or mid-bucket startup.
  while (start < end && m1[start]!.time !== htfBucket(m1[start]!.time, tf)) start++
  return { completed: m1.slice(start, end), forming }
}

export function aggregateCompleteMinutes(minutes: Candle[], tf: TfMinutes): AggregatedBar[] {
  const bars: AggregatedBar[] = []
  for (let i = 0; i + tf <= minutes.length; i += tf) {
    bars.push(aggregateBucket(minutes.slice(i, i + tf), true))
  }
  return bars
}

export function aggregateClosedBars(m1: Candle[], tf: TfMinutes): AggregatedBar[] {
  return aggregateCompleteMinutes(timeframeMinutes(m1, tf).completed, tf)
}

export function aggregateThroughLive(m1: Candle[], tf: TfMinutes) {
  const { completed, forming } = timeframeMinutes(m1, tf)
  return { completed: aggregateCompleteMinutes(completed, tf), forming }
}

export function contextMinutes(m1: Candle[], tf: TfMinutes) {
  return timeframeMinutes(m1, tf, COMPLETE_CONTEXT_MINUTES)
}

/** Seconds remaining until the forming HTF candle's UTC close. */
export function secondsUntilHtfClose(formingOpen: number, tf: TfMinutes, nowEpoch: number): number {
  const closeAt = formingOpen + periodSeconds(tf)
  return Math.max(0, closeAt - nowEpoch)
}

export function formatHtfCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}m ${r.toString().padStart(2, '0')}s`
}
