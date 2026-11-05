import { describe, expect, it } from 'vitest'
import {
  authorizeC5AutoTrade,
  c5DedupeKey,
  isC5EntryExpired,
  migrateLegacyAutoPrefs,
  type C5AutoAuthInput,
} from './c5AutoAuth'

function okSig(over: Partial<C5AutoAuthInput> = {}): C5AutoAuthInput {
  return {
    strategyId: 'C5',
    side: 'RISE',
    isQualified: true,
    requiredPassed: 4,
    requiredTotal: 4,
    flags: { ut: true, supertrend: true, sma: true, ao: false, rsi: true },
    candleTime: 1_700_000_000,
    expiresAt: 1_700_000_065,
    confidence: 1,
    ...over,
  }
}

describe('authorizeC5AutoTrade', () => {
  it('authorizes only when Auto ON and all four required flags pass', () => {
    const auth = authorizeC5AutoTrade(okSig(), {
      autoEnabled: true,
      nowSec: 1_700_000_061,
      alreadyTraded: false,
      minConfidencePct: 0,
    })
    expect(auth).toEqual({ ok: true, dedupeKey: c5DedupeKey(1_700_000_000, 'RISE') })
  })

  it('rejects when Auto is OFF even if 4/4 and confidence is 100%', () => {
    expect(authorizeC5AutoTrade(okSig({ confidence: 1 }), {
      autoEnabled: false,
      nowSec: 1_700_000_061,
      alreadyTraded: false,
      minConfidencePct: 0,
    }).ok).toBe(false)
  })

  it('old saved minConfidence cannot bypass missing UT', () => {
    const auth = authorizeC5AutoTrade(
      okSig({
        flags: { ut: false, supertrend: true, sma: true, ao: true, rsi: true },
        confidence: 0.8,
        requiredPassed: 3,
      }),
      { autoEnabled: true, nowSec: 1_700_000_061, alreadyTraded: false, minConfidencePct: 50 },
    )
    expect(auth.ok).toBe(false)
    if (!auth.ok) expect(auth.reason).toBe('required_count')
  })

  it('AO false does not block when required flags pass', () => {
    const auth = authorizeC5AutoTrade(okSig({
      flags: { ut: true, supertrend: true, sma: true, ao: false, rsi: true },
    }), {
      autoEnabled: true,
      nowSec: 1_700_000_061,
      alreadyTraded: false,
    })
    expect(auth.ok).toBe(true)
  })

  it('rejects expired signals and does not queue them', () => {
    expect(isC5EntryExpired(100, 101)).toBe(true)
    const auth = authorizeC5AutoTrade(okSig({ expiresAt: 100 }), {
      autoEnabled: true,
      nowSec: 101,
      alreadyTraded: false,
    })
    expect(auth).toEqual({ ok: false, reason: 'expired' })
  })

  it('rejects duplicates for the same candle/side', () => {
    const auth = authorizeC5AutoTrade(okSig(), {
      autoEnabled: true,
      nowSec: 1_700_000_061,
      alreadyTraded: true,
    })
    expect(auth).toEqual({ ok: false, reason: 'duplicate' })
  })

  it('rejects wrong strategy id or unqualified signal', () => {
    expect(authorizeC5AutoTrade(okSig({ strategyId: 'B1' }), {
      autoEnabled: true, nowSec: 1_700_000_061, alreadyTraded: false,
    }).ok).toBe(false)
    expect(authorizeC5AutoTrade(okSig({ isQualified: false }), {
      autoEnabled: true, nowSec: 1_700_000_061, alreadyTraded: false,
    }).ok).toBe(false)
  })

  it('leaves Auto OFF after legacy migration even if legacy Auto was ON', () => {
    const migrated = migrateLegacyAutoPrefs(
      { autoEnabled: true, wantConnected: true, appId: '123' },
      { autoEnabled: false, wantConnected: false, appId: '', token: '' },
    )
    expect(migrated.autoEnabled).toBe(false)
    expect(migrated.wantConnected).toBe(false)
    expect(migrated.appId).toBe('123')
  })

  it('expiry during async prep is detected by recheck helper', () => {
    const expiresAt = 1_000
    const afterBalance = 1_001
    expect(isC5EntryExpired(expiresAt, afterBalance)).toBe(true)
  })
})
