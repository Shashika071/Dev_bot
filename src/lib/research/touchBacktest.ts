import type { Candle } from '../types'
import { closedSegments, isValidCandle } from '../deriv/candles'
import { DEFAULT_FILTER_CONFIG, type FilterConfig } from '../strategy/filters'
import { evaluateC5All } from '../strategy/c5Strategy'
import { WARMUP_BARS } from '../strategy/signalEngine'
import { wilsonInterval } from './statistics'

export interface TouchObservation {
  signalTime: number
  outcomeTime: number
  outcomeEndTime: number
  horizonMinutes: number
  firstTouchMinute: number | null
  side: 'RISE' | 'FALL'
  signalClose: number
  nextOpen: number
  nextHigh: number
  nextLow: number
  nextClose: number
  target: number
  oppositeTarget: number
  touched: boolean
  oppositeTouched: boolean
  bothTouched: boolean
  touchedFromNextOpen: boolean
  closeDirectionHit: boolean
}

/** Inclusive absolute-price threshold; allow only binary floating-point roundoff. */
export function reaches(distance: number, threshold: number) {
  return distance + 1e-9 >= threshold
}

export function observeTouch(signal: Candle, next: Candle, side: 'RISE' | 'FALL', threshold = 0.03): TouchObservation | null {
  if (!Number.isFinite(threshold) || threshold <= 0) throw new Error('Threshold must be positive')
  if (!signal.isClosed || !next.isClosed || !isValidCandle(signal) || !isValidCandle(next) || next.time !== signal.time + 60) return null
  const rise = side === 'RISE'
  const touched = reaches(rise ? next.high - signal.close : signal.close - next.low, threshold)
  const oppositeTouched = reaches(rise ? signal.close - next.low : next.high - signal.close, threshold)
  return {
    signalTime: signal.time, outcomeTime: next.time, outcomeEndTime: next.time + 60,
    horizonMinutes: 1, firstTouchMinute: touched ? 1 : null, side,
    signalClose: signal.close, nextOpen: next.open, nextHigh: next.high, nextLow: next.low, nextClose: next.close,
    target: signal.close + (rise ? threshold : -threshold), oppositeTarget: signal.close + (rise ? -threshold : threshold),
    touched, oppositeTouched, bothTouched: touched && oppositeTouched,
    touchedFromNextOpen: reaches(rise ? next.high - next.open : next.open - next.low, threshold),
    closeDirectionHit: rise ? next.close > signal.close : next.close < signal.close,
  }
}

/** Every minute must be present and closed; never look through a data gap. */
export function observeTouchWindow(signal: Candle, future: Candle[], side: 'RISE' | 'FALL', threshold = 0.03, minutes = 1): TouchObservation | null {
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw new Error('Minutes must be an integer from 1 to 1440')
  if (future.length !== minutes || future.some((c, i) => !c.isClosed || !isValidCandle(c) || c.time !== signal.time + (i + 1) * 60)) return null
  const combined = {
    ...future[0], high: Math.max(...future.map((c) => c.high)),
    low: Math.min(...future.map((c) => c.low)), close: future.at(-1)!.close,
  }
  const row = observeTouch(signal, combined, side, threshold)
  if (!row) return null
  const first = future.findIndex((c) => reaches(side === 'RISE' ? c.high - signal.close : signal.close - c.low, threshold))
  return { ...row, horizonMinutes: minutes, outcomeEndTime: future.at(-1)!.time + 60, firstTouchMinute: first < 0 ? null : first + 1 }
}

export function summarizeTouches(rows: TouchObservation[]) {
  const n = rows.length
  const touched = rows.filter((r) => r.touched).length
  const oppositeTouched = rows.filter((r) => r.oppositeTouched).length
  const bothTouched = rows.filter((r) => r.bothTouched).length
  const nextOpenTouched = rows.filter((r) => r.touchedFromNextOpen).length
  const closeHits = rows.filter((r) => r.closeDirectionHit).length
  let streak = 0, longestMissStreak = 0
  for (const row of rows) {
    streak = row.touched ? 0 : streak + 1
    longestMissStreak = Math.max(longestMissStreak, streak)
  }
  return {
    n, touched, missed: n - touched, touchRate: n ? touched / n : null,
    interval95: wilsonInterval(touched, n), oppositeTouched, bothTouched,
    onlySignalDirection: touched - bothTouched, onlyOppositeDirection: oppositeTouched - bothTouched,
    neither: n - touched - oppositeTouched + bothTouched,
    nextOpenTouched, nextOpenTouchRate: n ? nextOpenTouched / n : null,
    closeHits, closeHitRate: n ? closeHits / n : null,
    matchedRandomDirectionTouchRate: n ? (touched + oppositeTouched) / (2 * n) : null,
    longestMissStreak,
  }
}

export function runTouchBacktest(
  candles: Candle[],
  threshold = 0.03,
  minutes = 1,
  filterConfig: FilterConfig = DEFAULT_FILTER_CONFIG,
) {
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw new Error('Minutes must be an integer from 1 to 1440')
  if (!Number.isFinite(threshold) || threshold <= 0) throw new Error('Threshold must be positive')
  if (candles.some((c, i) => !isValidCandle(c) || (i > 0 && c.time <= candles[i - 1].time))) {
    throw new Error('Dataset requires valid, unique, ascending M1 candles')
  }
  const closed = candles.filter((c) => c.isClosed)
  if (closed.length <= WARMUP_BARS) throw new Error('Insufficient completed history')
  const cfg: FilterConfig = { ...filterConfig, targetDistance: threshold }
  const rows: TouchObservation[] = []
  let qualified = 0, missingOutcomes = 0, baselineBars = 0, baselineTouches = 0
  const segments = closedSegments(candles)
  const byTime = new Map(closed.map((c) => [c.time, c]))
  for (const segment of segments) {
    for (let i = WARMUP_BARS; i < segment.length; i++) {
      const c = segment[i]!
      const future = segment.slice(i + 1, i + 1 + minutes)
      if (future.length === minutes) {
        const high = Math.max(...future.map((bar) => bar.high))
        const low = Math.min(...future.map((bar) => bar.low))
        baselineBars++
        baselineTouches += Number(reaches(high - c.close, threshold)) + Number(reaches(c.close - low, threshold))
      }
    }
  }
  // Production C5 signals (same as live evaluateC5All / chart markers).
  for (const ev of evaluateC5All(candles)) {
    if (ev.final !== 'RISE' && ev.final !== 'FALL') continue
    const c = byTime.get(ev.time)
    if (!c) continue
    qualified++
    const future: Candle[] = []
    for (let m = 1; m <= minutes; m++) {
      const bar = byTime.get(ev.time + m * 60)
      if (!bar) break
      future.push(bar)
    }
    if (future.length !== minutes) { missingOutcomes++; continue }
    const row = observeTouchWindow(c, future, ev.final, threshold, minutes)
    if (row) rows.push(row)
  }
  const monthly = new Map<string, TouchObservation[]>()
  for (const row of rows) {
    const month = new Date(row.outcomeTime * 1000).toISOString().slice(0, 7)
    const list = monthly.get(month) ?? []
    list.push(row)
    monthly.set(month, list)
  }
  const splitTime = closed[Math.floor(closed.length * 0.7)].time
  return {
    report: {
      symbol: 'R_100', granularitySeconds: 60, horizonMinutes: minutes, threshold, thresholdUnit: 'absolute price points, not percent',
      filterConfig: cfg,
      definition: `A RISE hits if the maximum high across the next ${minutes} completed M1 candles >= signal close + threshold; a FALL hits if the minimum low <= signal close - threshold. End direction uses the final candle close.`,
      limitations: 'Candle excursion proxy, not actual entry or contract settlement. Both barriers can be reached in one minute; OHLC does not generally reveal which came first. No profit or win-rate claim. Wilson intervals assume independent observations. A long-history indicator seed may differ from a restarted live dashboard.',
      bars: closed.length, segments: segments.length, gaps: Math.max(0, segments.length - 1),
      startUTC: new Date(closed[0].time * 1000).toISOString(),
      endExclusiveUTC: new Date((closed.at(-1)!.time + 60) * 1000).toISOString(),
      qualified, missingOutcomes, overall: summarizeTouches(rows),
      rise: summarizeTouches(rows.filter((r) => r.side === 'RISE')),
      fall: summarizeTouches(rows.filter((r) => r.side === 'FALL')),
      chronological: [
        { name: 'Earlier 70% (descriptive)', ...summarizeTouches(rows.filter((r) => r.outcomeEndTime <= splitTime)) },
        { name: 'Latest 30% (descriptive, not a fresh unseen holdout)', ...summarizeTouches(rows.filter((r) => r.signalTime >= splitTime)) },
      ],
      monthly: [...monthly.entries()].map(([month, list]) => ({ monthUTC: month, ...summarizeTouches(list) })),
      touchTiming: Array.from({ length: minutes }, (_, i) => ({
        minute: i + 1,
        firstTouches: rows.filter((r) => r.firstTouchMinute === i + 1).length,
        cumulativeTouches: rows.filter((r) => r.firstTouchMinute != null && r.firstTouchMinute <= i + 1).length,
      })),
      allCandleRandomDirectionBaseline: { bars: baselineBars, touchRate: baselineBars ? baselineTouches / (2 * baselineBars) : null },
    }, rows,
  }
}
