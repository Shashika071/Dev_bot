import type { Candle, ConfirmationFlags, SignalEvaluation } from '../types'
import { isValidCandle } from '../deriv/candles'
import { evaluateAll } from '../strategy/signalEngine'
import { summarize } from './statistics'

const filters: (keyof ConfirmationFlags)[] = ['ut', 'supertrend', 'sma', 'ao', 'rsi']
export const variants = [
  { name: 'Current five rules', required: filters },
  { name: 'UT only', required: ['ut'] as (keyof ConfirmationFlags)[] },
  ...filters.filter((key) => key !== 'ut').map((key) => ({ name: `Without ${key}`, required: filters.filter((f) => f !== key) })),
]

export function accepts(ev: SignalEvaluation, required: (keyof ConfirmationFlags)[]) {
  return ev.rawUt !== 'NONE' && required.every((key) => ev.confirmations[key])
}

/** Research proxy only: exact shared rules, strictly chronological, no parameter fitting. */
export function runBacktest(candles: Candle[], netWinReturn = 0.8) {
  if (candles.some((c, i) => !isValidCandle(c) || (i > 0 && c.time <= candles[i - 1].time))) {
    throw new Error('Dataset must contain valid, unique, ascending M1 candles')
  }
  const closed = candles.filter((c) => c.isClosed)
  if (closed.length < 200) throw new Error('At least 200 completed candles required')
  const evaluations = evaluateAll(candles)
  const byTime = new Map(closed.map((c) => [c.time, c]))
  const splitTime = closed[Math.floor(closed.length * 0.7)].time
  const quarters = [0, 1, 2, 3].map((i) => ({
    name: `Chronological quarter ${i + 1}`,
    start: closed[Math.floor(closed.length * i / 4)].time,
    end: i === 3 ? closed.at(-1)!.time + 60 : closed[Math.floor(closed.length * (i + 1) / 4)].time,
  }))
  const windows = [
    { name: 'Development first 70%', start: closed[0].time, end: splitTime },
    { name: 'Holdout final 30%', start: splitTime, end: closed.at(-1)!.time + 60 },
    ...quarters,
  ]
  const rows = windows.flatMap((window) => variants.map((variant) => {
    const outcomes: boolean[] = []
    for (const ev of evaluations) {
      // Purge the boundary: both the signal and its full outcome candle belong to this window.
      if (ev.time < window.start || ev.time + 60 >= window.end || !accepts(ev, variant.required)) continue
      const next = byTime.get(ev.time + 60)
      if (!next) continue
      outcomes.push(ev.rawUt === 'BUY' ? next.close > ev.close : next.close < ev.close)
    }
    return { window: window.name, variant: variant.name, ...summarize(outcomes, netWinReturn) }
  }))
  const gaps = closed.reduce((n, c, i) => n + (i > 0 && c.time !== closed[i - 1].time + 60 ? 1 : 0), 0)
  return {
    measurement: 'Historical signal close to next completed M1 close; NOT contract settlements or executable entries.',
    warning: 'No parameter optimization performed. Holdout is only unseen if you have not used it to select rules. Variant comparisons are exploratory; validate any selected change on a later untouched period. Wilson intervals assume independent outcomes.',
    symbol: 'R_100', strategy: 'UT(2,10), ST(7,2), SMA20, AO(5,34), RSI14',
    bars: closed.length, gaps, warmupBarsPerSegment: 60,
    startUTC: new Date(closed[0].time * 1000).toISOString(),
    endUTC: new Date((closed.at(-1)!.time + 60) * 1000).toISOString(),
    holdoutStartUTC: new Date(splitTime * 1000).toISOString(),
    assumedNetWinReturn: netWinReturn, rows,
  }
}
