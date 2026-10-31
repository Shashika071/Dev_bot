import type { Candle } from '../types'

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

export function computeSma20(candles: Candle[], period = 20): {
  sma: (number | null)[]
  rising: boolean[]
  falling: boolean[]
} {
  const closes = candles.map((c) => c.close)
  const s = sma(closes, period)
  const rising = new Array(candles.length).fill(false)
  const falling = new Array(candles.length).fill(false)
  for (let i = 1; i < candles.length; i++) {
    if (s[i] != null && s[i - 1] != null) {
      rising[i] = (s[i] as number) > (s[i - 1] as number)
      falling[i] = (s[i] as number) < (s[i - 1] as number)
    }
  }
  return { sma: s, rising, falling }
}
