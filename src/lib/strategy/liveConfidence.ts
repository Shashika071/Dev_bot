/**
 * Real-time setup strength from the exact 5 signal conditions
 * on the current (forming) minute — not historical backtest rates.
 */
import type { Candle, ConfirmationFlags } from '../types'
import { closedSegments } from '../deriv/candles'
import { enrich, WARMUP_BARS, type EnrichedSeries } from './signalEngine'

export interface SideSetup {
  side: 'RISE' | 'FALL'
  /** 0–1 from how many of the 5 conditions are true right now */
  confidence: number
  passed: number
  total: number
  flags: ConfirmationFlags
  labels: { key: keyof ConfirmationFlags; ok: boolean; text: string }[]
}

export interface LiveSetupConfidence {
  ready: boolean
  candleTime: number | null
  /** Forming minute (not yet a tradable alert until close + all 5) */
  forming: boolean
  rise: SideSetup
  fall: SideSetup
  /** Stronger side right now */
  lean: 'RISE' | 'FALL' | 'NONE'
}

const RISE_LABELS: Record<keyof ConfirmationFlags, string> = {
  ut: 'New UT BUY',
  supertrend: 'Supertrend bullish',
  sma: 'Close > rising SMA20',
  ao: 'AO > 0 & rising',
  rsi: '52 < RSI < 70',
}

const FALL_LABELS: Record<keyof ConfirmationFlags, string> = {
  ut: 'New UT SELL',
  supertrend: 'Supertrend bearish',
  sma: 'Close < falling SMA20',
  ao: 'AO < 0 & falling',
  rsi: '30 < RSI < 48',
}

function scoreRise(series: EnrichedSeries, i: number): SideSetup {
  const close = series.candles[i].close
  const sma = series.sma20[i]
  const ao = series.ao[i]
  const aoPrev = i > 0 ? series.ao[i - 1] : null
  const rsi = series.rsi[i]
  const flags: ConfirmationFlags = {
    ut: series.utBuy[i] && !series.utSell[i],
    supertrend: series.stDir[i] === 1,
    sma: sma != null && close > sma && series.smaRising[i],
    ao: ao != null && aoPrev != null && ao > 0 && ao > aoPrev,
    rsi: rsi != null && rsi > 52 && rsi < 70,
  }
  const keys = Object.keys(flags) as (keyof ConfirmationFlags)[]
  const passed = keys.filter((k) => flags[k]).length
  return {
    side: 'RISE',
    confidence: passed / keys.length,
    passed,
    total: keys.length,
    flags,
    labels: keys.map((key) => ({ key, ok: flags[key], text: RISE_LABELS[key] })),
  }
}

function scoreFall(series: EnrichedSeries, i: number): SideSetup {
  const close = series.candles[i].close
  const sma = series.sma20[i]
  const ao = series.ao[i]
  const aoPrev = i > 0 ? series.ao[i - 1] : null
  const rsi = series.rsi[i]
  const flags: ConfirmationFlags = {
    ut: series.utSell[i] && !series.utBuy[i],
    supertrend: series.stDir[i] === -1,
    sma: sma != null && close < sma && series.smaFalling[i],
    ao: ao != null && aoPrev != null && ao < 0 && ao < aoPrev,
    rsi: rsi != null && rsi < 48 && rsi > 30,
  }
  const keys = Object.keys(flags) as (keyof ConfirmationFlags)[]
  const passed = keys.filter((k) => flags[k]).length
  return {
    side: 'FALL',
    confidence: passed / keys.length,
    passed,
    total: keys.length,
    flags,
    labels: keys.map((key) => ({ key, ok: flags[key], text: FALL_LABELS[key] })),
  }
}

/**
 * Score the latest minute in real time (includes the open forming candle).
 * Alert still only fires on a completed candle with all 5 — this is preview strength.
 */
export function computeLiveSetupConfidence(candles: Candle[]): LiveSetupConfidence {
  const empty: LiveSetupConfidence = {
    ready: false,
    candleTime: null,
    forming: false,
    rise: {
      side: 'RISE', confidence: 0, passed: 0, total: 5,
      flags: { ut: false, supertrend: false, sma: false, ao: false, rsi: false },
      labels: (Object.keys(RISE_LABELS) as (keyof ConfirmationFlags)[]).map((key) => ({
        key, ok: false, text: RISE_LABELS[key],
      })),
    },
    fall: {
      side: 'FALL', confidence: 0, passed: 0, total: 5,
      flags: { ut: false, supertrend: false, sma: false, ao: false, rsi: false },
      labels: (Object.keys(FALL_LABELS) as (keyof ConfirmationFlags)[]).map((key) => ({
        key, ok: false, text: FALL_LABELS[key],
      })),
    },
    lean: 'NONE',
  }

  if (candles.length < WARMUP_BARS + 1) return empty

  // Keep enough history for indicators; avoid re-scoring the full year every tick.
  const window = candles.length > WARMUP_BARS + 200 ? candles.slice(-(WARMUP_BARS + 200)) : candles

  // Treat forming candle as evaluable for live preview only.
  const preview = window.map((c, i) =>
    i === window.length - 1 && !c.isClosed ? { ...c, isClosed: true } : c,
  )
  const segment = closedSegments(preview).at(-1)
  if (!segment || segment.length <= WARMUP_BARS) return empty

  const series = enrich(segment)
  const i = segment.length - 1
  const rise = scoreRise(series, i)
  const fall = scoreFall(series, i)
  let lean: LiveSetupConfidence['lean'] = 'NONE'
  if (rise.confidence > fall.confidence && rise.confidence > 0) lean = 'RISE'
  else if (fall.confidence > rise.confidence && fall.confidence > 0) lean = 'FALL'

  return {
    ready: true,
    candleTime: segment[i].time,
    forming: !candles[candles.length - 1]?.isClosed,
    rise,
    fall,
    lean,
  }
}
