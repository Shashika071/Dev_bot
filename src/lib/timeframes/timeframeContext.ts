/**
 * Causal M5/M15 context for manual confirmation.
 * Display-only — never authorizes C5 scoring or Auto.
 */
import type { Candle } from '../types'
import { computeDynamicSRv2, type DynamicSRLevel } from '../indicators/dynamicSRv2'
import { atrRma } from '../indicators/atr'
import { sma } from '../indicators/sma'
import {
  aggregateCompleteMinutes,
  contextMinutes,
  formatHtfCountdown,
  secondsUntilHtfClose,
  type AggregatedBar,
  type TfMinutes,
} from './candleAggregation'

export type TfTrend = 'Up' | 'Down' | 'Mixed'
export type ZoneDistance = 'Inside' | 'Near' | 'Away'
export type CandleBehavior =
  | 'rejection_support'
  | 'rejection_resistance'
  | 'breakout'
  | 'inside_range'
  | 'testing'
  | 'no_clear_reaction'
export type Agreement = 'Supports' | 'Opposes' | 'Mixed'
export type ContextReadiness = 'ready' | 'warming' | 'paused'

export interface ZoneReading {
  type: 'support' | 'resistance'
  mid: number
  hi: number
  lo: number
  distance: ZoneDistance
  present: boolean
}

export interface ClosedTfReading {
  timeframe: TfMinutes
  label: '5m' | '15m'
  candleTime: number
  trend: TfTrend
  sma20: number | null
  atr14: number | null
  support: ZoneReading | null
  resistance: ZoneReading | null
  behavior: CandleBehavior
  behaviorText: string
  /** Directional vote from closed reaction/breakout: RISE, FALL, or null */
  reactionSide: 'RISE' | 'FALL' | null
  agreement: Agreement | null
  agreementReason: string
}

export interface FormingTfReading {
  timeframe: TfMinutes
  openTime: number
  closeInSeconds: number
  countdownLabel: string
  behavior: CandleBehavior
  text: string
  supportDistance: ZoneDistance | null
  resistanceDistance: ZoneDistance | null
  livePrice: number | null
}

export interface TimeframeCard {
  timeframe: TfMinutes
  label: '5m' | '15m'
  status: ContextReadiness
  statusText: string
  closed: ClosedTfReading | null
  forming: FormingTfReading | null
}

export interface MultiTimeframeContext {
  status: ContextReadiness
  statusText: string
  m1Side: 'RISE' | 'FALL' | null
  m5: TimeframeCard
  m15: TimeframeCard
}

const NEAR_ATR_MULT = 0.25
const MIN_CLOSED_FOR_SMA = 21 // need SMA20 + prior for rising/falling
const MIN_CLOSED_FOR_ATR = 14

function asCandle(bar: AggregatedBar): Candle {
  return {
    time: bar.time,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    isClosed: bar.isClosed,
  }
}

function trendAt(closed: AggregatedBar[], index: number): { trend: TfTrend; sma20: number | null } {
  if (index < 0 || index >= closed.length) return { trend: 'Mixed', sma20: null }
  const closes = closed.map((b) => b.close)
  const series = sma(closes, 20)
  const cur = series[index]
  const prev = index > 0 ? series[index - 1] : null
  if (cur == null || prev == null) return { trend: 'Mixed', sma20: cur }
  const rising = cur > prev
  const falling = cur < prev
  const close = closed[index]!.close
  if (close > cur && rising) return { trend: 'Up', sma20: cur }
  if (close < cur && falling) return { trend: 'Down', sma20: cur }
  return { trend: 'Mixed', sma20: cur }
}

function atrAt(closed: AggregatedBar[], index: number): number | null {
  if (index < MIN_CLOSED_FOR_ATR - 1) return null
  const highs = closed.map((b) => b.high)
  const lows = closed.map((b) => b.low)
  const closes = closed.map((b) => b.close)
  const atr = atrRma(highs, lows, closes, 14)
  return atr[index]
}

function zoneDistance(price: number, zone: { hi: number; lo: number }, atr: number | null): ZoneDistance {
  if (price >= zone.lo && price <= zone.hi) return 'Inside'
  const near = atr != null && atr > 0 ? atr * NEAR_ATR_MULT : 0
  const dist = price > zone.hi ? price - zone.hi : zone.lo - price
  if (near > 0 && dist <= near) return 'Near'
  return 'Away'
}

function nearestZones(
  levels: DynamicSRLevel[],
  price: number,
  atr: number | null,
): { support: ZoneReading | null; resistance: ZoneReading | null } {
  let support: DynamicSRLevel | null = null
  let resistance: DynamicSRLevel | null = null
  let bestSup = Infinity
  let bestRes = Infinity
  for (const lvl of levels) {
    if (lvl.type === 'support') {
      // Prefer supports at or below price; fall back to nearest
      const below = price >= lvl.lo
      const dist = below ? price - lvl.hi : lvl.lo - price
      const score = below ? dist : 1e9 + dist
      if (score < bestSup) {
        bestSup = score
        support = lvl
      }
    } else {
      const above = price <= lvl.hi
      const dist = above ? lvl.lo - price : price - lvl.hi
      const score = above ? dist : 1e9 + dist
      if (score < bestRes) {
        bestRes = score
        resistance = lvl
      }
    }
  }
  return {
    support: support
      ? {
          type: 'support',
          mid: support.price,
          hi: support.hi,
          lo: support.lo,
          distance: zoneDistance(price, support, atr),
          present: true,
        }
      : null,
    resistance: resistance
      ? {
          type: 'resistance',
          mid: resistance.price,
          hi: resistance.hi,
          lo: resistance.lo,
          distance: zoneDistance(price, resistance, atr),
          present: true,
        }
      : null,
  }
}

/** Large wick: ≥ half the candle range and longer than the opposite wick. */
export function isLargeLowerWick(bar: AggregatedBar): boolean {
  const range = bar.high - bar.low
  if (!(range > 0)) return false
  const lower = Math.min(bar.open, bar.close) - bar.low
  const upper = bar.high - Math.max(bar.open, bar.close)
  return lower >= range * 0.5 && lower > upper
}

export function isLargeUpperWick(bar: AggregatedBar): boolean {
  const range = bar.high - bar.low
  if (!(range > 0)) return false
  const upper = bar.high - Math.max(bar.open, bar.close)
  const lower = Math.min(bar.open, bar.close) - bar.low
  return upper >= range * 0.5 && upper > lower
}

function wickTouchesZone(wickLow: number, wickHigh: number, zone: { lo: number; hi: number }): boolean {
  return wickLow <= zone.hi && wickHigh >= zone.lo
}

/**
 * Judge closed candle behavior against zones known *before* that candle.
 */
export function judgeClosedBehavior(
  bar: AggregatedBar,
  priorClose: number | null,
  zones: DynamicSRLevel[],
): { behavior: CandleBehavior; reactionSide: 'RISE' | 'FALL' | null; text: string } {
  const supports = zones.filter((z) => z.type === 'support')
  const resistances = zones.filter((z) => z.type === 'resistance')

  // Breakouts first (completed close beyond far edge).
  for (const z of resistances) {
    const priorApproach = priorClose == null || priorClose <= z.hi
    if (priorApproach && bar.close > z.hi) {
      return {
        behavior: 'breakout',
        reactionSide: 'RISE',
        text: 'Last candle closed above resistance.',
      }
    }
  }
  for (const z of supports) {
    const priorApproach = priorClose == null || priorClose >= z.lo
    if (priorApproach && bar.close < z.lo) {
      return {
        behavior: 'breakout',
        reactionSide: 'FALL',
        text: 'Last candle closed below support.',
      }
    }
  }

  // Support rejection: large lower wick into a known support, close back above upper edge.
  if (isLargeLowerWick(bar)) {
    for (const z of supports) {
      if (wickTouchesZone(bar.low, Math.min(bar.open, bar.close), z) && bar.close > z.hi) {
        return {
          behavior: 'rejection_support',
          reactionSide: 'RISE',
          text: 'Last candle rejected support.',
        }
      }
    }
    // Large wick in middle of range → no directional vote
    if (!supports.some((z) => wickTouchesZone(bar.low, Math.min(bar.open, bar.close), z))
      && !resistances.some((z) => wickTouchesZone(bar.low, Math.min(bar.open, bar.close), z))) {
      // wick away from any zone
    }
  }

  if (isLargeUpperWick(bar)) {
    for (const z of resistances) {
      if (wickTouchesZone(Math.max(bar.open, bar.close), bar.high, z) && bar.close < z.lo) {
        return {
          behavior: 'rejection_resistance',
          reactionSide: 'FALL',
          text: 'Last candle rejected resistance.',
        }
      }
    }
  }

  // Inside nearest channel?
  for (const z of zones) {
    if (bar.close >= z.lo && bar.close <= z.hi) {
      return {
        behavior: 'inside_range',
        reactionSide: null,
        text: 'Last candle closed inside a confirmed zone.',
      }
    }
  }

  return {
    behavior: 'no_clear_reaction',
    reactionSide: null,
    text: 'No clear level reaction on the last closed candle.',
  }
}

function trendSide(trend: TfTrend): 'RISE' | 'FALL' | null {
  if (trend === 'Up') return 'RISE'
  if (trend === 'Down') return 'FALL'
  return null
}

function agreementFor(
  m1Side: 'RISE' | 'FALL' | null,
  trend: TfTrend,
  reactionSide: 'RISE' | 'FALL' | null,
  support: ZoneReading | null,
  resistance: ZoneReading | null,
  behaviorText: string,
): { agreement: Agreement | null; reason: string } {
  if (m1Side == null) {
    return {
      agreement: null,
      reason: `${trend === 'Up' ? 'Uptrend' : trend === 'Down' ? 'Downtrend' : 'Mixed trend'}. ${behaviorText}`,
    }
  }
  const tSide = trendSide(trend)
  const trendAgrees = tSide === m1Side
  const trendOpposes = tSide != null && tSide !== m1Side
  const reactionAgrees = reactionSide === m1Side
  const reactionOpposes = reactionSide != null && reactionSide !== m1Side

  // Opposing zone immediately ahead
  const opposingNear =
    m1Side === 'RISE'
      ? resistance && (resistance.distance === 'Inside' || resistance.distance === 'Near')
      : support && (support.distance === 'Inside' || support.distance === 'Near')

  if (trendAgrees && reactionAgrees) {
    return {
      agreement: 'Supports',
      reason: `${trend === 'Up' ? 'Uptrend' : 'Downtrend'}. ${behaviorText}`,
    }
  }
  if (trendOpposes && reactionOpposes) {
    return {
      agreement: 'Opposes',
      reason: `${trend === 'Up' ? 'Uptrend' : 'Downtrend'} against ${m1Side}. ${behaviorText}`,
    }
  }
  if (opposingNear) {
    const zone = m1Side === 'RISE' ? 'resistance' : 'support'
    return {
      agreement: 'Mixed',
      reason: `${trend === 'Up' ? 'Uptrend' : trend === 'Down' ? 'Downtrend' : 'Mixed trend'}, but price is near ${zone}.`,
    }
  }
  if (trendAgrees && !reactionOpposes && reactionSide == null) {
    // Trend alone is not enough for Supports — need reaction/breakout agreement.
    return {
      agreement: 'Mixed',
      reason: `${trend === 'Up' ? 'Uptrend' : 'Downtrend'}, but no confirming level reaction.`,
    }
  }
  if (trendOpposes || reactionOpposes || (trendAgrees && reactionOpposes) || (trendOpposes && reactionAgrees)) {
    return {
      agreement: 'Mixed',
      reason: `Conflicting evidence vs ${m1Side}. ${behaviorText}`,
    }
  }
  return {
    agreement: 'Mixed',
    reason: `${trend === 'Up' ? 'Uptrend' : trend === 'Down' ? 'Downtrend' : 'Mixed trend'}. ${behaviorText}`,
  }
}

/**
 * Keep the facts from one completed HTF candle frozen while refreshing the
 * direction-specific answer and live distance labels.
 */
export function refreshClosedReading(
  frozen: ClosedTfReading,
  m1Side: 'RISE' | 'FALL' | null,
  livePrice: number | null,
): ClosedTfReading {
  const refreshZone = (zone: ZoneReading | null): ZoneReading | null => {
    if (!zone || livePrice == null) return zone
    return {
      ...zone,
      distance: zoneDistance(livePrice, zone, frozen.atr14),
    }
  }
  const support = refreshZone(frozen.support)
  const resistance = refreshZone(frozen.resistance)
  const { agreement, reason } = agreementFor(
    m1Side,
    frozen.trend,
    frozen.reactionSide,
    support,
    resistance,
    frozen.behaviorText,
  )
  return {
    ...frozen,
    support,
    resistance,
    agreement,
    agreementReason: reason,
  }
}

function zonesBeforeCandle(completed: AggregatedBar[], judgeIndex: number): DynamicSRLevel[] {
  // Prefix ending before the judged candle.
  const prefix = completed.slice(0, judgeIndex).map(asCandle)
  if (prefix.length < 20) return []
  return computeDynamicSRv2(prefix).levels
}

function buildClosedReading(
  completed: AggregatedBar[],
  tf: TfMinutes,
  m1Side: 'RISE' | 'FALL' | null,
  livePrice: number | null,
): ClosedTfReading | null {
  if (completed.length < MIN_CLOSED_FOR_SMA) return null
  const index = completed.length - 1
  const bar = completed[index]!
  const { trend, sma20 } = trendAt(completed, index)
  const atr14 = atrAt(completed, index)
  const priorZones = zonesBeforeCandle(completed, index)
  // These types were fixed from the prefix ending before the judged candle.
  // Retyping from the judged close would turn a broken resistance into support
  // (and a broken support into resistance) before breakout classification.
  const priceForDist = livePrice ?? bar.close
  const { support, resistance } = nearestZones(priorZones, priceForDist, atr14)
  const priorClose = index > 0 ? completed[index - 1]!.close : null
  const judged = judgeClosedBehavior(bar, priorClose, priorZones)
  const frozen: ClosedTfReading = {
    timeframe: tf,
    label: tf === 5 ? '5m' : '15m',
    candleTime: bar.time,
    trend,
    sma20,
    atr14,
    support,
    resistance,
    behavior: judged.behavior,
    behaviorText: judged.text,
    reactionSide: judged.reactionSide,
    agreement: null,
    agreementReason: '',
  }
  return refreshClosedReading(frozen, m1Side, livePrice)
}

function formingBehavior(
  forming: AggregatedBar,
  zones: DynamicSRLevel[],
  atr: number | null,
): { behavior: CandleBehavior; text: string; supportDistance: ZoneDistance | null; resistanceDistance: ZoneDistance | null } {
  const price = forming.close
  const typed = zones.map((z) => ({
    ...z,
    type: (z.price >= price ? 'resistance' : 'support') as 'support' | 'resistance',
  }))
  const { support, resistance } = nearestZones(typed, price, atr)
  const testingRes = resistance && (resistance.distance === 'Inside' || resistance.distance === 'Near')
  const testingSup = support && (support.distance === 'Inside' || support.distance === 'Near')
  if (testingRes) {
    return {
      behavior: 'testing',
      text: 'testing resistance',
      supportDistance: support?.distance ?? null,
      resistanceDistance: resistance.distance,
    }
  }
  if (testingSup) {
    return {
      behavior: 'testing',
      text: 'testing support',
      supportDistance: support.distance,
      resistanceDistance: resistance?.distance ?? null,
    }
  }
  return {
    behavior: 'no_clear_reaction',
    text: 'no clear level test',
    supportDistance: support?.distance ?? null,
    resistanceDistance: resistance?.distance ?? null,
  }
}

function buildCard(
  m1: Candle[],
  tf: TfMinutes,
  m1Side: 'RISE' | 'FALL' | null,
  livePrice: number | null,
  nowEpoch: number,
  feedReady: boolean,
): TimeframeCard {
  const label = tf === 5 ? '5m' : '15m'
  if (!feedReady) {
    return {
      timeframe: tf,
      label,
      status: 'paused',
      statusText: 'Feed paused',
      closed: null,
      forming: null,
    }
  }
  const context = contextMinutes(m1, tf)
  const completed = aggregateCompleteMinutes(context.completed, tf)
  const forming = context.forming
  if (completed.length < MIN_CLOSED_FOR_SMA) {
    return {
      timeframe: tf,
      label,
      status: 'warming',
      statusText: 'Warming up',
      closed: null,
      forming: null,
    }
  }
  const closed = buildClosedReading(completed, tf, m1Side, livePrice)
  let formingReading: FormingTfReading | null = null
  if (forming) {
    const atr = atrAt(completed, completed.length - 1)
    // Zones known before the forming candle = zones from all completed bars.
    const zones = computeDynamicSRv2(completed.map(asCandle)).levels
    const fb = formingBehavior(forming, zones, atr)
    const left = secondsUntilHtfClose(forming.time, tf, nowEpoch)
    formingReading = {
      timeframe: tf,
      openTime: forming.time,
      closeInSeconds: left,
      countdownLabel: formatHtfCountdown(left),
      behavior: fb.behavior,
      text: `${label === '5m' ? 'M5' : 'M15'} is ${fb.text}; ${formatHtfCountdown(left)} left.`,
      supportDistance: fb.supportDistance,
      resistanceDistance: fb.resistanceDistance,
      livePrice,
    }
  }
  return {
    timeframe: tf,
    label,
    status: 'ready',
    statusText: 'Ready',
    closed,
    forming: formingReading,
  }
}

/**
 * Full multi-timeframe context from M1 candles.
 * Closed readings are pure functions of completed bars; forming is separate.
 */
export function computeMultiTimeframeContext(opts: {
  candles: Candle[]
  m1Side: 'RISE' | 'FALL' | null
  livePrice: number | null
  nowEpoch: number
  feedReady: boolean
}): MultiTimeframeContext {
  const { candles, m1Side, livePrice, nowEpoch, feedReady } = opts
  if (!feedReady) {
    const paused = (tf: TfMinutes): TimeframeCard => ({
      timeframe: tf,
      label: tf === 5 ? '5m' : '15m',
      status: 'paused',
      statusText: 'Feed paused',
      closed: null,
      forming: null,
    })
    return {
      status: 'paused',
      statusText: 'Feed paused',
      m1Side,
      m5: paused(5),
      m15: paused(15),
    }
  }
  const m5 = buildCard(candles, 5, m1Side, livePrice, nowEpoch, true)
  const m15 = buildCard(candles, 15, m1Side, livePrice, nowEpoch, true)
  const warming = m5.status === 'warming' || m15.status === 'warming'
  return {
    status: warming && m5.status !== 'ready' && m15.status !== 'ready' ? 'warming' : 'ready',
    statusText: warming && m5.status !== 'ready' && m15.status !== 'ready' ? 'Warming up' : 'Ready',
    m1Side,
    m5,
    m15,
  }
}

/** Snapshot of closed facts only — safe to freeze in journals. */
export function freezeClosedContextSnapshot(card: TimeframeCard): {
  timeframe: TfMinutes
  trend: TfTrend | null
  support: { mid: number; hi: number; lo: number; distance: ZoneDistance } | null
  resistance: { mid: number; hi: number; lo: number; distance: ZoneDistance } | null
  behavior: CandleBehavior | null
  behaviorText: string | null
  agreement: Agreement | null
  agreementReason: string | null
  candleTime: number | null
  forming: boolean
} {
  const c = card.closed
  return {
    timeframe: card.timeframe,
    trend: c?.trend ?? null,
    support: c?.support
      ? { mid: c.support.mid, hi: c.support.hi, lo: c.support.lo, distance: c.support.distance }
      : null,
    resistance: c?.resistance
      ? { mid: c.resistance.mid, hi: c.resistance.hi, lo: c.resistance.lo, distance: c.resistance.distance }
      : null,
    behavior: c?.behavior ?? null,
    behaviorText: c?.behaviorText ?? null,
    agreement: c?.agreement ?? null,
    agreementReason: c?.agreementReason ?? null,
    candleTime: c?.candleTime ?? null,
    forming: card.forming != null,
  }
}

export function emptyMultiTimeframeContext(m1Side: 'RISE' | 'FALL' | null = null): MultiTimeframeContext {
  const empty = (tf: TfMinutes): TimeframeCard => ({
    timeframe: tf,
    label: tf === 5 ? '5m' : '15m',
    status: 'warming',
    statusText: 'Warming up',
    closed: null,
    forming: null,
  })
  return {
    status: 'warming',
    statusText: 'Warming up',
    m1Side,
    m5: empty(5),
    m15: empty(15),
  }
}

/** Exported for research: closed-only reading at a specific completed series. */
export function closedReadingFromBars(
  completed: AggregatedBar[],
  tf: TfMinutes,
  m1Side: 'RISE' | 'FALL' | null,
  livePrice: number | null = null,
): ClosedTfReading | null {
  return buildClosedReading(completed, tf, m1Side, livePrice)
}

export function completedBarsFor(m1: Candle[], tf: TfMinutes): AggregatedBar[] {
  return aggregateCompleteMinutes(contextMinutes(m1, tf).completed, tf)
}
