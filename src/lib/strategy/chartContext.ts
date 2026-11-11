import type { Candle } from '../types'
import { isValidCandle } from '../deriv/candles'

export type ChartContextLabel =
  | 'Up breakout' | 'Down breakout' | 'Above range' | 'Below range'
  | 'Upper rejection' | 'Lower rejection' | 'Both wicks rejected' | 'Inside range'

export interface ChartContext {
  time: number
  high: number
  low: number
  label: ChartContextLabel
  isClosed: boolean
  /** Unclamped: values outside 0..100 indicate a close beyond the prior range. */
  locationPct: number | null
  description: string
  chop14: number | null
}

/** Display-only chart geometry. Never authorizes or modifies a C5 trade. */
export function contextAt(candles: readonly Candle[], index: number, allowForming = false): ChartContext | null {
  if (!Number.isInteger(index) || index < 20 || index >= candles.length) return null
  const current = candles[index]!
  if (!isValidCandle(current) || (!current.isClosed && !allowForming)) return null
  const prior = candles.slice(index - 20, index)
  if (prior.some((c, j) => !c.isClosed || !isValidCandle(c) || c.time !== current.time - (20 - j) * 60)) return null

  const high = Math.max(...prior.map(c => c.high))
  const low = Math.min(...prior.map(c => c.low))
  let label: ChartContextLabel
  let description: string
  if (current.close > high) {
    label = current.isClosed ? 'Up breakout' : 'Above range'
    description = current.isClosed ? 'The candle closed above the previous 20-bar high.' : 'Price is above the previous 20-bar high; the candle has not closed.'
  } else if (current.close < low) {
    label = current.isClosed ? 'Down breakout' : 'Below range'
    description = current.isClosed ? 'The candle closed below the previous 20-bar low.' : 'Price is below the previous 20-bar low; the candle has not closed.'
  } else if (current.high > high && current.low < low) {
    label = 'Both wicks rejected'
    description = 'Price crossed both range boundaries and returned inside.'
  } else if (current.high > high) {
    label = 'Upper rejection'
    description = 'Price crossed the prior high and returned inside the range.'
  } else if (current.low < low) {
    label = 'Lower rejection'
    description = 'Price crossed the prior low and returned inside the range.'
  } else {
    label = 'Inside range'
    description = 'Price remains within the previous 20-bar range.'
  }
  if (!current.isClosed && current.close <= high && current.close >= low) {
    description += ' Provisional: this candle is still forming.'
  }

  // TradingView CHOP(14): 100 * log10(sum(TR,14) / range(14)) / log10(14).
  // The latest bar is included, so forming CHOP is provisional alongside its context.
  let totalTr = 0, maxHigh = -Infinity, minLow = Infinity
  for (let j = index - 13; j <= index; j++) {
    const c = candles[j]!, previousClose = candles[j - 1]!.close
    totalTr += Math.max(c.high - c.low, Math.abs(c.high - previousClose), Math.abs(c.low - previousClose))
    maxHigh = Math.max(maxHigh, c.high)
    minLow = Math.min(minLow, c.low)
  }
  const range14 = maxHigh - minLow
  const chop14 = range14 > 0 && totalTr > 0 ? 100 * Math.log10(totalTr / range14) / Math.log10(14) : null
  return {
    time: current.time, high, low, label, isClosed: current.isClosed,
    locationPct: high > low ? (current.close - low) / (high - low) * 100 : null,
    description, chop14,
  }
}

/** Aligned series; unavailable history remains null rather than joining across a gap. */
export function getChartContexts(candles: readonly Candle[], allowForming = false): (ChartContext | null)[] {
  return candles.map((_, index) => contextAt(candles, index, allowForming))
}
