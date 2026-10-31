/**
 * QuantNomad UT Bot Alerts
 * Key=2, ATR=10, Heikin Ashi=OFF, source=close
 */
import type { Candle } from '../types'
import { atrRma } from './atr'

export interface UtBotResult {
  trail: (number | null)[]
  buy: boolean[]
  sell: boolean[]
  direction: number[]
}

export function computeUtBot(
  candles: Candle[],
  keyValue = 2,
  atrPeriod = 10,
): UtBotResult {
  const n = candles.length
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr = atrRma(highs, lows, closes, atrPeriod)

  const trail: (number | null)[] = new Array(n).fill(null)
  const buy = new Array(n).fill(false)
  const sell = new Array(n).fill(false)
  const direction = new Array(n).fill(0)

  for (let i = 0; i < n; i++) {
    if (atr[i] == null) continue
    const nLoss = keyValue * (atr[i] as number)
    const prev = i === 0 || trail[i - 1] == null ? 0 : (trail[i - 1] as number)
    const src = closes[i]
    const prevSrc = i > 0 ? closes[i - 1] : src

    if (src > prev && prevSrc > prev) trail[i] = Math.max(prev, src - nLoss)
    else if (src < prev && prevSrc < prev) trail[i] = Math.min(prev, src + nLoss)
    else if (src > prev) trail[i] = src - nLoss
    else trail[i] = src + nLoss

    const t = trail[i] as number
    direction[i] = src > t ? 1 : src < t ? -1 : i > 0 ? direction[i - 1] : 0

    if (i > 0 && trail[i] != null && trail[i - 1] != null) {
      if (closes[i - 1] <= (trail[i - 1] as number) && closes[i] > t) buy[i] = true
      if (closes[i - 1] >= (trail[i - 1] as number) && closes[i] < t) sell[i] = true
    }
  }

  return { trail, buy, sell, direction }
}
