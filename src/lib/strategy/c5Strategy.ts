/**
 * C5 — main live strategy and shared research mask keys.
 * Required: fresh UT + Supertrend + SMA20 + RSI14. AO recorded, not required.
 */
import type { Candle, ConfirmationFlags, FinalSignal, SignalEvaluation, TrendDir } from '../types'
import { closedSegments, isValidCandle } from '../deriv/candles'
import { enrich, WARMUP_BARS, type EnrichedSeries } from './signalEngine'
import {
  fallFlagsAt,
  maskPasses,
  riseFlagsAt,
} from './conditionFlags'

export const C5_STRATEGY_ID = 'C5' as const
export const C5_STRATEGY_LABEL = 'C5 — UT + Supertrend + SMA20 + RSI14'

/** Same keys as research MASKS.C5 — do not add AO. */
export const C5_REQUIRED: (keyof ConfirmationFlags)[] = ['ut', 'supertrend', 'sma', 'rsi']

/** Maximum contiguous closed candles used for C5 indicator calculation. */
export const C5_CALC_WINDOW = 260

export function sliceC5Window(candles: Candle[]): Candle[] {
  if (candles.length <= C5_CALC_WINDOW) return candles
  return candles.slice(-C5_CALC_WINDOW)
}

function requiredFailures(
  side: 'RISE' | 'FALL',
  flags: ConfirmationFlags,
  series: EnrichedSeries,
  i: number,
): string[] {
  const failures: string[] = []
  const close = series.candles[i]!.close
  const sma = series.sma20[i]
  const rsi = series.rsi[i]
  const stDir = series.stDir[i]

  if (!flags.ut) {
    if (series.utBuy[i] && series.utSell[i]) failures.push('Simultaneous UT BUY and SELL — rejected')
    else failures.push(side === 'RISE'
      ? 'No fresh UT Bot BUY crossover on this completed candle'
      : 'No fresh UT Bot SELL crossover on this completed candle')
  }
  if (!flags.supertrend) {
    failures.push(side === 'RISE' ? 'Supertrend not bullish' : 'Supertrend not bearish')
    if (stDir === 0) failures.push('Supertrend direction unavailable / flat')
  }
  if (!flags.sma) {
    if (sma == null) failures.push('SMA20 unavailable')
    else if (side === 'RISE') {
      if (!(close > sma)) failures.push('Close not above SMA20')
      if (!series.smaRising[i]) failures.push('SMA20 not rising')
    } else {
      if (!(close < sma)) failures.push('Close not below SMA20')
      if (!series.smaFalling[i]) failures.push('SMA20 not falling')
    }
  }
  if (!flags.rsi) {
    if (rsi == null) failures.push('RSI unavailable')
    else if (side === 'RISE') {
      if (!(rsi > 52)) failures.push('RSI not greater than 52')
      if (!(rsi < 70)) failures.push('RSI not less than 70')
    } else {
      if (!(rsi < 48)) failures.push('RSI not less than 48')
      if (!(rsi > 30)) failures.push('RSI not greater than 30')
    }
  }
  return failures
}

/**
 * Evaluate C5 at index `i` of an already-enriched contiguous closed series.
 * AO is always reflected in confirmations.ao but never gates qualification.
 */
export function evaluateC5Index(series: EnrichedSeries, i: number): SignalEvaluation {
  const c = series.candles[i]!
  const utBuy = series.utBuy[i]
  const utSell = series.utSell[i]
  const rawUt = utBuy && !utSell ? 'BUY' as const : utSell && !utBuy ? 'SELL' as const : 'NONE' as const
  const rise = riseFlagsAt(series, i)
  const fall = fallFlagsAt(series, i)

  let final: FinalSignal = 'NO_TRADE'
  let flags: ConfirmationFlags = {
    ut: false, supertrend: false, sma: false, ao: false, rsi: false,
  }
  let failures: string[] = []

  if (!c.isClosed || !isValidCandle(c)) {
    failures = ['Candle is incomplete or invalid']
  } else if (utBuy && utSell) {
    failures = ['Simultaneous UT BUY and SELL — rejected']
  } else {
    const riseOk = maskPasses(rise, C5_REQUIRED)
    const fallOk = maskPasses(fall, C5_REQUIRED)
    if (riseOk && !fallOk) {
      final = 'RISE'
      flags = { ...rise }
      failures = []
    } else if (fallOk && !riseOk) {
      final = 'FALL'
      flags = { ...fall }
      failures = []
    } else if (riseOk && fallOk) {
      // Prefer side with UT crossover matching (should be exclusive); else NO_TRADE.
      failures = ['Both C5 sides passed unexpectedly — rejected']
    } else if (rawUt === 'BUY') {
      flags = { ...rise }
      failures = requiredFailures('RISE', rise, series, i)
    } else if (rawUt === 'SELL') {
      flags = { ...fall }
      failures = requiredFailures('FALL', fall, series, i)
    } else {
      // No fresh UT — still surface best-effort flags for UI diagnostics.
      const preferRise = rise.supertrend || rise.sma || rise.rsi
      flags = preferRise ? { ...rise } : { ...fall }
      failures = [
        'No fresh UT Bot BUY/SELL crossover on this completed candle',
        ...requiredFailures(preferRise ? 'RISE' : 'FALL', flags, series, i).filter((f) => !f.includes('UT')),
      ]
    }
  }

  return {
    time: c.time,
    final,
    rawUt,
    confirmations: flags,
    failures,
    close: c.close,
    stDir: series.stDir[i] as TrendDir,
    smaRising: series.smaRising[i],
    smaFalling: series.smaFalling[i],
    ao: series.ao[i] ?? null,
    rsi: series.rsi[i] ?? null,
    isQualified: final === 'RISE' || final === 'FALL',
    strategyId: C5_STRATEGY_ID,
    requiredPassed: C5_REQUIRED.filter((k) => flags[k]).length,
    requiredTotal: C5_REQUIRED.length,
  }
}

/**
 * Historical C5 on contiguous closed segments.
 * Each candle uses only history available at that candle, capped at C5_CALC_WINDOW.
 */
export function evaluateC5All(candles: Candle[]): SignalEvaluation[] {
  const out: SignalEvaluation[] = []
  for (const segment of closedSegments(candles)) {
    if (segment.length <= WARMUP_BARS) continue
    // Short segments: one enrich is equivalent to per-bar windows (causal indicators).
    if (segment.length <= C5_CALC_WINDOW) {
      const series = enrich(segment)
      for (let i = WARMUP_BARS; i < segment.length; i++) out.push(evaluateC5Index(series, i))
      continue
    }
    for (let end = WARMUP_BARS; end < segment.length; end++) {
      const start = Math.max(0, end + 1 - C5_CALC_WINDOW)
      const win = segment.slice(start, end + 1)
      if (win.length <= WARMUP_BARS) continue
      const series = enrich(win)
      out.push(evaluateC5Index(series, win.length - 1))
    }
  }
  return out
}

export function evaluateC5LatestClosed(candles: Candle[]): SignalEvaluation | null {
  const closed = closedSegments(candles).at(-1)
  const latestClosed = candles.filter((c) => c.isClosed).at(-1)
  if (!closed || closed.length <= WARMUP_BARS || closed.at(-1) !== latestClosed) return null
  const win = sliceC5Window(closed)
  if (win.length <= WARMUP_BARS) return null
  const series = enrich(win)
  return evaluateC5Index(series, win.length - 1)
}
