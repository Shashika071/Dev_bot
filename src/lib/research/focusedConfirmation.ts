import type { StrategyId } from './strategies/types'

/** Paper comparison set: baseline / main candidate / AO-required confirmation. */
export const FOCUSED_CONFIRMATION_IDS: StrategyId[] = ['B1', 'C5', 'C1']

export const FOCUSED_CONFIRMATION_LABELS: Record<'B1' | 'C5' | 'C1', string> = {
  B1: 'Baseline',
  C5: 'Main candidate — AO optional',
  C1: 'Full confirmation — AO required',
}

export function isFocusedConfirmation(ids: StrategyId[] | undefined | null): boolean {
  if (!ids || ids.length !== FOCUSED_CONFIRMATION_IDS.length) return false
  const set = new Set(ids)
  return FOCUSED_CONFIRMATION_IDS.every((id) => set.has(id))
}
