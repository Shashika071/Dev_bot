/** Exponential moving average (seeded with SMA of first `period` values). */

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  if (values.length < period || period < 1) return out
  let sum = 0
  for (let i = 0; i < period; i++) sum += values[i]!
  let prev = sum / period
  out[period - 1] = prev
  const k = 2 / (period + 1)
  for (let i = period; i < values.length; i++) {
    prev = values[i]! * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export function emaRising(series: (number | null)[], i: number): boolean {
  if (i < 1) return false
  const a = series[i]
  const b = series[i - 1]
  return a != null && b != null && a > b
}

export function emaFalling(series: (number | null)[], i: number): boolean {
  if (i < 1) return false
  const a = series[i]
  const b = series[i - 1]
  return a != null && b != null && a < b
}
