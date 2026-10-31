/**
 * Optional post–five-rule filters. Defaults OFF so baseline stays unchanged.
 * These do not replace UT/ST/SMA/AO/RSI — they only reject already-qualified candidates.
 */
import type { Candle } from '../types'
import { atrRma } from '../indicators/atr'
import { sma } from '../indicators/sma'

export interface VolatilityFilter {
  /** Require ATR14 >= targetDistance * minAtrMultOfTarget */
  minAtrMultOfTarget?: number
  /** Require ATR14 <= targetDistance * maxAtrMultOfTarget */
  maxAtrMultOfTarget?: number
}

export interface LargeCandleFilter {
  /** Skip when signal candle range > atrMult * ATR14 */
  atrMult: number
}

export interface FilterConfig {
  /** Absolute touch / barrier distance used for ATR comparisons (default 0.03) */
  targetDistance: number
  volatility?: VolatilityFilter | null
  largeCandle?: LargeCandleFilter | null
  /** RISE only if last completed M5 close > rising M5 SMA20; FALL inverse */
  m5Trend?: boolean
}

export const DEFAULT_FILTER_CONFIG: FilterConfig = {
  targetDistance: 0.03,
  volatility: null,
  largeCandle: null,
  m5Trend: false,
}

/** Presets for A/B comparison (predeclared — not brute-force search). */
export const FILTER_PRESETS: Record<string, FilterConfig> = {
  baseline: { ...DEFAULT_FILTER_CONFIG },
  vol_atr_ge_10x: {
    targetDistance: 0.03,
    volatility: { minAtrMultOfTarget: 10 },
    largeCandle: null,
    m5Trend: false,
  },
  vol_atr_10_to_40x: {
    targetDistance: 0.03,
    volatility: { minAtrMultOfTarget: 10, maxAtrMultOfTarget: 40 },
    largeCandle: null,
    m5Trend: false,
  },
  vol_atr_le_25x: {
    targetDistance: 0.03,
    volatility: { maxAtrMultOfTarget: 25 },
    largeCandle: null,
    m5Trend: false,
  },
  skip_large_candle_1_5x: {
    targetDistance: 0.03,
    volatility: null,
    largeCandle: { atrMult: 1.5 },
    m5Trend: false,
  },
  m5_trend: {
    targetDistance: 0.03,
    volatility: null,
    largeCandle: null,
    m5Trend: true,
  },
  large_candle_and_m5: {
    targetDistance: 0.03,
    volatility: null,
    largeCandle: { atrMult: 1.5 },
    m5Trend: true,
  },
  vol_band_and_large_candle: {
    targetDistance: 0.03,
    volatility: { minAtrMultOfTarget: 10, maxAtrMultOfTarget: 40 },
    largeCandle: { atrMult: 1.5 },
    m5Trend: false,
  },
  all_three: {
    targetDistance: 0.03,
    volatility: { minAtrMultOfTarget: 10, maxAtrMultOfTarget: 40 },
    largeCandle: { atrMult: 1.5 },
    m5Trend: true,
  },
}

export interface FilterSeries {
  atr14: (number | null)[]
  /** Last completed M5 close known at this M1 bar (null if unavailable) */
  m5Close: (number | null)[]
  m5Sma20: (number | null)[]
  m5SmaRising: boolean[]
  m5SmaFalling: boolean[]
}

export function buildFilterSeries(candles: Candle[]): FilterSeries {
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr14 = atrRma(highs, lows, closes, 14)

  const m5Bars: { openTime: number; close: number }[] = []
  const byOpen = new Map<number, Candle[]>()
  for (const c of candles) {
    const open5 = Math.floor(c.time / 300) * 300
    const list = byOpen.get(open5) ?? []
    list.push(c)
    byOpen.set(open5, list)
  }
  for (const [openTime, bars] of [...byOpen.entries()].sort((a, b) => a[0] - b[0])) {
    bars.sort((a, b) => a.time - b.time)
    if (bars.length < 5) continue
    // Require full 5 M1 minutes present
    const expected = [0, 60, 120, 180, 240].map((o) => openTime + o)
    if (!expected.every((t, i) => bars[i]?.time === t)) continue
    m5Bars.push({
      openTime,
      close: bars[4].close,
    })
  }
  const m5Closes = m5Bars.map((b) => b.close)
  const m5Sma = sma(m5Closes, 20)
  const m5Index = new Map(m5Bars.map((b, idx) => [b.openTime, idx]))

  const m5Close: (number | null)[] = new Array(candles.length).fill(null)
  const m5Sma20: (number | null)[] = new Array(candles.length).fill(null)
  const m5SmaRising = new Array(candles.length).fill(false)
  const m5SmaFalling = new Array(candles.length).fill(false)

  for (let i = 0; i < candles.length; i++) {
    const T = candles[i].time
    const open5 = Math.floor(T / 300) * 300
    const offset = T - open5
    const completedOpen = offset === 240 ? open5 : open5 - 300
    const idx = m5Index.get(completedOpen)
    if (idx == null) continue
    m5Close[i] = m5Bars[idx].close
    m5Sma20[i] = m5Sma[idx]
    if (idx > 0 && m5Sma[idx] != null && m5Sma[idx - 1] != null) {
      m5SmaRising[i] = (m5Sma[idx] as number) > (m5Sma[idx - 1] as number)
      m5SmaFalling[i] = (m5Sma[idx] as number) < (m5Sma[idx - 1] as number)
    }
  }

  return { atr14, m5Close, m5Sma20, m5SmaRising, m5SmaFalling }
}

export interface FilterCheck {
  passed: boolean
  failures: string[]
  atr14: number | null
  candleRange: number
  atrMultipleOfTarget: number | null
}

/**
 * Apply optional filters to a side that already passed the five base rules.
 */
export function applyOptionalFilters(
  candles: Candle[],
  filterSeries: FilterSeries,
  i: number,
  side: 'RISE' | 'FALL',
  config: FilterConfig,
): FilterCheck {
  const failures: string[] = []
  const c = candles[i]
  const atr = filterSeries.atr14[i]
  const range = c.high - c.low
  const target = config.targetDistance
  const atrMultipleOfTarget = atr != null && target > 0 ? atr / target : null

  if (config.volatility) {
    const { minAtrMultOfTarget, maxAtrMultOfTarget } = config.volatility
    if (atr == null) failures.push('ATR14 unavailable for volatility filter')
    else {
      if (minAtrMultOfTarget != null && atr < target * minAtrMultOfTarget) {
        failures.push(
          `Volatility filter: ATR14 ${atr.toFixed(4)} < ${target}*${minAtrMultOfTarget}`,
        )
      }
      if (maxAtrMultOfTarget != null && atr > target * maxAtrMultOfTarget) {
        failures.push(
          `Volatility filter: ATR14 ${atr.toFixed(4)} > ${target}*${maxAtrMultOfTarget}`,
        )
      }
    }
  }

  if (config.largeCandle) {
    if (atr == null) failures.push('ATR14 unavailable for large-candle filter')
    else if (range > config.largeCandle.atrMult * atr) {
      failures.push(
        `Large-candle filter: range ${range.toFixed(4)} > ${config.largeCandle.atrMult}×ATR14`,
      )
    }
  }

  if (config.m5Trend) {
    const m5c = filterSeries.m5Close[i]
    const m5s = filterSeries.m5Sma20[i]
    if (m5c == null || m5s == null) {
      failures.push('M5 trend filter: completed M5 SMA20 unavailable')
    } else if (side === 'RISE') {
      if (!(m5c > m5s && filterSeries.m5SmaRising[i])) {
        failures.push('M5 trend filter: need M5 close above rising SMA20 for RISE')
      }
    } else if (!(m5c < m5s && filterSeries.m5SmaFalling[i])) {
      failures.push('M5 trend filter: need M5 close below falling SMA20 for FALL')
    }
  }

  return {
    passed: failures.length === 0,
    failures,
    atr14: atr,
    candleRange: range,
    atrMultipleOfTarget,
  }
}
