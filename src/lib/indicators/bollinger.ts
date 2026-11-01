import { sma } from './sma'

/** Bollinger bands: SMA ± k * population stdev of last `period` closes. */
export function bollingerBands(
  closes: number[],
  period = 20,
  k = 2,
): {
  mid: (number | null)[]
  upper: (number | null)[]
  lower: (number | null)[]
} {
  const mid = sma(closes, period)
  const upper: (number | null)[] = new Array(closes.length).fill(null)
  const lower: (number | null)[] = new Array(closes.length).fill(null)
  for (let i = period - 1; i < closes.length; i++) {
    const m = mid[i]
    if (m == null) continue
    let sumSq = 0
    for (let j = i - period + 1; j <= i; j++) {
      const d = closes[j]! - m
      sumSq += d * d
    }
    const std = Math.sqrt(sumSq / period)
    upper[i] = m + k * std
    lower[i] = m - k * std
  }
  return { mid, upper, lower }
}
