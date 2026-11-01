import type { Candle } from '../../types'
import { ema } from '../../indicators/ema'
import { bollingerBands } from '../../indicators/bollinger'
import { fullStochastic } from '../../indicators/stochastic'
import { cci } from '../../indicators/cci'
import { computeDmiAdx } from '../../indicators/dmi'
import type { ResearchExtras } from './types'

export function buildResearchExtras(candles: Candle[]): ResearchExtras {
  const closes = candles.map((c) => c.close)
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const bb = bollingerBands(closes, 20, 2)
  const stoch = fullStochastic(highs, lows, closes, 14, 3, 3)
  const dmi = computeDmiAdx(highs, lows, closes, 14)
  const logReturns: (number | null)[] = new Array(candles.length).fill(null)
  for (let i = 1; i < candles.length; i++) {
    const prev = closes[i - 1]!
    const cur = closes[i]!
    if (prev > 0 && cur > 0) logReturns[i] = Math.log(cur / prev)
  }
  return {
    ema20: ema(closes, 20),
    ema50: ema(closes, 50),
    bbUpper: bb.upper,
    bbLower: bb.lower,
    bbMid: bb.mid,
    stochK: stoch.k,
    stochD: stoch.d,
    cci20: cci(highs, lows, closes, 20),
    plusDi: dmi.plusDi,
    minusDi: dmi.minusDi,
    adx: dmi.adx,
    logReturns,
  }
}
