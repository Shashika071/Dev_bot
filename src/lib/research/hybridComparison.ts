import type { StrategyId } from './strategies/types'

/** Paper comparison: C5, T1, and experimental H1 agreement. */
export const HYBRID_COMPARISON_IDS: StrategyId[] = ['C5', 'T1', 'H1']

export const HYBRID_COMPARISON_LABELS: Record<'C5' | 'T1' | 'H1', string> = {
  C5: 'Four required indicators — AO optional',
  T1: 'Return-shock reversal',
  H1: 'Experimental — C5 + T1 agreement',
}

export function isHybridComparison(ids: StrategyId[] | undefined | null): boolean {
  if (!ids || ids.length !== HYBRID_COMPARISON_IDS.length) return false
  const set = new Set(ids)
  return HYBRID_COMPARISON_IDS.every((id) => set.has(id))
}

export interface H1Diagnostics {
  /** C5 and T1 same direction on a closed candle (before cooldowns). */
  agree: number
  /** C5 and T1 opposite directions. */
  oppose: number
  onlyC5: number
  onlyT1: number
  neither: number
  /** H1 would have entered but its own cooldown blocked it. */
  h1CooldownBlocked: number
}

export function emptyH1Diagnostics(): H1Diagnostics {
  return {
    agree: 0,
    oppose: 0,
    onlyC5: 0,
    onlyT1: 0,
    neither: 0,
    h1CooldownBlocked: 0,
  }
}
