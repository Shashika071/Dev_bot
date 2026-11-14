/**
 * Prospective confirmed-C5 context study (paper-only, separate from R1–R8).
 */
import type { Candle, ConfirmationFlags, Tick } from '../types'
import type { Agreement, CandleBehavior, TfTrend, ZoneDistance } from '../timeframes/timeframeContext'
import { closedReadingFromBars, completedBarsFor } from '../timeframes/timeframeContext'
import { evaluateC5LatestClosed } from '../strategy/c5Strategy'
import { setupKey } from '../journal/decisionJournal'
import type { FrozenContractSettings } from './contractSettings'
import type { ResearchSide, TouchPaperState } from './strategies/types'
import {
  advanceTouchPaper,
  attachProposalQuote,
  expireTouchPaper,
  hasPendingTouch,
  maybeStartTouchPaper,
  type PaperQuote,
} from './touchPaper'
import type { ResearchObservation } from './strategies/types'

export type C5ContextTfSnapshot = {
  timeframe: 5 | 15
  trend: TfTrend | null
  support: { mid: number; hi: number; lo: number; distance: ZoneDistance } | null
  resistance: { mid: number; hi: number; lo: number; distance: ZoneDistance } | null
  behavior: CandleBehavior | null
  behaviorText: string | null
  agreement: Agreement | null
  agreementReason: string | null
  candleTime: number | null
  forming: boolean
}

export type C5ContextObservation = {
  id: string
  side: ResearchSide
  candleTime: number
  signalEpoch: number
  receiptEpoch: number
  entryReferencePrice: number
  entryTick: Tick
  reason: string
  passed: number
  total: number
  confirmations: ConfirmationFlags
  m5: C5ContextTfSnapshot
  m15: C5ContextTfSnapshot
  lastTickEpoch: number
  touch?: TouchPaperState
}

export type C5CohortId = 'all' | 'm5_support' | 'm15_support' | 'both_support'

export interface C5CohortTouchSummary {
  id: C5CohortId
  label: string
  sampleSize: number
  hits: number
  misses: number
  unknown: number
  pending: number
  /** Observed paper touch rate — not a win probability. */
  observedTouchRate: number | null
  /** Observations where the cohort TF did not Support (excluded from this cohort). */
  excludedCount: number
  /** Misses among excluded (non-supporting) observations. */
  missesExcluded: number
  /** Hits among excluded (non-supporting) observations. */
  successfulTouchesExcluded: number
  /** excluded hits / all study hits (when hits > 0). */
  excludedHitsShareOfAllHits: number | null
  quotesAvailable: number
  quotedSettled: number
  quotedHits: number
  quotedMisses: number
  quotedUnknown: number
  quotedPending: number
  observedQuotedTouchRate: number | null
  paperCost: number | null
  paperPnl: number | null
  paperReturn: number | null
  paperPnlLower: number | null
  paperPnlUpper: number | null
  pnlUnavailable: boolean
}

export interface C5CohortSummaryBundle {
  generatedAt: number
  observationCount: number
  cohorts: C5CohortTouchSummary[]
}

export function c5ContextId(candleTime: number, side: ResearchSide): string {
  return setupKey(candleTime, side)
}

function emptyTfSnapshot(tf: 5 | 15): C5ContextTfSnapshot {
  return {
    timeframe: tf,
    trend: null,
    support: null,
    resistance: null,
    behavior: null,
    behaviorText: null,
    agreement: null,
    agreementReason: null,
    candleTime: null,
    forming: false,
  }
}

function snapshotFromClosed(
  candles: Candle[],
  tf: 5 | 15,
  m1Side: ResearchSide | null,
  livePrice: number,
): C5ContextTfSnapshot {
  const bars = completedBarsFor(candles, tf)
  const reading = closedReadingFromBars(bars, tf, m1Side, livePrice)
  if (!reading) return emptyTfSnapshot(tf)
  const support = reading.support
  const resistance = reading.resistance
  return {
    timeframe: tf,
    trend: reading.trend,
    support: support?.present
      ? { mid: support.mid, hi: support.hi, lo: support.lo, distance: support.distance }
      : null,
    resistance: resistance?.present
      ? { mid: resistance.mid, hi: resistance.hi, lo: resistance.lo, distance: resistance.distance }
      : null,
    behavior: reading.behavior,
    behaviorText: reading.behaviorText,
    agreement: reading.agreement,
    agreementReason: reading.agreementReason,
    candleTime: reading.candleTime,
    forming: false,
  }
}

function asTouchCarrier(obs: C5ContextObservation): ResearchObservation {
  return obs as unknown as ResearchObservation
}

export function freezeC5ContextSnapshots(
  candles: Candle[],
  side: ResearchSide,
  livePrice: number,
): { m5: C5ContextTfSnapshot; m15: C5ContextTfSnapshot } {
  return {
    m5: snapshotFromClosed(candles, 5, side, livePrice),
    m15: snapshotFromClosed(candles, 15, side, livePrice),
  }
}

export function createC5ContextObservation(
  candles: Candle[],
  ev: NonNullable<ReturnType<typeof evaluateC5LatestClosed>>,
  tick: Tick,
  receiptEpoch: number,
  contract: FrozenContractSettings,
): C5ContextObservation | null {
  if (!ev.isQualified || (ev.final !== 'RISE' && ev.final !== 'FALL')) return null
  const side = ev.final
  const { m5, m15 } = freezeC5ContextSnapshots(candles, side, tick.quote)
  let obs: C5ContextObservation = {
    id: c5ContextId(ev.time, side),
    side,
    candleTime: ev.time,
    signalEpoch: tick.epoch,
    receiptEpoch,
    entryReferencePrice: tick.quote,
    entryTick: tick,
    reason: `Confirmed C5 ${side} (${ev.requiredPassed}/${ev.requiredTotal})`,
    passed: ev.requiredPassed ?? 4,
    total: ev.requiredTotal ?? 4,
    confirmations: ev.confirmations,
    m5,
    m15,
    lastTickEpoch: tick.epoch,
  }
  obs = maybeStartC5TouchPaper(obs, contract, tick)
  return obs
}

export function maybeStartC5TouchPaper(
  obs: C5ContextObservation,
  contract: FrozenContractSettings,
  tick: Tick,
): C5ContextObservation {
  const withTouch = maybeStartTouchPaper(asTouchCarrier(obs), contract, tick)
  return { ...obs, touch: withTouch.touch }
}

export function advanceC5TouchPaper(
  obs: C5ContextObservation,
  tick: Tick | null,
  healthy: boolean,
  contract: FrozenContractSettings,
  receiptEpoch = tick?.epoch ?? obs.receiptEpoch,
): C5ContextObservation {
  const adv = advanceTouchPaper(asTouchCarrier(obs), tick, healthy, contract, receiptEpoch)
  return { ...obs, touch: adv.touch, lastTickEpoch: tick?.epoch ?? obs.lastTickEpoch }
}

export function expireC5TouchPaper(
  obs: C5ContextObservation,
  now: number,
  contract: FrozenContractSettings,
): C5ContextObservation {
  const adv = expireTouchPaper(asTouchCarrier(obs), now, contract)
  return { ...obs, touch: adv.touch }
}

export function attachC5ProposalQuote(
  obs: C5ContextObservation,
  quote: PaperQuote,
  contract: FrozenContractSettings,
): C5ContextObservation {
  const adv = attachProposalQuote(asTouchCarrier(obs), quote, contract)
  return { ...obs, touch: adv.touch }
}

export function hasPendingC5Touch(obs: C5ContextObservation): boolean {
  return hasPendingTouch(asTouchCarrier(obs))
}

function tfSupports(agreement: Agreement | null): boolean {
  return agreement === 'Supports'
}

function inCohort(obs: C5ContextObservation, id: C5CohortId): boolean {
  if (id === 'all') return true
  if (id === 'm5_support') return tfSupports(obs.m5.agreement)
  if (id === 'm15_support') return tfSupports(obs.m15.agreement)
  return tfSupports(obs.m5.agreement) && tfSupports(obs.m15.agreement)
}

function excludedByCohort(obs: C5ContextObservation, id: C5CohortId): boolean {
  if (id === 'all') return false
  return !inCohort(obs, id)
}

function cohortPaperPnl(rows: C5ContextObservation[]) {
  const quoted = rows.filter((r) => r.touch?.quoteAvailable)
  const settled = quoted.filter((r) =>
    (r.touch?.quotedPath?.status === 'hit' || r.touch?.quotedPath?.status === 'miss')
    && Number.isFinite(r.touch.paperPnl) && r.touch.proposalAsk != null && r.touch.proposalAsk > 0)
  const cost = settled.reduce((s, r) => s + r.touch!.proposalAsk!, 0)
  const pnlVals = settled.map((r) => r.touch!.paperPnl!)
  const pnl = pnlVals.length ? pnlVals.reduce((a, b) => a + b, 0) : null
  const unknownPriced = quoted.filter((r) =>
    r.touch?.quotedPath?.status === 'unknown'
    && r.touch.proposalAsk != null && Number.isFinite(r.touch.proposalAsk) && r.touch.proposalAsk > 0
    && r.touch.proposalPayout != null && Number.isFinite(r.touch.proposalPayout)
    && r.touch.proposalPayout >= r.touch.proposalAsk)
  const unknownCost = unknownPriced.reduce((s, r) => s + r.touch!.proposalAsk!, 0)
  const unknownProfit = unknownPriced.reduce((s, r) => s + r.touch!.proposalPayout! - r.touch!.proposalAsk!, 0)
  const hasMatured = settled.length + unknownPriced.length > 0
  return {
    quotesAvailable: quoted.length,
    quotedSettled: settled.length,
    quotedHits: settled.filter((r) => r.touch?.quotedPath?.status === 'hit').length,
    quotedMisses: settled.filter((r) => r.touch?.quotedPath?.status === 'miss').length,
    quotedUnknown: quoted.filter((r) => r.touch?.quotedPath?.status === 'unknown').length,
    quotedPending: quoted.filter((r) => !r.touch?.quotedPath || r.touch.quotedPath.status === 'pending').length,
    paperCost: settled.length ? cost : null,
    paperPnl: pnl,
    paperReturn: pnl != null && cost > 0 ? pnl / cost : null,
    paperPnlLower: hasMatured ? (pnl ?? 0) - unknownCost : null,
    paperPnlUpper: hasMatured ? (pnl ?? 0) + unknownProfit : null,
    pnlUnavailable: settled.length === 0,
  }
}

function summarizeCohort(
  all: C5ContextObservation[],
  id: C5CohortId,
  label: string,
): C5CohortTouchSummary {
  const rows = all.filter((o) => inCohort(o, id))
  const excluded = all.filter((o) => excludedByCohort(o, id))
  const hits = rows.filter((r) => r.touch?.status === 'hit').length
  const misses = rows.filter((r) => r.touch?.status === 'miss').length
  const unknown = rows.filter((r) => r.touch?.status === 'unknown').length
  const pending = rows.filter((r) => r.touch?.status === 'pending').length
  const allHits = all.filter((r) => r.touch?.status === 'hit').length
  const excludedHits = excluded.filter((r) => r.touch?.status === 'hit').length
  const excludedMisses = excluded.filter((r) => r.touch?.status === 'miss').length
  const paper = cohortPaperPnl(rows)
  const quotedRate = paper.quotedSettled > 0 ? paper.quotedHits / paper.quotedSettled : null
  return {
    id,
    label,
    sampleSize: rows.length,
    hits,
    misses,
    unknown,
    pending,
    observedTouchRate: hits + misses > 0 ? hits / (hits + misses) : null,
    excludedCount: excluded.length,
    missesExcluded: excludedMisses,
    successfulTouchesExcluded: excludedHits,
    excludedHitsShareOfAllHits: allHits > 0 ? excludedHits / allHits : null,
    ...paper,
    observedQuotedTouchRate: quotedRate,
  }
}

export function summarizeC5CohortBundle(observations: C5ContextObservation[]): C5CohortSummaryBundle {
  const cohorts: C5CohortTouchSummary[] = [
    summarizeCohort(observations, 'all', 'All confirmed C5'),
    summarizeCohort(observations, 'm5_support', 'M5 Supports'),
    summarizeCohort(observations, 'm15_support', 'M15 Supports'),
    summarizeCohort(observations, 'both_support', 'M5 + M15 Support'),
  ]
  return {
    generatedAt: Math.floor(Date.now() / 1000),
    observationCount: observations.length,
    cohorts,
  }
}

/**
 * Record a confirmed C5 on the first timely post-close tick (no R1–R8 cooldown gate).
 */
export function tryRecordC5ContextObservation(
  acceptedKeys: string[],
  candles: Candle[],
  lastClosedTime: number,
  tick: Tick,
  receiptEpoch: number,
  contract: FrozenContractSettings,
): { observation: C5ContextObservation; key: string } | null {
  const ev = evaluateC5LatestClosed(candles)
  if (!ev || !ev.isQualified || ev.time !== lastClosedTime) return null
  const key = c5ContextId(ev.time, ev.final as ResearchSide)
  if (acceptedKeys.includes(key)) return null
  const observation = createC5ContextObservation(candles, ev, tick, receiptEpoch, contract)
  if (!observation) return null
  return { observation, key }
}
