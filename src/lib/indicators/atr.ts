/** Wilder / TradingView RMA ATR */

export function trueRange(
  high: number,
  low: number,
  prevClose: number | null,
): number {
  if (prevClose == null) return high - low
  return Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose))
}

export function atrRma(
  highs: number[],
  lows: number[],
  closes: number[],
  period: number,
): (number | null)[] {
  const n = closes.length
  const tr: number[] = new Array(n)
  const atr: (number | null)[] = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    tr[i] = trueRange(highs[i], lows[i], i === 0 ? null : closes[i - 1])
  }
  if (n < period) return atr
  let sum = 0
  for (let i = 0; i < period; i++) sum += tr[i]
  atr[period - 1] = sum / period
  const alpha = 1 / period
  for (let i = period; i < n; i++) {
    atr[i] = (atr[i - 1] as number) + alpha * (tr[i] - (atr[i - 1] as number))
  }
  return atr
}
