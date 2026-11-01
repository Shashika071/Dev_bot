/**
 * Shared five-condition flags for live confidence and research masks.
 * Does not require UT as a gate — callers decide which flags must pass.
 */
import type { ConfirmationFlags } from '../types'
import type { EnrichedSeries } from './signalEngine'

export const CONDITION_KEYS: (keyof ConfirmationFlags)[] = [
  'ut', 'supertrend', 'sma', 'ao', 'rsi',
]

export function riseFlagsAt(series: EnrichedSeries, i: number): ConfirmationFlags {
  const close = series.candles[i].close
  const sma = series.sma20[i]
  const ao = series.ao[i]
  const aoPrev = i > 0 ? series.ao[i - 1] : null
  const rsi = series.rsi[i]
  return {
    ut: series.utBuy[i] && !series.utSell[i],
    supertrend: series.stDir[i] === 1,
    sma: sma != null && close > sma && series.smaRising[i],
    ao: ao != null && aoPrev != null && ao > 0 && ao > aoPrev,
    rsi: rsi != null && rsi > 52 && rsi < 70,
  }
}

export function fallFlagsAt(series: EnrichedSeries, i: number): ConfirmationFlags {
  const close = series.candles[i].close
  const sma = series.sma20[i]
  const ao = series.ao[i]
  const aoPrev = i > 0 ? series.ao[i - 1] : null
  const rsi = series.rsi[i]
  return {
    ut: series.utSell[i] && !series.utBuy[i],
    supertrend: series.stDir[i] === -1,
    sma: sma != null && close < sma && series.smaFalling[i],
    ao: ao != null && aoPrev != null && ao < 0 && ao < aoPrev,
    rsi: rsi != null && rsi < 48 && rsi > 30,
  }
}

export function countPassed(flags: ConfirmationFlags): number {
  return CONDITION_KEYS.filter((k) => flags[k]).length
}

/** True when every key in `required` is true; omitted keys are ignored. */
export function maskPasses(
  flags: ConfirmationFlags,
  required: (keyof ConfirmationFlags)[],
): boolean {
  return required.every((k) => flags[k])
}

export type DirectionSide = 'RISE' | 'FALL'

/**
 * Pick stronger side when both qualify (≥ minPassed of 5).
 * Mirrors useMarketAndSignals side selection for B0/B1.
 */
export function pickSideByScore(
  risePassed: number,
  fallPassed: number,
  minPassed: number,
): DirectionSide | null {
  if (risePassed < minPassed && fallPassed < minPassed) return null
  if (risePassed > fallPassed && risePassed >= minPassed) return 'RISE'
  if (fallPassed > risePassed && fallPassed >= minPassed) return 'FALL'
  if (risePassed >= minPassed && risePassed >= fallPassed) return 'RISE'
  if (fallPassed >= minPassed) return 'FALL'
  return null
}
