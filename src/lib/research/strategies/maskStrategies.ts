import type { ConfirmationFlags } from '../../types'
import {
  countPassed,
  fallFlagsAt,
  maskPasses,
  pickSideByScore,
  riseFlagsAt,
} from '../../strategy/conditionFlags'
import { C5_REQUIRED } from '../../strategy/c5Strategy'
import type { StrategyContext, StrategyHit } from './types'

const ALL: (keyof ConfirmationFlags)[] = ['ut', 'supertrend', 'sma', 'ao', 'rsi']

function snapshotMetrics(ctx: StrategyContext, rise: ConfirmationFlags, fall: ConfirmationFlags) {
  const i = ctx.index
  const s = ctx.series
  const aoPrev = i > 0 ? s.ao[i - 1] : null
  return {
    close: s.candles[i]!.close,
    sma20: s.sma20[i] ?? null,
    smaRising: s.smaRising[i],
    smaFalling: s.smaFalling[i],
    ao: s.ao[i] ?? null,
    aoPrev: aoPrev ?? null,
    rsi: s.rsi[i] ?? null,
    utBuy: s.utBuy[i],
    utSell: s.utSell[i],
    stDir: s.stDir[i],
    flagUtRise: rise.ut,
    flagUtFall: fall.ut,
    flagStRise: rise.supertrend,
    flagStFall: fall.supertrend,
    flagSmaRise: rise.sma,
    flagSmaFall: fall.sma,
    flagAoRise: rise.ao,
    flagAoFall: fall.ao,
    flagRsiRise: rise.rsi,
    flagRsiFall: fall.rsi,
  }
}

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
    metrics: snapshotMetrics(ctx, rise, fall),
  }
}

export function evalMask(
  ctx: StrategyContext,
  required: (keyof ConfirmationFlags)[],
  label: string,
): StrategyHit | null {
  const rise = riseFlagsAt(ctx.series, ctx.index)
  const fall = fallFlagsAt(ctx.series, ctx.index)
  const metrics = snapshotMetrics(ctx, rise, fall)
  const riseOk = maskPasses(rise, required)
  const fallOk = maskPasses(fall, required)
  if (riseOk && !fallOk) {
    return { side: 'RISE', reason: label, flags: rise, passed: required.length, total: required.length, metrics }
  }
  if (fallOk && !riseOk) {
    return { side: 'FALL', reason: label, flags: fall, passed: required.length, total: required.length, metrics }
  }
  if (riseOk && fallOk) {
    // Prefer side with more total flags passed among all five
    const rp = countPassed(rise)
    const fp = countPassed(fall)
    if (rp >= fp) {
      return { side: 'RISE', reason: label, flags: rise, passed: required.length, total: required.length, metrics }
    }
    return { side: 'FALL', reason: label, flags: fall, passed: required.length, total: required.length, metrics }
  }
  return null
}

export const MASKS: Record<string, (keyof ConfirmationFlags)[]> = {
  C1: ALL,
  C2: ['supertrend', 'sma', 'ao', 'rsi'],
  C3: ['ut', 'sma', 'ao', 'rsi'],
  C4: ['ut', 'supertrend', 'ao', 'rsi'],
  /** Shared with live main strategy — AO not required. */
  C5: [...C5_REQUIRED],
  C6: ['ut', 'supertrend', 'sma', 'ao'],
}
