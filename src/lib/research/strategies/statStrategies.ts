import type { StrategyContext, StrategyHit } from './types'

function zScoreAt(ctx: StrategyContext): { z: number; r: number } | null {
  const i = ctx.index
  const rets = ctx.extras.logReturns
  const r = rets[i]
  if (r == null || !Number.isFinite(r)) return null
  // mean/std of 120 returns immediately BEFORE current return: indices i-120 .. i-1
  if (i < 120) return null
  let sum = 0
  for (let j = i - 120; j < i; j++) {
    const v = rets[j]
    if (v == null || !Number.isFinite(v)) return null
    sum += v
  }
  const mean = sum / 120
  let ss = 0
  for (let j = i - 120; j < i; j++) {
    const d = rets[j]! - mean
    ss += d * d
  }
  const std = Math.sqrt(ss / 120) // population
  if (!(std > 0)) return null
  return { z: (r - mean) / std, r }
}

export function evalT1(ctx: StrategyContext): StrategyHit | null {
  const z = zScoreAt(ctx)
  if (!z) return null
  if (z.z <= -2) return { side: 'RISE', reason: 'Return-shock reversal (z≤-2)', metrics: { z: z.z } }
  if (z.z >= 2) return { side: 'FALL', reason: 'Return-shock reversal (z≥+2)', metrics: { z: z.z } }
  return null
}

export function evalT2(ctx: StrategyContext): StrategyHit | null {
  const z = zScoreAt(ctx)
  if (!z) return null
  if (z.z >= 2) return { side: 'RISE', reason: 'Return-shock continuation (z≥+2)', metrics: { z: z.z } }
  if (z.z <= -2) return { side: 'FALL', reason: 'Return-shock continuation (z≤-2)', metrics: { z: z.z } }
  return null
}

export function evalT3(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  const rets = ctx.extras.logReturns
  // latest 240 completed log returns ending at i
  if (i < 239) return null
  const start = i - 239
  const window: number[] = []
  for (let j = start; j <= i; j++) {
    const v = rets[j]
    if (v == null || !Number.isFinite(v)) return null
    window.push(v)
  }
  if (window.length !== 240) return null
  const latest = window[239]!
  if (latest === 0) return null
  const m = window.reduce((a, b) => a + b, 0) / 240
  let num = 0
  let den = 0
  for (let j = 0; j < 240; j++) {
    const d = window[j]! - m
    den += d * d
    if (j >= 1) num += (window[j]! - m) * (window[j - 1]! - m)
  }
  if (!(den > 0)) return null
  const rho = num / den
  if (rho >= 0.15) {
    return {
      side: latest > 0 ? 'RISE' : 'FALL',
      reason: `Autocorr follow (ρ=${rho.toFixed(3)})`,
      metrics: { rho, latest },
    }
  }
  if (rho <= -0.15) {
    return {
      side: latest > 0 ? 'FALL' : 'RISE',
      reason: `Autocorr fade (ρ=${rho.toFixed(3)})`,
      metrics: { rho, latest },
    }
  }
  return null
}
