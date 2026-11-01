import type { ConfirmationFlags } from '../../types'
import {
  countPassed,
  fallFlagsAt,
  maskPasses,
  pickSideByScore,
  riseFlagsAt,
} from '../../strategy/conditionFlags'
import type { StrategyContext, StrategyHit } from './types'

const ALL: (keyof ConfirmationFlags)[] = ['ut', 'supertrend', 'sma', 'ao', 'rsi']

export function evalMinPassed(ctx: StrategyContext, minPassed: number): StrategyHit | null {
  const rise = riseFlagsAt(ctx.series, ctx.index)
  const fall = fallFlagsAt(ctx.series, ctx.index)
  const rp = countPassed(rise)
  const fp = countPassed(fall)
  const side = pickSideByScore(rp, fp, minPassed)
  if (!side) return null
  const flags = side === 'RISE' ? rise : fall
  const passed = side === 'RISE' ? rp : fp
  return {
    side,
    reason: `${passed}/5 conditions (≥${minPassed} required)`,
    flags,
    passed,
    total: 5,
  }
}

export function evalMask(
  ctx: StrategyContext,
  required: (keyof ConfirmationFlags)[],
  label: string,
): StrategyHit | null {
  const rise = riseFlagsAt(ctx.series, ctx.index)
  const fall = fallFlagsAt(ctx.series, ctx.index)
  const riseOk = maskPasses(rise, required)
  const fallOk = maskPasses(fall, required)
  if (riseOk && !fallOk) {
    return { side: 'RISE', reason: label, flags: rise, passed: required.length, total: required.length }
  }
  if (fallOk && !riseOk) {
    return { side: 'FALL', reason: label, flags: fall, passed: required.length, total: required.length }
  }
  if (riseOk && fallOk) {
    // Prefer side with more total flags passed among all five
    const rp = countPassed(rise)
    const fp = countPassed(fall)
    if (rp >= fp) {
      return { side: 'RISE', reason: label, flags: rise, passed: required.length, total: required.length }
    }
    return { side: 'FALL', reason: label, flags: fall, passed: required.length, total: required.length }
  }
  return null
}

export const MASKS: Record<string, (keyof ConfirmationFlags)[]> = {
  C1: ALL,
  C2: ['supertrend', 'sma', 'ao', 'rsi'],
  C3: ['ut', 'sma', 'ao', 'rsi'],
  C4: ['ut', 'supertrend', 'ao', 'rsi'],
  C5: ['ut', 'supertrend', 'sma', 'rsi'],
  C6: ['ut', 'supertrend', 'sma', 'ao'],
}
