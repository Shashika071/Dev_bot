import type { StrategyId } from './strategies/types'

/** @deprecated Old B1/C5/C1 set removed — discovery now runs all R1–R8. */
export const FOCUSED_CONFIRMATION_IDS: StrategyId[] = []

export const FOCUSED_CONFIRMATION_LABELS: Record<string, string> = {}

export function isFocusedConfirmation(_ids: StrategyId[] | undefined | null): boolean {
  return false
}
