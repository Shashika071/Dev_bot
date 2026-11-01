import { emaFalling, emaRising } from '../../indicators/ema'
import type { StrategyContext, StrategyHit } from './types'

function priorRange(ctx: StrategyContext, lookback: number): { hi: number; lo: number } | null {
  const end = ctx.index // signal candle index — exclude from range
  const start = end - lookback
  if (start < 0) return null
  let hi = -Infinity
  let lo = Infinity
  for (let j = start; j < end; j++) {
    hi = Math.max(hi, ctx.candles[j]!.high)
    lo = Math.min(lo, ctx.candles[j]!.low)
  }
  if (!Number.isFinite(hi) || !Number.isFinite(lo)) return null
  return { hi, lo }
}

export function evalS1(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const { ema20, ema50 } = ctx.extras
  const e20 = ema20[i]
  const e50 = ema50[i]
  const e50p = ema50[i - 1]
  const rsi = ctx.series.rsi[i]
  const rsiP = ctx.series.rsi[i - 1]
  const close = ctx.candles[i]!.close
  if (e20 == null || e50 == null || e50p == null || rsi == null || rsiP == null) return null

  if (e20 > e50 && e50 > e50p && close > e20 && rsiP <= 50 && rsi > 50) {
    return { side: 'RISE', reason: 'EMA/RSI trend resumption up', metrics: { ema20: e20, ema50: e50, rsi } }
  }
  if (e20 < e50 && e50 < e50p && close < e20 && rsiP >= 50 && rsi < 50) {
    return { side: 'FALL', reason: 'EMA/RSI trend resumption down', metrics: { ema20: e20, ema50: e50, rsi } }
  }
  return null
}

export function evalS2(ctx: StrategyContext): StrategyHit | null {
  const range = priorRange(ctx, 20)
  if (!range) return null
  const close = ctx.candles[ctx.index]!.close
  if (close > range.hi) {
    return { side: 'RISE', reason: 'Break above prior 20-candle high', metrics: { priorHigh: range.hi, close } }
  }
  if (close < range.lo) {
    return { side: 'FALL', reason: 'Break below prior 20-candle low', metrics: { priorLow: range.lo, close } }
  }
  return null
}

export function evalS3(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const { bbUpper, bbLower } = ctx.extras
  const close = ctx.candles[i]!.close
  const closeP = ctx.candles[i - 1]!.close
  const rsiP = ctx.series.rsi[i - 1]
  const loP = bbLower[i - 1]
  const upP = bbUpper[i - 1]
  const lo = bbLower[i]
  const up = bbUpper[i]
  if (rsiP == null || loP == null || upP == null || lo == null || up == null) return null

  if (closeP < loP && rsiP < 30 && close >= lo && close <= up) {
    return { side: 'RISE', reason: 'Bollinger/RSI reversal up', metrics: { prevClose: closeP, prevRsi: rsiP } }
  }
  if (closeP > upP && rsiP > 70 && close >= lo && close <= up) {
    return { side: 'FALL', reason: 'Bollinger/RSI reversal down', metrics: { prevClose: closeP, prevRsi: rsiP } }
  }
  return null
}

export function evalS4(ctx: StrategyContext): StrategyHit | null {
  const i = ctx.index
  if (i < 1) return null
  const prev = ctx.candles[i - 1]!
  const cur = ctx.candles[i]!
  const e50 = ctx.extras.ema50[i]
  if (e50 == null) return null
  const prevBodyLo = Math.min(prev.open, prev.close)
  const prevBodyHi = Math.max(prev.open, prev.close)
  const curBodyLo = Math.min(cur.open, cur.close)
  const curBodyHi = Math.max(cur.open, cur.close)
  const prevBear = prev.close < prev.open
  const prevBull = prev.close > prev.open
  const curBull = cur.close > cur.open
  const curBear = cur.close < cur.open

  // Current body contains previous body with at least one boundary strictly beyond
  const contains =
    curBodyLo <= prevBodyLo
    && curBodyHi >= prevBodyHi
    && (curBodyLo < prevBodyLo || curBodyHi > prevBodyHi)

  if (prevBear && curBull && contains && cur.close > e50 && emaRising(ctx.extras.ema50, i)) {
    return { side: 'RISE', reason: 'Bullish engulfing + rising EMA50' }
  }
  if (prevBull && curBear && contains && cur.close < e50 && emaFalling(ctx.extras.ema50, i)) {
    return { side: 'FALL', reason: 'Bearish engulfing + falling EMA50' }
  }
  return null
}

export function evalS5(ctx: StrategyContext): StrategyHit | null {
  const range = priorRange(ctx, 20)
  if (!range) return null
  const c = ctx.candles[ctx.index]!
  const body = Math.abs(c.close - c.open)
  const full = c.high - c.low
  if (!(body > 0) || !(full > 0)) return null
  const lowerWick = Math.min(c.open, c.close) - c.low
  const upperWick = c.high - Math.max(c.open, c.close)

  if (
    c.low < range.lo
    && c.close > range.lo
    && lowerWick >= 2 * body
    && upperWick <= body
    && c.close >= c.low + (2 / 3) * full
  ) {
    return { side: 'RISE', reason: 'Wick rejection at prior range low', metrics: { priorLow: range.lo } }
  }
  if (
    c.high > range.hi
    && c.close < range.hi
    && upperWick >= 2 * body
    && lowerWick <= body
    && c.close <= c.high - (2 / 3) * full
  ) {
    return { side: 'FALL', reason: 'Wick rejection at prior range high', metrics: { priorHigh: range.hi } }
  }
  return null
}

/** Three strict same-sign close-to-close returns → direction. */
export function threeReturnSide(ctx: StrategyContext): 'RISE' | 'FALL' | null {
  const i = ctx.index
  if (i < 3) return null
  const r1 = ctx.candles[i]!.close - ctx.candles[i - 1]!.close
  const r2 = ctx.candles[i - 1]!.close - ctx.candles[i - 2]!.close
  const r3 = ctx.candles[i - 2]!.close - ctx.candles[i - 3]!.close
  if (r1 === 0 || r2 === 0 || r3 === 0) return null
  if (r1 > 0 && r2 > 0 && r3 > 0) return 'RISE'
  if (r1 < 0 && r2 < 0 && r3 < 0) return 'FALL'
  return null
}

export function evalS6(ctx: StrategyContext): StrategyHit | null {
  const side = threeReturnSide(ctx)
  if (!side) return null
  return { side, reason: 'Three-return continuation' }
}

export function evalS7(ctx: StrategyContext): StrategyHit | null {
  const side = threeReturnSide(ctx)
  if (!side) return null
  return {
    side: side === 'RISE' ? 'FALL' : 'RISE',
    reason: 'Three-return reversal (opposite of S6)',
  }
}
