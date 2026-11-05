/**
 * Live C5 setup preview (forming or closed) — not an order trigger by itself.
 * Required score = UT/ST/SMA/RSI (4). AO is informational only.
 */
import type { Candle, ConfirmationFlags } from '../types'
import { closedSegments } from '../deriv/candles'
import { enrich, WARMUP_BARS, type EnrichedSeries } from './signalEngine'
import { fallFlagsAt, riseFlagsAt } from './conditionFlags'
import { C5_CALC_WINDOW, C5_REQUIRED, evaluateC5All } from './c5Strategy'

export interface SideSetup {
  side: 'RISE' | 'FALL'
  /** requiredPassed / 4 — display only; never authorizes trades alone */
  confidence: number
  passed: number
  total: number
  flags: ConfirmationFlags
  /** Required C5 checks only (excludes AO) */
  requiredLabels: { key: keyof ConfirmationFlags; ok: boolean; text: string }[]
  /** @deprecated alias of requiredLabels for older UI */
  labels: { key: keyof ConfirmationFlags; ok: boolean; text: string }[]
  aoOptional: { ok: boolean; text: string; value: number | null }
  nearRemaining: { key: keyof ConfirmationFlags; text: string }[]
}

export const READY_ALERT_PCT = 75

export interface LiveSetupConfidence {
  ready: boolean
  candleTime: number | null
  /** Forming minute preview — never actionable for auto-entry */
  forming: boolean
  rise: SideSetup
  fall: SideSetup
  lean: 'RISE' | 'FALL' | 'NONE'
  strategyId: 'C5'
  strategyLabel: string
}

const RISE_REQUIRED_LABELS: Record<'ut' | 'supertrend' | 'sma' | 'rsi', string> = {
  ut: 'Fresh UT BUY crossover',
  supertrend: 'Supertrend bullish (ATR7×2)',
  sma: 'Close > rising SMA20',
  rsi: '52 < RSI14 < 70',
}

const FALL_REQUIRED_LABELS: Record<'ut' | 'supertrend' | 'sma' | 'rsi', string> = {
  ut: 'Fresh UT SELL crossover',
  supertrend: 'Supertrend bearish (ATR7×2)',
  sma: 'Close < falling SMA20',
  rsi: '30 < RSI14 < 48',
}

function scoreSide(
  side: 'RISE' | 'FALL',
  series: EnrichedSeries,
  i: number,
): SideSetup {
  const flags = side === 'RISE' ? riseFlagsAt(series, i) : fallFlagsAt(series, i)
  const labelsSrc = side === 'RISE' ? RISE_REQUIRED_LABELS : FALL_REQUIRED_LABELS
  const requiredLabels = (C5_REQUIRED as ('ut' | 'supertrend' | 'sma' | 'rsi')[]).map((key) => ({
    key,
    ok: flags[key],
    text: labelsSrc[key],
  }))
  const passed = requiredLabels.filter((r) => r.ok).length
  const ao = series.ao[i] ?? null
  return {
    side,
    confidence: passed / C5_REQUIRED.length,
    passed,
    total: C5_REQUIRED.length,
    flags,
    requiredLabels,
    labels: requiredLabels,
    aoOptional: {
      ok: flags.ao,
      value: ao,
      text: side === 'RISE'
        ? 'Optional — AO > 0 & rising (informational)'
        : 'Optional — AO < 0 & falling (informational)',
    },
    nearRemaining: [],
  }
}

function emptySide(side: 'RISE' | 'FALL'): SideSetup {
  const labelsSrc = side === 'RISE' ? RISE_REQUIRED_LABELS : FALL_REQUIRED_LABELS
  const requiredLabels = (C5_REQUIRED as ('ut' | 'supertrend' | 'sma' | 'rsi')[]).map((key) => ({
    key,
    ok: false,
    text: labelsSrc[key],
  }))
  return {
    side,
    confidence: 0,
    passed: 0,
    total: 4,
    flags: { ut: false, supertrend: false, sma: false, ao: false, rsi: false },
    requiredLabels,
    labels: requiredLabels,
    aoOptional: {
      ok: false,
      value: null,
      text: 'Optional — informational',
    },
    nearRemaining: [],
  }
}

/**
 * Score the latest minute in real time (includes forming candle as preview only).
 * Does not authorize automatic trades.
 */
export function computeLiveSetupConfidence(candles: Candle[]): LiveSetupConfidence {
  const empty: LiveSetupConfidence = {
    ready: false,
    candleTime: null,
    forming: false,
    rise: emptySide('RISE'),
    fall: emptySide('FALL'),
    lean: 'NONE',
    strategyId: 'C5',
    strategyLabel: 'C5 — UT + Supertrend + SMA20 + RSI14',
  }

  if (candles.length < WARMUP_BARS + 1) return empty

  const window = candles.length > C5_CALC_WINDOW ? candles.slice(-C5_CALC_WINDOW) : candles
  const preview = window.map((c, i) =>
    i === window.length - 1 && !c.isClosed ? { ...c, isClosed: true } : c,
  )
  const segment = closedSegments(preview).at(-1)
  if (!segment || segment.length <= WARMUP_BARS) return empty

  const series = enrich(segment)
  const i = segment.length - 1
  const rise = scoreSide('RISE', series, i)
  const fall = scoreSide('FALL', series, i)
  let lean: LiveSetupConfidence['lean'] = 'NONE'
  if (rise.passed > fall.passed && rise.passed > 0) lean = 'RISE'
  else if (fall.passed > rise.passed && fall.passed > 0) lean = 'FALL'

  return {
    ready: true,
    candleTime: segment[i]!.time,
    forming: !candles[candles.length - 1]?.isClosed,
    rise,
    fall,
    lean,
    strategyId: 'C5',
    strategyLabel: 'C5 — UT + Supertrend + SMA20 + RSI14',
  }
}

export type SetupChartMarker = {
  time: number
  side: 'RISE' | 'FALL'
  confidence: number
  passed: number
  total: number
  full: boolean
  strategyId: 'C5'
}

/** Closed-candle C5 markers (4/4 required). AO never required. */
export function computeSetupChartMarkers(
  candles: Candle[],
  _minConfidence = 1,
  maxBars = 800,
): SetupChartMarker[] {
  const closed = candles.filter((c) => c.isClosed)
  const window = closed.length > maxBars ? closed.slice(-maxBars) : closed
  const out: SetupChartMarker[] = []

  for (const ev of evaluateC5All(window)) {
    if (!ev.isQualified || (ev.final !== 'RISE' && ev.final !== 'FALL')) continue
    out.push({
      time: ev.time,
      side: ev.final,
      confidence: (ev.requiredPassed ?? 4) / 4,
      passed: ev.requiredPassed ?? 4,
      total: 4,
      full: true,
      strategyId: 'C5',
    })
  }
  return out
}
