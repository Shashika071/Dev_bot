/**
 * Commodity Channel Index.
 * CCI = (TP - SMA(TP, period)) / (0.015 * MAD)
 * MAD = mean absolute deviation of the same period's typical prices vs their SMA.
 */
export function cci(
  highs: number[],
  lows: number[],
  closes: number[],
  period = 20,
): (number | null)[] {
  const n = closes.length
  const tp = highs.map((h, i) => (h + lows[i]! + closes[i]!) / 3)
  const out: (number | null)[] = new Array(n).fill(null)
  for (let i = period - 1; i < n; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += tp[j]!
    const mean = sum / period
    let madSum = 0
    for (let j = i - period + 1; j <= i; j++) madSum += Math.abs(tp[j]! - mean)
    const mad = madSum / period
    if (!(mad > 0)) {
      out[i] = null
      continue
    }
    out[i] = (tp[i]! - mean) / (0.015 * mad)
  }
  return out
}
