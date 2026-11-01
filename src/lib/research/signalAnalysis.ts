/**
 * Descriptive analysis of signal observations.
 * Wilson intervals are labeled descriptive — overlapping/correlated setups.
 */
import { wilsonInterval } from './statistics'
import {
  OBSERVATION_HORIZONS_MIN,
  type CandleSizeBucket,
  type ObservationHorizonMin,
  type SignalObservation,
} from './signalObservation'

export const INSUFFICIENT_SAMPLE = 20

export interface HorizonCounts {
  total: number
  completed: number
  pending: number
  unknown: number
  unchanged: number
  continuation: number
  reversal: number
}

export interface HorizonStats {
  minutes: ObservationHorizonMin
  counts: HorizonCounts
  /**
   * Continuation among directional outcomes only (continuation + reversal).
   * Unchanged / unknown / pending excluded from denominator.
   */
  continuationAmongDirectional: number | null
  /** Reversal among directional outcomes. */
  reversalAmongDirectional: number | null
  /** Descriptive Wilson interval on continuationAmongDirectional. */
  descriptiveWilson: [number, number] | null
  avgSignedMove: number | null
  avgSignedMoveInAtr: number | null
  sampleSizeDirectional: number
  /** Matched 50/50 random-direction continuation rate (same times/prices). */
  randomContinuationAmongDirectional: number | null
  randomSampleSizeDirectional: number
  randomDescriptiveWilson: [number, number] | null
  insufficient: boolean
}

export interface BreakdownRow {
  label: string
  key: string
  n: number
  horizons: HorizonStats[]
}

export interface SignalAnalysisReport {
  totalObservations: number
  pendingObservations: number
  retentionLimit: number
  byHorizon: HorizonStats[]
  byDirection: BreakdownRow[]
  byConditionKey: BreakdownRow[]
  byScoreBand: BreakdownRow[]
  byCandleSize: BreakdownRow[]
  notes: string[]
}

function emptyCounts(): HorizonCounts {
  return {
    total: 0,
    completed: 0,
    pending: 0,
    unknown: 0,
    unchanged: 0,
    continuation: 0,
    reversal: 0,
  }
}

function mean(xs: number[]): number | null {
  if (!xs.length) return null
  return xs.reduce((a, b) => a + b, 0) / xs.length
}

function statsForHorizon(
  minutes: ObservationHorizonMin,
  observations: SignalObservation[],
): HorizonStats {
  const counts = emptyCounts()
  const signed: number[] = []
  const signedAtr: number[] = []
  let directionalCont = 0
  let directionalRev = 0
  let randomCont = 0
  let randomRev = 0

  for (const obs of observations) {
    const h = obs.horizons.find((x) => x.minutes === minutes)
    if (!h) continue
    counts.total += 1

    if (h.status === 'pending') counts.pending += 1
    else if (h.status === 'unknown') counts.unknown += 1
    else if (h.status === 'unchanged') {
      counts.unchanged += 1
      counts.completed += 1
    } else if (h.status === 'continuation') {
      counts.continuation += 1
      counts.completed += 1
    } else if (h.status === 'reversal') {
      counts.reversal += 1
      counts.completed += 1
    }

    if (h.status === 'continuation' || h.status === 'reversal' || h.status === 'unchanged') {
      if (h.signedMove != null && Number.isFinite(h.signedMove)) signed.push(h.signedMove)
      if (h.signedMoveInAtr != null && Number.isFinite(h.signedMoveInAtr)) signedAtr.push(h.signedMoveInAtr)
    }
    if (h.status === 'continuation') directionalCont += 1
    if (h.status === 'reversal') directionalRev += 1
    if (h.randomClass === 'continuation') randomCont += 1
    if (h.randomClass === 'reversal') randomRev += 1
  }

  const sampleSizeDirectional = directionalCont + directionalRev
  const continuationAmongDirectional =
    sampleSizeDirectional ? directionalCont / sampleSizeDirectional : null
  const reversalAmongDirectional =
    sampleSizeDirectional ? directionalRev / sampleSizeDirectional : null

  const randomSampleSizeDirectional = randomCont + randomRev
  const randomContinuationAmongDirectional =
    randomSampleSizeDirectional ? randomCont / randomSampleSizeDirectional : null

  return {
    minutes,
    counts,
    continuationAmongDirectional,
    reversalAmongDirectional,
    descriptiveWilson: sampleSizeDirectional
      ? wilsonInterval(directionalCont, sampleSizeDirectional)
      : null,
    avgSignedMove: mean(signed),
    avgSignedMoveInAtr: mean(signedAtr),
    sampleSizeDirectional,
    randomContinuationAmongDirectional,
    randomSampleSizeDirectional,
    randomDescriptiveWilson: randomSampleSizeDirectional
      ? wilsonInterval(randomCont, randomSampleSizeDirectional)
      : null,
    insufficient: sampleSizeDirectional < INSUFFICIENT_SAMPLE,
  }
}

function breakdown(
  observations: SignalObservation[],
  keyFn: (o: SignalObservation) => string,
  labelFn: (key: string) => string,
): BreakdownRow[] {
  const groups = new Map<string, SignalObservation[]>()
  for (const o of observations) {
    const k = keyFn(o)
    const list = groups.get(k) ?? []
    list.push(o)
    groups.set(k, list)
  }
  return [...groups.entries()]
    .map(([key, rows]) => ({
      label: labelFn(key),
      key,
      n: rows.length,
      horizons: OBSERVATION_HORIZONS_MIN.map((m) => statsForHorizon(m, rows)),
    }))
    .sort((a, b) => b.n - a.n)
}

const SIZE_LABELS: Record<CandleSizeBucket, string> = {
  below_1_atr: 'Last completed range < 1 ATR',
  '1_to_2_atr': 'Last completed range 1–2 ATR',
  above_2_atr: 'Last completed range > 2 ATR',
  unknown: 'ATR / range unknown',
}

export function analyzeSignalObservations(
  observations: SignalObservation[],
  retentionLimit: number,
): SignalAnalysisReport {
  const byHorizon = OBSERVATION_HORIZONS_MIN.map((m) => statsForHorizon(m, observations))

  return {
    totalObservations: observations.length,
    pendingObservations: observations.filter((o) => o.pending).length,
    retentionLimit,
    byHorizon,
    byDirection: breakdown(
      observations,
      (o) => o.direction,
      (k) => k,
    ),
    byConditionKey: breakdown(
      observations,
      (o) => o.conditionKey,
      (k) => {
        const miss = k.split('|miss:')[1] ?? 'none'
        return miss === 'none' ? '5/5 all conditions' : `Missing ${miss}`
      },
    ),
    byScoreBand: breakdown(
      observations,
      (o) => {
        if (o.passed >= 5) return '5of5'
        if (o.passed === 4) return '4of5'
        return `${o.passed}of5`
      },
      (k) => {
        if (k === '5of5') return 'Five-of-five (5/5)'
        if (k === '4of5') return 'Four-of-five (4/5)'
        return `${k.replace('of', '/')} setups`
      },
    ),
    byCandleSize: breakdown(
      observations,
      (o) => o.atr.sizeBucket,
      (k) => SIZE_LABELS[k as CandleSizeBucket] ?? k,
    ),
    notes: [
      '80% means four of five live conditions passed — not an 80% win probability.',
      'Continuation / reversal = signed price move vs signal direction at each horizon; not a structural trend call.',
      'Continuation % denominator = continuation + reversal only (unchanged, pending, and unknown excluded).',
      'Wilson intervals are descriptive; overlapping candles and serial correlation can make them too narrow.',
      'Matched 50/50 random direction uses the same observation times and prices; it tests directional selection, not contract profitability or entry timing value.',
      `Retained history is capped at ${retentionLimit} observations.`,
      'These size groups are descriptive research categories, not validated trading filters.',
      'Observations are public-tick research records — not executed trades or broker fills.',
    ],
  }
}
