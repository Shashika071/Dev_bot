import type { Candle, FinalSignal } from '../types'
import { runTouchBacktest } from './touchBacktest'

/** Live confidence target: favorable wick touch within next N minutes. */
export const TOUCH_TARGET = 0.05
export const TOUCH_WINDOW_MINUTES = 7

/** 1-year R_100 M1 prior (UT+ST+SMA20+AO+RSI), touch ≥ 0.05 within 7 minutes. */
export const YEAR_PRIOR = {
  overall: { n: 5244, rate: 0.9441 },
  rise: { n: 2661, rate: 0.9387 },
  fall: { n: 2583, rate: 0.9497 },
  sampleDays: 365,
} as const

export interface SideConfidence {
  rate: number
  n: number
  source: 'year' | 'session'
}

export interface TouchConfidence {
  target: number
  windowMinutes: number
  rise: SideConfidence
  fall: SideConfidence
  overall: SideConfidence
  sessionOverall: SideConfidence | null
}

export function yearTouchConfidence(): TouchConfidence {
  return {
    target: TOUCH_TARGET,
    windowMinutes: TOUCH_WINDOW_MINUTES,
    rise: { ...YEAR_PRIOR.rise, source: 'year' },
    fall: { ...YEAR_PRIOR.fall, source: 'year' },
    overall: { ...YEAR_PRIOR.overall, source: 'year' },
    sessionOverall: null,
  }
}

/** Prefer year prior; overlay session rate when enough completed outcomes exist. */
export function computeTouchConfidence(closedCandles: Candle[], minSessionN = 20): TouchConfidence {
  const base = yearTouchConfidence()
  if (closedCandles.length < 80) return base
  try {
    const { report } = runTouchBacktest(closedCandles, TOUCH_TARGET, TOUCH_WINDOW_MINUTES)
    const sessionOverall =
      report.overall.touchRate != null && report.overall.n > 0
        ? { rate: report.overall.touchRate, n: report.overall.n, source: 'session' as const }
        : null
    const rise =
      report.rise.touchRate != null && report.rise.n >= minSessionN
        ? { rate: report.rise.touchRate, n: report.rise.n, source: 'session' as const }
        : base.rise
    const fall =
      report.fall.touchRate != null && report.fall.n >= minSessionN
        ? { rate: report.fall.touchRate, n: report.fall.n, source: 'session' as const }
        : base.fall
    const overall =
      sessionOverall && sessionOverall.n >= minSessionN ? sessionOverall : base.overall
    return {
      target: TOUCH_TARGET,
      windowMinutes: TOUCH_WINDOW_MINUTES,
      rise,
      fall,
      overall,
      sessionOverall,
    }
  } catch {
    return base
  }
}

export function confidenceForSide(conf: TouchConfidence, side: FinalSignal): SideConfidence | null {
  if (side === 'RISE') return conf.rise
  if (side === 'FALL') return conf.fall
  return null
}

/** Live tick progress toward ±TOUCH_TARGET from signal entry. */
export function liveTouchProgress(
  side: 'RISE' | 'FALL',
  entry: number,
  price: number,
  bestFavorable: number,
): { favorable: number; best: number; progress: number; hit: boolean } {
  const tickFav = side === 'RISE' ? price - entry : entry - price
  const favorable = Math.max(0, tickFav)
  const best = Math.max(bestFavorable, favorable)
  const progress = Math.min(1, best / TOUCH_TARGET)
  return { favorable, best, progress, hit: best + 1e-9 >= TOUCH_TARGET }
}

export function formatPct(rate: number): string {
  return `${(100 * rate).toFixed(1)}%`
}
