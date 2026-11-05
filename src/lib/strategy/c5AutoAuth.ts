/**
 * Pure C5 automatic-entry authorization — never uses confidence % alone.
 */
import type { ConfirmationFlags } from '../types'
import { C5_REQUIRED, C5_STRATEGY_ID } from './c5Strategy'

export type C5AutoAuthInput = {
  strategyId: string
  side: string
  isQualified: boolean
  requiredPassed: number
  requiredTotal: number
  flags: ConfirmationFlags
  candleTime: number
  expiresAt: number
  /** Tick time also bounds entry when the device clock is slightly behind. */
  receivedTickEpoch?: number
  /** Display-only; ignored for authorization */
  confidence?: number
}

export type C5AutoAuthResult =
  | { ok: true; dedupeKey: string }
  | { ok: false; reason: string }

export function c5DedupeKey(candleTime: number, side: 'RISE' | 'FALL'): string {
  return `${C5_STRATEGY_ID}:${candleTime}:${side}`
}

export function c5RequiredFlagsOk(flags: ConfirmationFlags): boolean {
  return C5_REQUIRED.every((k) => flags[k])
}

export function isC5EntryExpired(expiresAt: number, nowSec: number): boolean {
  return nowSec > expiresAt
}

/**
 * Gate for automatic C5 entries. A high confidence / minConfidencePct alone never authorizes.
 */
export function authorizeC5AutoTrade(
  sig: C5AutoAuthInput,
  opts: {
    autoEnabled: boolean
    nowSec: number
    alreadyTraded: boolean
    /** Intentionally unused — must not weaken 4/4 requirements */
    minConfidencePct?: number
  },
): C5AutoAuthResult {
  if (!opts.autoEnabled) return { ok: false, reason: 'auto_disabled' }
  if (sig.strategyId !== C5_STRATEGY_ID) return { ok: false, reason: 'wrong_strategy' }
  if (sig.side !== 'RISE' && sig.side !== 'FALL') return { ok: false, reason: 'invalid_side' }
  if (!sig.isQualified) return { ok: false, reason: 'not_qualified' }
  if (sig.requiredPassed < 4 || sig.requiredTotal !== 4) {
    return { ok: false, reason: 'required_count' }
  }
  if (!c5RequiredFlagsOk(sig.flags)) return { ok: false, reason: 'required_flags' }
  if (isC5EntryExpired(sig.expiresAt, Math.max(opts.nowSec, sig.receivedTickEpoch ?? opts.nowSec))) {
    return { ok: false, reason: 'expired' }
  }
  if (opts.alreadyTraded) return { ok: false, reason: 'duplicate' }
  return {
    ok: true,
    dedupeKey: c5DedupeKey(sig.candleTime, sig.side),
  }
}

/** Legacy Auto ON must not carry into C5 storage. */
export function migrateLegacyAutoPrefs<T extends { autoEnabled?: boolean; wantConnected?: boolean }>(
  legacy: Partial<T>,
  defaults: T & { autoEnabled: boolean; wantConnected: boolean },
): T & { autoEnabled: false; wantConnected: false } {
  return {
    ...defaults,
    ...legacy,
    autoEnabled: false,
    wantConnected: false,
  }
}
