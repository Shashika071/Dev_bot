import { evalMask, MASKS } from './maskStrategies'
import { evalT1 } from './statStrategies'
import type { StrategyContext, StrategyHit, ResearchSide } from './types'

export type C5T1AgreementKind =
  | 'agree'
  | 'oppose'
  | 'only_c5'
  | 'only_t1'
  | 'neither'

export interface C5T1AgreementSnapshot {
  kind: C5T1AgreementKind
  c5Side: ResearchSide | null
  t1Side: ResearchSide | null
  c5: StrategyHit | null
  t1: StrategyHit | null
}

/**
 * Raw C5/T1 condition eligibility on the same candle — ignores parent cooldowns
 * and observation history. Used by H1 and pre-cooldown diagnostics.
 */
export function classifyC5T1Agreement(ctx: StrategyContext): C5T1AgreementSnapshot {
  const c5 = evalMask(ctx, MASKS.C5!, 'C5 main candidate AO optional')
  const t1 = evalT1(ctx)
  const c5Side = c5?.side ?? null
  const t1Side = t1?.side ?? null
  if (c5Side && t1Side && c5Side === t1Side) {
    return { kind: 'agree', c5Side, t1Side, c5, t1 }
  }
  if (c5Side && t1Side && c5Side !== t1Side) {
    return { kind: 'oppose', c5Side, t1Side, c5, t1 }
  }
  if (c5Side && !t1Side) return { kind: 'only_c5', c5Side, t1Side: null, c5, t1 }
  if (!c5Side && t1Side) return { kind: 'only_t1', c5Side: null, t1Side, c5, t1 }
  return { kind: 'neither', c5Side: null, t1Side: null, c5, t1 }
}

export function snapshotMetricsFromAgreement(snap: C5T1AgreementSnapshot) {
  const z = snap.t1?.metrics?.z
  return {
    ...(snap.c5?.metrics ?? {}),
    ...(snap.t1?.metrics ?? {}),
    t1z: typeof z === 'number' ? z : (typeof snap.t1?.metrics?.z === 'number' ? snap.t1.metrics.z : null),
    z: typeof z === 'number' ? z : null,
    c5Side: snap.c5Side,
    t1Side: snap.t1Side,
    agreement: snap.kind === 'agree',
    agreementKind: snap.kind,
  }
}

export function h1SkipReason(snap: C5T1AgreementSnapshot): string {
  if (snap.kind === 'oppose') {
    return `H1 skip: C5 ${snap.c5Side} vs T1 ${snap.t1Side}`
  }
  if (snap.kind === 'only_c5') return `H1 skip: only C5 ${snap.c5Side}`
  if (snap.kind === 'only_t1') return `H1 skip: only T1 ${snap.t1Side}`
  if (snap.kind === 'neither') return 'H1 skip: neither C5 nor T1'
  return 'H1 skip'
}

/** H1 — C5 + T1 same-direction agreement on the same completed M1 candle. */
export function evalH1(ctx: StrategyContext): StrategyHit | null {
  const snap = classifyC5T1Agreement(ctx)
  if (snap.kind !== 'agree' || !snap.c5 || !snap.t1 || !snap.c5Side) return null
  return {
    side: snap.c5Side,
    reason: `H1 C5+T1 agreement (${snap.c5Side})`,
    flags: snap.c5.flags,
    passed: snap.c5.passed,
    total: snap.c5.total,
    metrics: snapshotMetricsFromAgreement(snap),
  }
}
