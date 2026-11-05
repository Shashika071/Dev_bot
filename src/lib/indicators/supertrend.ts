/**
 * KivancOzbilgic SuperTrend — ATR 7, Mult 2, HL2, Wilder ATR
 * https://www.tradingview.com/script/r6dAP7yi/
 * Reversals use the previous bands; direction is tracked explicitly.
 */
import type { Candle, TrendDir } from '../types'
import { atrRma } from './atr'

export interface SupertrendResult {
  line: (number | null)[]
  dir: TrendDir[]
}

export function computeSupertrend(
  candles: Candle[],
  atrPeriod = 7,
  multiplier = 2,
): SupertrendResult {
  const n = candles.length
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr = atrRma(highs, lows, closes, atrPeriod)

  const line: (number | null)[] = new Array(n).fill(null)
  const dir: TrendDir[] = new Array(n).fill(0)
  const finalUb: (number | null)[] = new Array(n).fill(null)
  const finalLb: (number | null)[] = new Array(n).fill(null)
  let trend: TrendDir = 1

  for (let i = 0; i < n; i++) {
    if (atr[i] == null) continue
    const hl2 = (highs[i] + lows[i]) / 2
    const basicUb = hl2 + multiplier * (atr[i] as number)
    const basicLb = hl2 - multiplier * (atr[i] as number)

    // The first valid ATR bar falls back to its basic bands (Pine's nz).
    const prevUb = finalUb[i - 1] ?? basicUb
    const prevLb = finalLb[i - 1] ?? basicLb
    finalUb[i] = i > 0 && closes[i - 1] < prevUb
      ? Math.min(basicUb, prevUb)
      : basicUb
    finalLb[i] = i > 0 && closes[i - 1] > prevLb
      ? Math.max(basicLb, prevLb)
      : basicLb

    if (trend === -1 && closes[i] > prevUb) trend = 1
    else if (trend === 1 && closes[i] < prevLb) trend = -1
    dir[i] = trend
    line[i] = trend === 1 ? finalLb[i] : finalUb[i]
  }

  return { line, dir }
}
