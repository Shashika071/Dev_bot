import { wilsonInterval } from './statistics'
import { STRATEGY_REGISTRY } from './strategies/registry'
import type { ExperimentState } from './experimentEngine'
import { RESEARCH_CODE_VERSION } from './experimentEngine'
import type { HorizonMin, ResearchObservation, StrategyId } from './strategies/types'
import { HORIZON_MINUTES } from './horizons'
import { c5StudyContract } from './contractSettings'
import {
  summarizeC5CohortBundle,
  type C5CohortSummaryBundle,
} from './c5ContextStudy'

const INSUFFICIENT = 20

export interface StrategyHorizonSummary {
  minutes: HorizonMin
  total: number
  pending: number
  unknown: number
  unchanged: number
  continuation: number
  reversal: number
  contRate: number | null
  avgSignedMove: number | null
  avgSignedMoveInAtr: number | null
  randomContRate: number | null
  descriptiveWilson: [number, number] | null
  insufficient: boolean
}

export interface SideTouchStats {
  hits: number
  misses: number
  unknown: number
  pending: number
  winRate: number | null
  /** Among misses with a known 7m endpoint: continuation = same side moved; reversal = opposite. */
  missContinuation: number
  missReversal: number
  missUnchanged: number
  /** If no touch, which way price more often went by minute 7 (same / opposite / flat / unknown). */
  ifNoTouchPrefer: 'same_side' | 'opposite_side' | 'flat' | 'unknown' | null
}

export interface BestPick {
  id: StrategyId
  name: string
  winRate: number
  hits: number
  misses: number
  known: number
}

export interface StrategySummary {
  id: StrategyId
  name: string
  family: string
  rules: string
  candidatesAccepted: number
  pending: number
  known: number
  horizons: StrategyHorizonSummary[]
  touchHits: number
  touchMisses: number
  touchUnknown: number
  touchPending: number
  touchWinRate: number | null
  rise: SideTouchStats
  fall: SideTouchStats
  quoteCoverageRate: number | null
  signalsPerDay: number | null
  /** Median seconds from signal epoch to first touch among reference hits. */
  medianTimeToTouchSec: number | null
  quotesAvailable: number
  quotedSettled: number
  quotedHits: number
  quotedMisses: number
  quotedUnknown: number
  quotedPending: number
  quotedWinRate: number | null
  /** Settled results plus unknown quoted outcomes, excluding still-pending contracts. */
  maturedQuotedCost: number | null
  paperPnlLower: number | null
  paperPnlUpper: number | null
  paperCost: number | null
  paperPnl: number | null
  paperReturn: number | null
  pnlUnavailable: boolean
}

function mean(xs: number[]): number | null {
  if (!xs.length) return null
  return xs.reduce((a, b) => a + b, 0) / xs.length
}

function median(xs: number[]): number | null {
  if (!xs.length) return null
  const sorted = [...xs].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? (sorted[mid - 1]! + sorted[mid]!) / 2
    : sorted[mid]!
}

function sideTouchStats(rows: ResearchObservation[]): SideTouchStats {
  const hits = rows.filter((r) => r.touch?.status === 'hit').length
  const misses = rows.filter((r) => r.touch?.status === 'miss').length
  const unknown = rows.filter((r) => r.touch?.status === 'unknown').length
  const pending = rows.filter((r) => r.touch?.status === 'pending').length
  let missContinuation = 0
  let missReversal = 0
  let missUnchanged = 0
  for (const r of rows) {
    if (r.touch?.status !== 'miss') continue
    const h7 = r.horizons.find((h) => h.minutes === 7)
    if (!h7) continue
    if (h7.status === 'continuation') missContinuation += 1
    else if (h7.status === 'reversal') missReversal += 1
    else if (h7.status === 'unchanged') missUnchanged += 1
  }
  const dirN = missContinuation + missReversal + missUnchanged
  let ifNoTouchPrefer: SideTouchStats['ifNoTouchPrefer'] = null
  if (dirN > 0) {
    if (missContinuation > missReversal && missContinuation >= missUnchanged) ifNoTouchPrefer = 'same_side'
    else if (missReversal > missContinuation && missReversal >= missUnchanged) ifNoTouchPrefer = 'opposite_side'
    else if (missUnchanged > missContinuation && missUnchanged > missReversal) ifNoTouchPrefer = 'flat'
    else if (missContinuation === missReversal && missContinuation > missUnchanged) ifNoTouchPrefer = 'unknown'
    else ifNoTouchPrefer = 'unknown'
  } else if (misses > 0) {
    ifNoTouchPrefer = 'unknown'
  }
  return {
    hits,
    misses,
    unknown,
    pending,
    winRate: hits + misses > 0 ? hits / (hits + misses) : null,
    missContinuation,
    missReversal,
    missUnchanged,
    ifNoTouchPrefer,
  }
}

function pickBest(
  strategies: StrategySummary[],
  rateOf: (s: StrategySummary) => { winRate: number | null; hits: number; misses: number },
): BestPick | null {
  let best: BestPick | null = null
  for (const s of strategies) {
    const { winRate, hits, misses } = rateOf(s)
    const known = hits + misses
    if (winRate == null || known <= 0) continue
    const cand: BestPick = { id: s.id, name: s.name, winRate, hits, misses, known }
    if (
      !best
      || cand.winRate > best.winRate
      || (cand.winRate === best.winRate && cand.hits > best.hits)
      || (cand.winRate === best.winRate && cand.hits === best.hits && cand.known > best.known)
    ) {
      best = cand
    }
  }
  return best
}

function horizonSummary(rows: ResearchObservation[], minutes: HorizonMin): StrategyHorizonSummary {
  let pending = 0, unknown = 0, unchanged = 0, continuation = 0, reversal = 0
  const signed: number[] = []
  const signedAtr: number[] = []
  for (const o of rows) {
    const h = o.horizons.find((x) => x.minutes === minutes)
    if (!h) continue
    if (h.status === 'pending') pending += 1
    else if (h.status === 'unknown') unknown += 1
    else if (h.status === 'unchanged') unchanged += 1
    else if (h.status === 'continuation') continuation += 1
    else if (h.status === 'reversal') reversal += 1
    if (h.status === 'continuation' || h.status === 'reversal' || h.status === 'unchanged') {
      if (h.signedMove != null) signed.push(h.signedMove)
      if (h.signedMoveInAtr != null) signedAtr.push(h.signedMoveInAtr)
    }
  }
  const dirN = continuation + reversal
  const contRate = dirN ? continuation / dirN : null
  return {
    minutes,
    total: rows.length,
    pending,
    unknown,
    unchanged,
    continuation,
    reversal,
    contRate,
    avgSignedMove: mean(signed),
    avgSignedMoveInAtr: mean(signedAtr),
    // On each non-flat endpoint exactly one of RISE/FALL succeeds.
    // Use the same denominator as contRate; ties are separately displayed.
    randomContRate: dirN ? 0.5 : null,
    descriptiveWilson: dirN ? wilsonInterval(continuation, dirN) : null,
    insufficient: dirN < INSUFFICIENT,
  }
}

export function summarizeExperiment(state: ExperimentState): {
  generatedAt: number
  codeVersion: string
  legacy: boolean
  primaryOutcome: 'touch_within_contract_window'
  touchWindowMinutes: number
  stage: ExperimentState['stage']
  mode: ExperimentState['mode']
  startedAt: number | null
  endsAt: number | null
  finishedAt: number | null
  feedHealthy: boolean
  lastTickEpoch: number | null
  observationCount: number
  rejectedCount: number
  strategies: StrategySummary[]
  /** Descriptive Touch leaders — not live trading advice. */
  bestOverall: BestPick | null
  bestRise: BestPick | null
  bestFall: BestPick | null
  c5Context: C5CohortSummaryBundle
  priorDiscoveryC5Summary: C5CohortSummaryBundle | null
  c5ObservationCount: number
  notes: string[]
} {
  const byId = new Map<StrategyId, ResearchObservation[]>()
  for (const o of state.observations) {
    const list = byId.get(o.strategyId) ?? []
    list.push(o)
    byId.set(o.strategyId, list)
  }

  const runSpanSec = (() => {
    if (state.startedAt == null) return null
    const end = state.finishedAt ?? state.lastTickEpoch ?? Math.floor(Date.now() / 1000)
    const span = end - state.startedAt
    return span > 0 ? span : null
  })()

  const strategies: StrategySummary[] = STRATEGY_REGISTRY
    .filter((d) => state.enabledStrategyIds.includes(d.id))
    .map((d) => {
      const rows = byId.get(d.id) ?? []
      const touchHits = rows.filter((r) => r.touch?.status === 'hit').length
      const touchMisses = rows.filter((r) => r.touch?.status === 'miss').length
      const touchUnknown = rows.filter((r) => r.touch?.status === 'unknown').length
      const rise = sideTouchStats(rows.filter((r) => r.side === 'RISE'))
      const fall = sideTouchStats(rows.filter((r) => r.side === 'FALL'))
      const quoted = rows.filter((r) => r.touch?.quoteAvailable)
      const hitTimes = rows
        .filter((r) => r.touch?.status === 'hit' && r.touch.firstTouchEpoch != null)
        .map((r) => r.touch!.firstTouchEpoch! - r.signalEpoch)
        .filter((sec) => Number.isFinite(sec) && sec >= 0)
      const settled = state.codeVersion === RESEARCH_CODE_VERSION ? quoted.filter((r) =>
        (r.touch?.quotedPath?.status === 'hit' || r.touch?.quotedPath?.status === 'miss')
        && Number.isFinite(r.touch.paperPnl) && r.touch.proposalAsk != null && r.touch.proposalAsk > 0) : []
      const cost = settled.reduce((s, r) => s + r.touch!.proposalAsk!, 0)
      const pnlVals = settled.map((r) => r.touch!.paperPnl!)
      const pnl = pnlVals.length ? pnlVals.reduce((a, b) => a + b, 0) : null
      const unknownPriced = state.codeVersion === RESEARCH_CODE_VERSION ? quoted.filter((r) =>
        r.touch?.quotedPath?.status === 'unknown'
        && r.touch.proposalAsk != null && Number.isFinite(r.touch.proposalAsk) && r.touch.proposalAsk > 0
        && r.touch.proposalPayout != null && Number.isFinite(r.touch.proposalPayout)
        && r.touch.proposalPayout >= r.touch.proposalAsk) : []
      const unknownCost = unknownPriced.reduce((s, r) => s + r.touch!.proposalAsk!, 0)
      const unknownProfit = unknownPriced.reduce((s, r) => s + r.touch!.proposalPayout! - r.touch!.proposalAsk!, 0)
      const hasMatured = settled.length + unknownPriced.length > 0
      return {
        id: d.id,
        name: d.name,
        family: d.family,
        rules: d.rules,
        candidatesAccepted: rows.length,
        pending: rows.filter((r) => r.touch?.status === 'pending').length,
        known: touchHits + touchMisses,
        horizons: HORIZON_MINUTES.map((m) => horizonSummary(rows, m)),
        touchHits,
        touchMisses,
        touchUnknown,
        touchPending: rows.filter((r) => r.touch?.status === 'pending').length,
        touchWinRate: touchHits + touchMisses > 0 ? touchHits / (touchHits + touchMisses) : null,
        rise,
        fall,
        quoteCoverageRate: rows.length ? quoted.length / rows.length : null,
        signalsPerDay: runSpanSec != null && rows.length
          ? rows.length / (runSpanSec / 86400)
          : null,
        medianTimeToTouchSec: median(hitTimes),
        quotesAvailable: quoted.length,
        quotedSettled: settled.length,
        quotedHits: settled.filter((r) => r.touch?.quotedPath?.status === 'hit').length,
        quotedMisses: settled.filter((r) => r.touch?.quotedPath?.status === 'miss').length,
        quotedUnknown: quoted.filter((r) => r.touch?.quotedPath?.status === 'unknown').length,
        quotedPending: quoted.filter((r) => !r.touch?.quotedPath || r.touch.quotedPath.status === 'pending').length,
        quotedWinRate: settled.length ? settled.filter((r) => r.touch?.quotedPath?.status === 'hit').length / settled.length : null,
        maturedQuotedCost: hasMatured ? cost + unknownCost : null,
        paperPnlLower: hasMatured ? (pnl ?? 0) - unknownCost : null,
        paperPnlUpper: hasMatured ? (pnl ?? 0) + unknownProfit : null,
        paperCost: settled.length ? cost : null,
        paperPnl: pnl,
        paperReturn: pnl != null && cost > 0 ? pnl / cost : null,
        pnlUnavailable: settled.length === 0,
      }
    })

  const bestOverall = pickBest(strategies, (s) => ({
    winRate: s.touchWinRate, hits: s.touchHits, misses: s.touchMisses,
  }))
  const bestRise = pickBest(strategies, (s) => ({
    winRate: s.rise.winRate, hits: s.rise.hits, misses: s.rise.misses,
  }))
  const bestFall = pickBest(strategies, (s) => ({
    winRate: s.fall.winRate, hits: s.fall.hits, misses: s.fall.misses,
  }))

  const c5Rows = state.c5ContextObservations ?? []
  const c5Context = summarizeC5CohortBundle(c5Rows)

  return {
    generatedAt: Math.floor(Date.now() / 1000),
    codeVersion: state.codeVersion,
    legacy: state.codeVersion !== RESEARCH_CODE_VERSION,
    primaryOutcome: 'touch_within_contract_window',
    touchWindowMinutes: state.contract.duration,
    stage: state.stage,
    mode: state.mode,
    startedAt: state.startedAt,
    endsAt: state.endsAt,
    finishedAt: state.finishedAt,
    feedHealthy: state.feedHealthy,
    lastTickEpoch: state.lastTickEpoch,
    observationCount: state.observations.length,
    rejectedCount: state.rejected.length,
    strategies,
    bestOverall,
    bestRise,
    bestFall,
    c5Context,
    priorDiscoveryC5Summary: state.priorDiscoveryC5Summary ?? null,
    c5ObservationCount: c5Rows.length,
    notes: [
      ...(state.codeVersion !== RESEARCH_CODE_VERSION ? ['Legacy / older research engine run: preserved read-only. Start a new discovery with the current 8 signal strategies.'] : []),
      'Paper comparison of 8 methods: Supertrend flip, UT solo, and combos with EMA50 / candle wicks.',
      'R1 uses Supertrend ATR7×2 flip signals as entries. R2 uses UT Bot Alerts (key2/ATR10) alone.',
      'EMA filter = close vs rising/falling EMA50. Wick = pin-bar rejection (wick ≥2× body).',
      'Rankings are descriptive/exploratory; observations can overlap and correlate.',
      'Best overall / best RISE / best FALL are ranked by Touch win% (hits / hits+misses). Misses lower the rate.',
      'If no touch: for that strategy+side, minute-7 endpoint says whether price more often continued (same side) or reversed (opposite side). Diagnostic only — not a Touch win.',
      'Do not label a small sample or a 100% observed Touch win rate as a proven best strategy.',
      'Never display an empty Touch sample as 100%.',
      'Research entry/post-win intervals are separate from the live Trade cooldown and freeze at experiment start.',
      'After a Touch HIT, only that strategy takes the research post-win break; sibling strategies keep collecting.',
      `Primary result: Touch within ${state.contract.duration} minutes of entry. A hit wins immediately when observed, including at expiry. It remains a win if price reverses or the feed disconnects later.`,
      'A miss requires no touch during a fully observed contract window. Missing coverage without an observed hit is UNKNOWN, not a loss.',
      'Endpoint direction is an optional diagnostic. Its pending/unknown status never delays or cancels a confirmed Touch win.',
      'Touch paper P&L is quote-based only when proposals were available; otherwise unavailable.',
      'Reference Touch counts start at the signal tick. Quoted Touch counts start at a fresh tick after quote receipt; earlier touches never earn quoted P&L.',
      'P&L and purchase cost include the SAME settled quoted cohort only; pending, unknown and unquoted outcomes are excluded and counted separately.',
      'Unknown outcomes can bias settled-only returns. The matured quoted P&L range treats every unknown as a loss at the lower bound and a win at the upper bound. It is a sensitivity range, not a confidence interval; pending and unquoted signals are excluded.',
      'Touch win rates use hits / (hits + misses). Unknown and pending Touch outcomes are displayed separately and excluded from that rate.',
      'The 50/50 reference applies only to endpoint direction, not to the probability of touching a barrier.',
      'The <20 marker only flags a very small settled sample; removing it does not establish statistical significance.',
      'Never auto-enables a top strategy in production.',
      'Prior filter-comparison reports that passed filterConfig into runTouchBacktest did not apply filters.',
      'C5 context study: prospective confirmed C5 with frozen M5/M15 closed context. Cohort Touch rates are observed paper results only — not win probabilities.',
      'C5 cohorts: all confirmed C5; M5 Supports; M15 Supports; both Support. Excluded rows = observations where that timeframe did not Support (Opposes/Mixed/null).',
      'Personal decision journal P/L is live/manual and separate from research paper Touch P/L.',
    ],
  }
}

const CSV_HEADER = [
  'rowType', 'id', 'strategyId', 'side', 'candleTime', 'signalEpoch', 'entryPrice', 'forming', 'reason', 'rejectionReason',
  'flagUt', 'flagSupertrend', 'flagSma', 'flagAo', 'flagRsi', 'passed', 'total',
  'close', 'sma20', 'smaRising', 'smaFalling', 'ao', 'aoPrev', 'rsi', 'utBuy', 'utSell', 'stDir',
  'frozenBarrierAbs', 'frozenDurationMin', 'frozenStake', 'frozenCurrency',
  'frozenEntryCooldownSec', 'frozenPostWinCooldownSec', 'mode',
  'c5Side', 't1Side', 't1z', 'agreement', 'agreementKind',
  'atr14', 'h1', 'h3', 'h5', 'h7', 'referenceTouchStatus', 'referenceFirstTouchEpoch', 'quotePnl', 'quoteAvailable',
  'quoteStatus', 'quoteReason', 'quoteRequestEpoch', 'quoteResponseEpoch', 'quoteEntryEpoch',
  'quoteEntryPrice', 'quoteBarrier', 'quoteTouchStatus', 'quoteFirstTouchEpoch', 'ask', 'payout', 'codeVersion',
  'h1Reason', 'h3Reason', 'h5Reason', 'h7Reason', 'h7TargetEpoch', 'h7ObservedEpoch',
  'referenceTouchReason', 'quoteTouchReason', 'quoteCoverageLost',
  'm5Trend', 'm5Agreement', 'm5Behavior', 'm15Trend', 'm15Agreement', 'm15Behavior',
  'm5ClosedContext', 'm15ClosedContext',
] as const

function csvCell(v: unknown): string {
  return `"${String(v ?? '').replaceAll('"', '""')}"`
}

/** Always emit exactly header.length columns so rejected/skip rows cannot shift fields. */
function csvRow(values: unknown[]): string {
  const width = CSV_HEADER.length
  const row = values.slice(0, width)
  while (row.length < width) row.push('')
  return row.map(csvCell).join(',')
}

function exportedT1z(metrics: Record<string, number | boolean | string | null> | undefined): unknown {
  if (!metrics) return ''
  // H1 stores t1z; T1 stores z — export both into the t1z column.
  if (metrics.t1z != null && metrics.t1z !== '') return metrics.t1z
  if (metrics.z != null && metrics.z !== '') return metrics.z
  return ''
}

export function exportObservationsCsv(state: ExperimentState): string {
  const c = state.contract
  const lines = [CSV_HEADER.join(',')]
  for (const o of state.observations) {
    const hs = Object.fromEntries(o.horizons.map((h) => [h.minutes, h.status]))
    const m = o.metrics ?? {}
    lines.push(csvRow([
      'observation', o.id, o.strategyId, o.side, o.candleTime, o.signalEpoch, o.entryReferencePrice, o.forming,
      o.reason, '',
      o.flags?.ut ?? '', o.flags?.supertrend ?? '', o.flags?.sma ?? '', o.flags?.ao ?? '', o.flags?.rsi ?? '',
      o.passed ?? '', o.total ?? '',
      m.close ?? '', m.sma20 ?? '', m.smaRising ?? '', m.smaFalling ?? '', m.ao ?? '', m.aoPrev ?? '',
      m.rsi ?? '', m.utBuy ?? '', m.utSell ?? '', m.stDir ?? '',
      c.barrierAbs, c.duration, c.paperStake, c.currency,
      c.entryCooldownSec ?? '', c.postWinCooldownSec ?? '', state.mode,
      m.c5Side ?? '', m.t1Side ?? '', exportedT1z(m), m.agreement ?? '', m.agreementKind ?? '',
      o.atr.atr14 ?? '',
      hs[1] ?? '', hs[3] ?? '', hs[5] ?? '', hs[7] ?? '',
      o.touch?.status ?? '', o.touch?.firstTouchEpoch ?? '', o.touch?.paperPnl ?? '', o.touch?.quoteAvailable ?? '',
      o.touch?.quoteStatus ?? '', o.touch?.quoteReason ?? '', o.touch?.quoteRequestEpoch ?? '',
      o.touch?.quoteResponseEpoch ?? '', o.touch?.quotedPath?.paperEntryEpoch ?? '',
      o.touch?.quotedPath?.paperEntryPrice ?? '', o.touch?.quotedPath?.absoluteBarrier ?? '',
      o.touch?.quotedPath?.status ?? '', o.touch?.quotedPath?.firstTouchEpoch ?? '',
      o.touch?.proposalAsk ?? '', o.touch?.proposalPayout ?? '', state.codeVersion,
      ...([1, 3, 5, 7] as const).map((mins) => o.horizons.find((h) => h.minutes === mins)?.reason ?? ''),
      o.horizons.find((h) => h.minutes === 7)?.targetEpoch ?? '',
      o.horizons.find((h) => h.minutes === 7)?.observedEpoch ?? '',
      o.touch?.reason ?? '', o.touch?.quotedPath?.reason ?? '', o.touch?.quotedPath?.coverageLost ?? '',
    ]))
  }
  for (const r of state.rejected) {
    const m = r.metrics ?? {}
    lines.push(csvRow([
      'rejected', `${r.strategyId}:${r.candleTime}:rejected`, r.strategyId, r.side ?? '', r.candleTime,
      r.signalEpoch, '', false, '', r.reason,
      r.flags?.ut ?? '', r.flags?.supertrend ?? '', r.flags?.sma ?? '', r.flags?.ao ?? '', r.flags?.rsi ?? '',
      r.passed ?? '', r.total ?? '',
      m.close ?? '', m.sma20 ?? '', m.smaRising ?? '', m.smaFalling ?? '', m.ao ?? '', m.aoPrev ?? '',
      m.rsi ?? '', m.utBuy ?? '', m.utSell ?? '', m.stDir ?? '',
      c.barrierAbs, c.duration, c.paperStake, c.currency,
      c.entryCooldownSec ?? '', c.postWinCooldownSec ?? '', state.mode,
      m.c5Side ?? '', m.t1Side ?? '', exportedT1z(m), m.agreement ?? '', m.agreementKind ?? '',
      '', '', '', '', '', '', '', '', '',
      '', '', '', '', '', '', '', '', '',
      '', '', state.codeVersion,
      '', '', '', '', '', '', '', '', '',
    ]))
  }
  for (const o of state.c5ContextObservations ?? []) {
    const t = o.touch
    const product = state.codeVersion === RESEARCH_CODE_VERSION ? c5StudyContract(c) : c
    const fields: Partial<Record<typeof CSV_HEADER[number], unknown>> = {
      rowType: 'c5_context', id: o.id, strategyId: 'C5', side: o.side,
      candleTime: o.candleTime, signalEpoch: o.signalEpoch, entryPrice: o.entryReferencePrice,
      forming: false, reason: o.reason, passed: o.passed, total: o.total,
      flagUt: o.confirmations.ut, flagSupertrend: o.confirmations.supertrend,
      flagSma: o.confirmations.sma, flagRsi: o.confirmations.rsi, flagAo: o.confirmations.ao,
      frozenBarrierAbs: t?.barrierAbs ?? product.barrierAbs, frozenDurationMin: t?.durationMin ?? product.duration,
      frozenStake: t?.stake ?? product.paperStake, frozenCurrency: product.currency, mode: state.mode,
      frozenEntryCooldownSec: 0, frozenPostWinCooldownSec: 0, referenceTouchStatus: t?.status,
      referenceFirstTouchEpoch: t?.firstTouchEpoch, referenceTouchReason: t?.reason,
      quotePnl: t?.paperPnl, quoteAvailable: t?.quoteAvailable, quoteStatus: t?.quoteStatus,
      quoteReason: t?.quoteReason, quoteRequestEpoch: t?.quoteRequestEpoch, quoteResponseEpoch: t?.quoteResponseEpoch,
      quoteEntryPrice: t?.quotedPath?.paperEntryPrice, quoteEntryEpoch: t?.quotedPath?.paperEntryEpoch,
      quoteBarrier: t?.quotedPath?.absoluteBarrier, quoteTouchStatus: t?.quotedPath?.status,
      quoteFirstTouchEpoch: t?.quotedPath?.firstTouchEpoch, quoteTouchReason: t?.quotedPath?.reason,
      quoteCoverageLost: t?.quotedPath?.coverageLost, ask: t?.proposalAsk, payout: t?.proposalPayout,
      codeVersion: state.codeVersion, m5Trend: o.m5.trend, m5Agreement: o.m5.agreement, m5Behavior: o.m5.behavior,
      m15Trend: o.m15.trend, m15Agreement: o.m15.agreement, m15Behavior: o.m15.behavior,
      m5ClosedContext: JSON.stringify(o.m5), m15ClosedContext: JSON.stringify(o.m15),
    }
    lines.push(csvRow(CSV_HEADER.map(k => fields[k])))
  }
  return lines.join('\n')
}

function noTouchLabel(side: SideTouchStats): string {
  if (side.ifNoTouchPrefer === 'same_side') return `same (${side.missContinuation}c/${side.missReversal}r)`
  if (side.ifNoTouchPrefer === 'opposite_side') return `opposite (${side.missContinuation}c/${side.missReversal}r)`
  if (side.ifNoTouchPrefer === 'flat') return `flat (${side.missUnchanged})`
  if (side.ifNoTouchPrefer === 'unknown') return 'unknown'
  return '—'
}

export function exportReportMarkdown(summary: ReturnType<typeof summarizeExperiment>): string {
  const lines = [
    '# Research experiment report',
    '',
    `Generated: ${new Date(summary.generatedAt * 1000).toISOString()}`,
    `Stage: ${summary.stage} · Mode: ${summary.mode}`,
    `Observations: ${summary.observationCount} · Rejected: ${summary.rejectedCount}`,
    `Primary result: target touched within ${summary.touchWindowMinutes} minutes. A hit wins immediately; no wait for the endpoint.`,
    '',
    '## Strategies (descriptive)',
    '',
    `Code version: ${summary.codeVersion}${summary.legacy ? ' (legacy, read-only)' : ''}`,
    '',
    'Eight paper methods: R1 Supertrend flip · R2 UT solo · R3 UT+EMA · R4 ST+EMA · R5 ST+wick · R6 UT+wick · R7 ST+wick+EMA · R8 UT+wick+EMA.',
    '',
    '## Best Touch picks (descriptive)',
    '',
    summary.bestOverall
      ? `Overall: ${summary.bestOverall.id} ${summary.bestOverall.name} — ${(100 * summary.bestOverall.winRate).toFixed(1)}% (${summary.bestOverall.hits}H/${summary.bestOverall.misses}M)`
      : 'Overall: —',
    summary.bestRise
      ? `Best RISE: ${summary.bestRise.id} — ${(100 * summary.bestRise.winRate).toFixed(1)}% (${summary.bestRise.hits}H/${summary.bestRise.misses}M)`
      : 'Best RISE: —',
    summary.bestFall
      ? `Best FALL: ${summary.bestFall.id} — ${(100 * summary.bestFall.winRate).toFixed(1)}% (${summary.bestFall.hits}H/${summary.bestFall.misses}M)`
      : 'Best FALL: —',
    '',
    '| ID | Name | n | Signal Touch win% | Signal H/M/U/P | RISE win% | RISE H/M | If no touch (RISE) | FALL win% | FALL H/M | If no touch (FALL) | Quote cov% | Sig/day | Median TTT s | Quotes/n | Quoted Touch win% | Quoted H/M/U/P | Settled cost | Settled P&L | Settled return | Matured quoted P&L range |',
    '|----|------|---|-------------------|----------------|-----------|----------|-------------------|-----------|----------|--------------------|------------|---------|--------------|----------|-------------------|----------------|--------------|-------------|----------------|--------------------------|',
  ]
  for (const s of summary.strategies) {
    const touchRate = s.touchWinRate == null ? '—' : `${(100 * s.touchWinRate).toFixed(1)}%`
    const riseRate = s.rise.winRate == null ? '—' : `${(100 * s.rise.winRate).toFixed(1)}%`
    const fallRate = s.fall.winRate == null ? '—' : `${(100 * s.fall.winRate).toFixed(1)}%`
    const quotedRate = s.quotedWinRate == null ? '—' : `${(100 * s.quotedWinRate).toFixed(1)}%`
    const pnl = s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')
    const cov = s.quoteCoverageRate == null ? '—' : `${(100 * s.quoteCoverageRate).toFixed(0)}%`
    const spd = s.signalsPerDay == null ? '—' : s.signalsPerDay.toFixed(2)
    const ttt = s.medianTimeToTouchSec == null ? '—' : s.medianTimeToTouchSec.toFixed(0)
    lines.push(
      `| ${s.id} | ${s.name} | ${s.candidatesAccepted} | ${touchRate} | ${s.touchHits}/${s.touchMisses}/${s.touchUnknown}/${s.touchPending} | ${riseRate} | ${s.rise.hits}/${s.rise.misses} | ${noTouchLabel(s.rise)} | ${fallRate} | ${s.fall.hits}/${s.fall.misses} | ${noTouchLabel(s.fall)} | ${cov} | ${spd} | ${ttt} | ${s.quotesAvailable}/${s.candidatesAccepted} | ${quotedRate} | ${s.quotedHits}/${s.quotedMisses}/${s.quotedUnknown}/${s.quotedPending} | ${s.paperCost?.toFixed(4) ?? '—'} | ${pnl} | ${s.paperReturn == null ? '—' : (s.paperReturn * 100).toFixed(2) + '%'} | ${s.paperPnlLower?.toFixed(4) ?? '—'} to ${s.paperPnlUpper?.toFixed(4) ?? '—'} |`,
    )
  }
  lines.push('', '## Optional endpoint diagnostics', '',
    'These measure direction at minute 7, not whether the target was touched earlier. They never change a Touch result.', '',
    '| ID | 7m continuation | Reversal | Unchanged | Unknown | Pending |',
    '|----|-----------------|----------|-----------|---------|---------|')
  for (const s of summary.strategies) {
    const h7 = s.horizons.find((h) => h.minutes === 7)
    lines.push(`| ${s.id} | ${h7?.continuation ?? 0} | ${h7?.reversal ?? 0} | ${h7?.unchanged ?? 0} | ${h7?.unknown ?? 0} | ${h7?.pending ?? 0} |`)
  }
  lines.push('', '## Notes', '')
  for (const n of summary.notes) lines.push(`- ${n}`)
  return lines.join('\n')
}
