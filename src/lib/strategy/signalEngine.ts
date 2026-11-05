/**
 * Shared indicator enrichment for live C5 and research masks.
 * Main live qualification lives in c5Strategy.ts (AO informational only).
 */
import type { Candle, TrendDir } from '../types'
import { computeAO } from '../indicators/ao'
import { computeRsi } from '../indicators/rsi'
import { computeSma20 } from '../indicators/sma'
import { computeSupertrend } from '../indicators/supertrend'
import { computeUtBot } from '../indicators/utBot'

export const WARMUP_BARS = 60

export interface EnrichedSeries {
  candles: Candle[]
  utTrail: (number | null)[]
  utBuy: boolean[]
  utSell: boolean[]
  stLine: (number | null)[]
  stDir: TrendDir[]
  sma20: (number | null)[]
  smaRising: boolean[]
  smaFalling: boolean[]
  ao: (number | null)[]
  rsi: (number | null)[]
}

export function enrich(candles: Candle[]): EnrichedSeries {
  const ut = computeUtBot(candles, 2, 10)
  const st = computeSupertrend(candles, 7, 2)
  const sma = computeSma20(candles, 20)
  const ao = computeAO(candles, 5, 34)
  const rsi = computeRsi(candles, 14)
  return {
    candles,
    utTrail: ut.trail,
    utBuy: ut.buy,
    utSell: ut.sell,
    stLine: st.line,
    stDir: st.dir,
    sma20: sma.sma,
    smaRising: sma.rising,
    smaFalling: sma.falling,
    ao,
    rsi,
  }
}

/** @deprecated Use evaluateC5Index — main strategy is C5. */
export { evaluateC5Index as evaluateIndex, evaluateC5All as evaluateAll, evaluateC5LatestClosed as evaluateLatestClosed } from './c5Strategy'
