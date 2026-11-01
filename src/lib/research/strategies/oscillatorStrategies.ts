import { emaFalling, emaRising } from '../../indicators/ema'
import type { StrategyContext, StrategyHit } from './types'

export function evalO1(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const { stochK: k, stochD: d } = ctx.extras
  const k0 = k[i]
  const d0 = d[i]
  const k1 = k[i - 1]
  const d1 = d[i - 1]
  if (k0 == null || d0 == null || k1 == null || d1 == null) return null

  if (k1 <= d1 && k0 > d0 && k1 <= 20 && d1 <= 20) {
    return { side: 'RISE', reason: 'Stochastic bullish cross from oversold', metrics: { k: k0, d: d0 } }
  }
  if (k1 >= d1 && k0 < d0 && k1 >= 80 && d1 >= 80) {
    return { side: 'FALL', reason: 'Stochastic bearish cross from overbought', metrics: { k: k0, d: d0 } }
  }
  return null
}

export function evalO2(ctx: StrategyContext): StrategyHit | null {
  const base = evalO1(ctx)
  if (!base) return null
  const i = ctx.index
  const e50 = ctx.extras.ema50[i]
  const close = ctx.candles[i]!.close
  if (e50 == null) return null
  if (base.side === 'RISE') {
    if (!(close > e50 && emaRising(ctx.extras.ema50, i))) return null
    return { ...base, reason: 'Stochastic reversal + rising EMA50 filter' }
  }
  if (!(close < e50 && emaFalling(ctx.extras.ema50, i))) return null
  return { ...base, reason: 'Stochastic reversal + falling EMA50 filter' }
}

export function evalN1(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const cci = ctx.extras.cci20
  const c0 = cci[i]
  const c1 = cci[i - 1]
  const e50 = ctx.extras.ema50[i]
  const e50p = ctx.extras.ema50[i - 1]
  const close = ctx.candles[i]!.close
  if (c0 == null || c1 == null || e50 == null || e50p == null) return null

  if (c1 <= -100 && c0 > -100 && close > e50 && e50 > e50p) {
    return { side: 'RISE', reason: 'CCI pullback up through -100 + rising EMA50', metrics: { cci: c0 } }
  }
  if (c1 >= 100 && c0 < 100 && close < e50 && e50 < e50p) {
    return { side: 'FALL', reason: 'CCI pullback down through +100 + falling EMA50', metrics: { cci: c0 } }
  }
  return null
}

export function evalN2(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const { plusDi, minusDi, adx } = ctx.extras
  const p0 = plusDi[i]
  const m0 = minusDi[i]
  const p1 = plusDi[i - 1]
  const m1 = minusDi[i - 1]
  const a0 = adx[i]
  if (p0 == null || m0 == null || p1 == null || m1 == null || a0 == null) return null
  if (!(a0 >= 25)) return null

  if (p1 <= m1 && p0 > m0) {
    return { side: 'RISE', reason: '+DI cross above -DI with ADX≥25', metrics: { adx: a0, plusDi: p0, minusDi: m0 } }
  }
  if (m1 <= p1 && m0 > p0) {
    return { side: 'FALL', reason: '-DI cross above +DI with ADX≥25', metrics: { adx: a0, plusDi: p0, minusDi: m0 } }
  }
  return null
}
