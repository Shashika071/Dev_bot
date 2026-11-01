/**
 * Full Stochastic %K/%D: rawK over `kPeriod` (includes signal bar),
 * K = SMA(rawK, kSmooth), D = SMA(K, dSmooth). Defaults 14,3,3.
 */
export function fullStochastic(
  highs: number[],
  lows: number[],
  closes: number[],
  kPeriod = 14,
  kSmooth = 3,
  dSmooth = 3,
): { rawK: (number | null)[]; k: (number | null)[]; d: (number | null)[] } {
  const n = closes.length
  const rawK: (number | null)[] = new Array(n).fill(null)
  for (let i = kPeriod - 1; i < n; i++) {
    let hi = -Infinity
    let lo = Infinity
    for (let j = i - kPeriod + 1; j <= i; j++) {
      hi = Math.max(hi, highs[j]!)
      lo = Math.min(lo, lows[j]!)
    }
    const range = hi - lo
    if (!(range > 0)) {
      rawK[i] = null
      continue
    }
    rawK[i] = 100 * (closes[i]! - lo) / range
  }
  const k = smaNullable(rawK, kSmooth)
  const d = smaNullable(k, dSmooth)
  return { rawK, k, d }
}

function smaNullable(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0
    let ok = true
    for (let j = i - period + 1; j <= i; j++) {
      const v = values[j]
      if (v == null || !Number.isFinite(v)) {
        ok = false
        break
      }
      sum += v
    }
    if (ok) out[i] = sum / period
  }
  return out
}
