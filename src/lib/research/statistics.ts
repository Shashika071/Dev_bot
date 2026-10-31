/** Descriptive interval; serially correlated trades can make it too optimistic. */
export function wilsonInterval(wins: number, n: number): [number, number] | null {
  if (!n) return null
  const z = 1.959963984540054
  const p = wins / n
  const d = 1 + z * z / n
  const center = (p + z * z / (2 * n)) / d
  const radius = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
  return [Math.max(0, center - radius), Math.min(1, center + radius)]
}

export function summarize(outcomes: boolean[], netWinReturn: number) {
  if (!Number.isFinite(netWinReturn) || netWinReturn <= 0) throw new Error('Net win return must be positive')
  const wins = outcomes.filter(Boolean).length
  const n = outcomes.length
  let equity = 0, peak = 0, maxDrawdown = 0, streak = 0, maxLosingStreak = 0
  for (const won of outcomes) {
    equity += won ? netWinReturn : -1
    peak = Math.max(peak, equity)
    maxDrawdown = Math.max(maxDrawdown, peak - equity)
    streak = won ? 0 : streak + 1
    maxLosingStreak = Math.max(maxLosingStreak, streak)
  }
  return {
    n, wins, losses: n - wins, winRate: n ? wins / n : null,
    interval95: wilsonInterval(wins, n), breakEven: 1 / (1 + netWinReturn),
    netUnits: equity, expectancy: n ? equity / n : null, maxDrawdown, maxLosingStreak,
  }
}
