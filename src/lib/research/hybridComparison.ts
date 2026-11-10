import type { StrategyId } from './strategies/types'

/** @deprecated Old C5/T1/H1 hybrid removed — discovery now runs all R1–R8. */
export const HYBRID_COMPARISON_IDS: StrategyId[] = []

export const HYBRID_COMPARISON_LABELS: Record<string, string> = {}

export function isHybridComparison(_ids: StrategyId[] | undefined | null): boolean {
  return false
}

export interface H1Diagnostics {
  agree: number
  oppose: number
  onlyC5: number
  onlyT1: number
  neither: number
  h1CooldownBlocked: number
}

export interface H1SkipRecord {
  id: string
  candleTime: number
  signalEpoch: number
  kind: string
  reason: string
  c5Side?: string
  t1Side?: string
  flags?: unknown
  passed?: number
  total?: number
  metrics?: Record<string, number | boolean | string | null>
}

export function emptyH1Diagnostics(): H1Diagnostics {
  return { agree: 0, oppose: 0, onlyC5: 0, onlyT1: 0, neither: 0, h1CooldownBlocked: 0 }
}
