/**
 * KivancOzbilgic SuperTrend — ATR 7, Mult 2, HL2, Wilder ATR
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

  for (let i = 0; i < n; i++) {
    if (atr[i] == null) continue
    const hl2 = (highs[i] + lows[i]) / 2
    const basicUb = hl2 + multiplier * (atr[i] as number)
    const basicLb = hl2 - multiplier * (atr[i] as number)

    if (i === 0 || finalUb[i - 1] == null) {
      finalUb[i] = basicUb
      finalLb[i] = basicLb
    } else {
      finalUb[i] =
        basicUb < (finalUb[i - 1] as number) || closes[i - 1] > (finalUb[i - 1] as number)
          ? basicUb
          : (finalUb[i - 1] as number)
      finalLb[i] =
        basicLb > (finalLb[i - 1] as number) || closes[i - 1] < (finalLb[i - 1] as number)
          ? basicLb
          : (finalLb[i - 1] as number)
    }

    if (i === 0 || line[i - 1] == null) {
      dir[i] = 1
      line[i] = finalLb[i]
    } else if (line[i - 1] === finalUb[i - 1]) {
      if (closes[i] > (finalUb[i] as number)) {
        dir[i] = 1
        line[i] = finalLb[i]
      } else {
        dir[i] = -1
        line[i] = finalUb[i]
      }
    } else {
      if (closes[i] < (finalLb[i] as number)) {
        dir[i] = -1
        line[i] = finalUb[i]
      } else {
        dir[i] = 1
        line[i] = finalLb[i]
      }
    }
  }

  return { line, dir }
}
