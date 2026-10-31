/**
 * Exact 5-indicator strategy — evaluate ONLY on completed candles.
 * UT Bot is the only trigger; all four others must confirm.
 */
import type { Candle, ConfirmationFlags, FinalSignal, SignalEvaluation, TrendDir } from '../types'
import { computeAO } from '../indicators/ao'
import { computeRsi } from '../indicators/rsi'
import { computeSma20 } from '../indicators/sma'
import { computeSupertrend } from '../indicators/supertrend'
import { computeUtBot } from '../indicators/utBot'
import { closedSegments, isValidCandle } from '../deriv/candles'

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

export function evaluateIndex(series: EnrichedSeries, i: number): SignalEvaluation {
  const c = series.candles[i]
  const failures: string[] = []
  const flags: ConfirmationFlags = {
    ut: false,
    supertrend: false,
    sma: false,
    ao: false,
    rsi: false,
  }

  const utBuy = series.utBuy[i]
  const utSell = series.utSell[i]
  const rawUt = utBuy ? 'BUY' : utSell ? 'SELL' : 'NONE'
  const stDir = series.stDir[i]
  const close = c.close
  const sma = series.sma20[i]
  const smaRising = series.smaRising[i]
  const smaFalling = series.smaFalling[i]
  const ao = series.ao[i]
  const aoPrev = i > 0 ? series.ao[i - 1] : null
  const rsi = series.rsi[i]

  let final: FinalSignal = 'NO_TRADE'

  if (!c.isClosed || !isValidCandle(c)) {
    failures.push('Candle is incomplete or invalid')
  } else if (!utBuy && !utSell) {
    failures.push('No new UT Bot BUY/SELL on this completed candle')
  } else if (utBuy && utSell) {
    failures.push('Simultaneous UT BUY and SELL — rejected')
  } else if (utBuy) {
    flags.ut = true
    // Supertrend bullish
    if (stDir === 1) flags.supertrend = true
    else failures.push('Supertrend not bullish')

    // SMA: close > SMA20 and SMA rising
    if (sma != null && close > sma && smaRising) flags.sma = true
    else {
      if (sma == null) failures.push('SMA20 unavailable')
      else if (!(close > sma)) failures.push('Close not above SMA20')
      else if (!smaRising) failures.push('SMA20 not rising')
    }

    // AO > 0 and increasing
    if (ao != null && aoPrev != null && ao > 0 && ao > aoPrev) flags.ao = true
    else {
      if (ao == null || aoPrev == null) failures.push('AO unavailable')
      else if (!(ao > 0)) failures.push('AO not above zero')
      else if (!(ao > aoPrev)) failures.push('AO not increasing')
    }

    // RSI strictly between 52 and 70
    if (rsi != null && rsi > 52 && rsi < 70) flags.rsi = true
    else {
      if (rsi == null) failures.push('RSI unavailable')
      else if (!(rsi > 52)) failures.push('RSI not greater than 52')
      else if (!(rsi < 70)) failures.push('RSI not less than 70')
    }

    if (flags.ut && flags.supertrend && flags.sma && flags.ao && flags.rsi) {
      final = 'RISE'
      failures.length = 0
    }
  } else if (utSell) {
    flags.ut = true
    if (stDir === -1) flags.supertrend = true
    else failures.push('Supertrend not bearish')

    if (sma != null && close < sma && smaFalling) flags.sma = true
    else {
      if (sma == null) failures.push('SMA20 unavailable')
      else if (!(close < sma)) failures.push('Close not below SMA20')
      else if (!smaFalling) failures.push('SMA20 not falling')
    }

    if (ao != null && aoPrev != null && ao < 0 && ao < aoPrev) flags.ao = true
    else {
      if (ao == null || aoPrev == null) failures.push('AO unavailable')
      else if (!(ao < 0)) failures.push('AO not below zero')
      else if (!(ao < aoPrev)) failures.push('AO not decreasing')
    }

    if (rsi != null && rsi < 48 && rsi > 30) flags.rsi = true
    else {
      if (rsi == null) failures.push('RSI unavailable')
      else if (!(rsi < 48)) failures.push('RSI not less than 48')
      else if (!(rsi > 30)) failures.push('RSI not greater than 30')
    }

    if (flags.ut && flags.supertrend && flags.sma && flags.ao && flags.rsi) {
      final = 'FALL'
      failures.length = 0
    }
  }

  return {
    time: c.time,
    final,
    rawUt,
    confirmations: flags,
    failures,
    close,
    stDir,
    smaRising,
    smaFalling,
    ao,
    rsi,
    isQualified: final === 'RISE' || final === 'FALL',
  }
}

/** Evaluate complete, consecutive candles; restart warmup after each gap. */
export function evaluateAll(candles: Candle[]): SignalEvaluation[] {
  const out: SignalEvaluation[] = []
  for (const segment of closedSegments(candles)) {
    const series = enrich(segment)
    for (let i = WARMUP_BARS; i < segment.length; i++) {
      out.push(evaluateIndex(series, i))
    }
  }
  return out
}

export function evaluateLatestClosed(candles: Candle[]): SignalEvaluation | null {
  const closed = closedSegments(candles).at(-1)
  const latestClosed = candles.filter((c) => c.isClosed).at(-1)
  if (!closed || closed.length <= WARMUP_BARS || closed.at(-1) !== latestClosed) return null
  const series = enrich(closed)
  return evaluateIndex(series, closed.length - 1)
}
