/**
 * Research signal strategies (paper-only).
 * Core entries: UT Bot Alerts (key2/ATR10) or Supertrend flip (ATR7×2).
 * Optional filters: EMA50 trend, candle wick rejection.
 */
import { emaFalling, emaRising } from '../../indicators/ema'
import type { StrategyContext, StrategyHit, ResearchSide } from './types'

/** Fresh UT Bot buy/sell on this completed bar. */
export function utSignalSide(ctx: StrategyContext): ResearchSide | null {
  const i = ctx.index
  const buy = ctx.series.utBuy[i]
  const sell = ctx.series.utSell[i]
  if (buy && !sell) return 'RISE'
  if (sell && !buy) return 'FALL'
  return null
}

/**
 * Supertrend generates an entry when direction flips
 * (current ATR7 × mult 2 settings — same as live chart).
 */
export function supertrendFlipSide(ctx: StrategyContext): ResearchSide | null {
  const i = ctx.index
  if (i < 1) return null
  const prev = ctx.series.stDir[i - 1]
  const cur = ctx.series.stDir[i]
  if (prev === -1 && cur === 1) return 'RISE'
  if (prev === 1 && cur === -1) return 'FALL'
  return null
}

/** EMA50 filter: close on the right side of a rising/falling EMA50. */
export function ema50Confirms(ctx: StrategyContext, side: ResearchSide): boolean {
  const i = ctx.index
  const e50 = ctx.extras.ema50[i]
  if (e50 == null) return false
  const close = ctx.candles[i]!.close
  if (side === 'RISE') return close > e50 && emaRising(ctx.extras.ema50, i)
  return close < e50 && emaFalling(ctx.extras.ema50, i)
}

/**
 * Candle wick rejection / pin bar on the signal candle.
 * RISE: long lower wick (≥2× body), small upper wick, close in upper third.
 * FALL: mirror.
 */
export function wickSignalSide(ctx: StrategyContext): ResearchSide | null {
  const c = ctx.candles[ctx.index]!
  const body = Math.abs(c.close - c.open)
  const full = c.high - c.low
  if (!(body > 0) || !(full > 0)) return null
  const lowerWick = Math.min(c.open, c.close) - c.low
  const upperWick = c.high - Math.max(c.open, c.close)

  if (
    lowerWick >= 2 * body
    && upperWick <= body
    && c.close >= c.low + (2 / 3) * full
  ) {
    return 'RISE'
  }
  if (
    upperWick >= 2 * body
    && lowerWick <= body
    && c.close <= c.high - (2 / 3) * full
  ) {
    return 'FALL'
  }
  return null
}

function hit(
  side: ResearchSide,
  reason: string,
  metrics: Record<string, number | boolean | string | null>,
): StrategyHit {
  return { side, reason, metrics }
}

function baseMetrics(ctx: StrategyContext) {
  const i = ctx.index
  return {
    close: ctx.candles[i]!.close,
    utBuy: ctx.series.utBuy[i],
    utSell: ctx.series.utSell[i],
    stDir: ctx.series.stDir[i],
    stDirPrev: i > 0 ? ctx.series.stDir[i - 1] : null,
    ema50: ctx.extras.ema50[i] ?? null,
  }
}

/** R1 — Supertrend flip alone (entry on generated ST signal). */
export function evalR1(ctx: StrategyContext): StrategyHit | null {
  const side = supertrendFlipSide(ctx)
  if (!side) return null
  return hit(side, 'R1 Supertrend flip signal', baseMetrics(ctx))
}

/** R2 — UT Bot Alerts alone. */
export function evalR2(ctx: StrategyContext): StrategyHit | null {
  const side = utSignalSide(ctx)
  if (!side) return null
  return hit(side, 'R2 UT signal solo', baseMetrics(ctx))
}

/** R3 — UT + EMA50. */
export function evalR3(ctx: StrategyContext): StrategyHit | null {
  const side = utSignalSide(ctx)
  if (!side || !ema50Confirms(ctx, side)) return null
  return hit(side, 'R3 UT + EMA50', baseMetrics(ctx))
}

/** R4 — Supertrend flip + EMA50. */
export function evalR4(ctx: StrategyContext): StrategyHit | null {
  const side = supertrendFlipSide(ctx)
  if (!side || !ema50Confirms(ctx, side)) return null
  return hit(side, 'R4 Supertrend flip + EMA50', baseMetrics(ctx))
}

/** R5 — Supertrend flip + candle wick. */
export function evalR5(ctx: StrategyContext): StrategyHit | null {
  const st = supertrendFlipSide(ctx)
  const wick = wickSignalSide(ctx)
  if (!st || !wick || st !== wick) return null
  return hit(st, 'R5 Supertrend flip + candle wick', baseMetrics(ctx))
}

/** R6 — UT + candle wick. */
export function evalR6(ctx: StrategyContext): StrategyHit | null {
  const ut = utSignalSide(ctx)
  const wick = wickSignalSide(ctx)
  if (!ut || !wick || ut !== wick) return null
  return hit(ut, 'R6 UT + candle wick', baseMetrics(ctx))
}

/** R7 — Supertrend flip + candle wick + EMA50. */
export function evalR7(ctx: StrategyContext): StrategyHit | null {
  const st = supertrendFlipSide(ctx)
  const wick = wickSignalSide(ctx)
  if (!st || !wick || st !== wick) return null
  if (!ema50Confirms(ctx, st)) return null
  return hit(st, 'R7 Supertrend flip + wick + EMA50', baseMetrics(ctx))
}

/** R8 — UT + candle wick + EMA50. */
export function evalR8(ctx: StrategyContext): StrategyHit | null {
  const ut = utSignalSide(ctx)
  const wick = wickSignalSide(ctx)
  if (!ut || !wick || ut !== wick) return null
  if (!ema50Confirms(ctx, ut)) return null
  return hit(ut, 'R8 UT + wick + EMA50', baseMetrics(ctx))
}
