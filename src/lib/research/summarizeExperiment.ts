import { wilsonInterval } from './statistics'
import { STRATEGY_REGISTRY } from './strategies/registry'
import type { ExperimentState } from './experimentEngine'
import { RESEARCH_CODE_VERSION } from './experimentEngine'
import { isFocusedConfirmation } from './focusedConfirmation'
import type { HorizonMin, ResearchObservation, StrategyId } from './strategies/types'
import { HORIZON_MINUTES } from './horizons'

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
  focusedConfirmation: boolean
  strategies: StrategySummary[]
  notes: string[]
} {
  const byId = new Map<StrategyId, ResearchObservation[]>()
  for (const o of state.observations) {
    const list = byId.get(o.strategyId) ?? []
    list.push(o)
    byId.set(o.strategyId, list)
  }

  const focused = isFocusedConfirmation(state.enabledStrategyIds)
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
    focusedConfirmation: focused,
    strategies,
    notes: [
      ...(state.codeVersion !== RESEARCH_CODE_VERSION ? ['PRE-FIX RUN: preserved read-only. Paired entries, quote coverage and expiry were affected by known bugs; legacy P&L is not comparable. Start a new v2 run.'] : []),
      ...(focused ? [
        'Focused confirmation: B1 = baseline (≥4/5 closed), C5 = main candidate (AO optional), C1 = full confirmation (AO required).',
        'Compare C5 to B1 for lift; compare C1 to C5 to see whether requiring AO improves the candidate.',
      ] : []),
      'Rankings are descriptive/exploratory; observations can overlap and correlate.',
      'Do not label a small sample or a 100% observed Touch win rate as a proven best strategy.',
      '80% baseline score means 4/5 conditions — not an 80% win probability.',
      'B0 uses forming-candle timing; compare completed strategies primarily to B1.',
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
    ],
  }
}

export function exportObservationsCsv(state: ExperimentState): string {
  const c = state.contract
  const header = [
    'id', 'strategyId', 'side', 'candleTime', 'signalEpoch', 'entryPrice', 'forming', 'reason',
    'flagUt', 'flagSupertrend', 'flagSma', 'flagAo', 'flagRsi', 'passed', 'total',
    'close', 'sma20', 'smaRising', 'smaFalling', 'ao', 'aoPrev', 'rsi', 'utBuy', 'utSell', 'stDir',
    'frozenBarrierAbs', 'frozenDurationMin', 'frozenStake', 'frozenCurrency', 'mode',
    'atr14', 'h1', 'h3', 'h5', 'h7', 'referenceTouchStatus', 'referenceFirstTouchEpoch', 'quotePnl', 'quoteAvailable',
    'quoteStatus', 'quoteReason', 'quoteRequestEpoch', 'quoteResponseEpoch', 'quoteEntryEpoch',
    'quoteEntryPrice', 'quoteBarrier', 'quoteTouchStatus', 'quoteFirstTouchEpoch', 'ask', 'payout', 'codeVersion',
    'h1Reason', 'h3Reason', 'h5Reason', 'h7Reason', 'h7TargetEpoch', 'h7ObservedEpoch',
    'referenceTouchReason', 'quoteTouchReason', 'quoteCoverageLost',
  ]
  const lines = [header.join(',')]
  for (const o of state.observations) {
    const hs = Object.fromEntries(o.horizons.map((h) => [h.minutes, h.status]))
    const m = o.metrics ?? {}
    lines.push([
      o.id, o.strategyId, o.side, o.candleTime, o.signalEpoch, o.entryReferencePrice, o.forming,
      o.reason,
      o.flags?.ut ?? '', o.flags?.supertrend ?? '', o.flags?.sma ?? '', o.flags?.ao ?? '', o.flags?.rsi ?? '',
      o.passed ?? '', o.total ?? '',
      m.close ?? '', m.sma20 ?? '', m.smaRising ?? '', m.smaFalling ?? '', m.ao ?? '', m.aoPrev ?? '',
      m.rsi ?? '', m.utBuy ?? '', m.utSell ?? '', m.stDir ?? '',
      c.barrierAbs, c.duration, c.paperStake, c.currency, state.mode,
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
    ].map((v) => `"${String(v).replaceAll('"', '""')}"`).join(','))
  }
  return lines.join('\n')
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
    `Code version: ${summary.codeVersion}${summary.legacy ? ' (pre-fix, read-only)' : ''}`,
    '',
    summary.focusedConfirmation
      ? 'Focused paper comparison: B1 baseline · C5 main candidate (AO optional) · C1 full confirmation (AO required).'
      : '',
    '',
    '| ID | Name | n | Signal Touch win% | Signal H/M/U/P | Quote cov% | Sig/day | Median TTT s | Quotes/n | Quoted Touch win% | Quoted H/M/U/P | Settled cost | Settled P&L | Settled return | Matured quoted P&L range |',
    '|----|------|---|-------------------|----------------|------------|---------|--------------|----------|-------------------|----------------|--------------|-------------|----------------|--------------------------|',
  ]
  for (const s of summary.strategies) {
    const touchRate = s.touchWinRate == null ? '—' : `${(100 * s.touchWinRate).toFixed(1)}%`
    const quotedRate = s.quotedWinRate == null ? '—' : `${(100 * s.quotedWinRate).toFixed(1)}%`
    const pnl = s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')
    const cov = s.quoteCoverageRate == null ? '—' : `${(100 * s.quoteCoverageRate).toFixed(0)}%`
    const spd = s.signalsPerDay == null ? '—' : s.signalsPerDay.toFixed(2)
    const ttt = s.medianTimeToTouchSec == null ? '—' : s.medianTimeToTouchSec.toFixed(0)
    lines.push(
      `| ${s.id} | ${s.name} | ${s.candidatesAccepted} | ${touchRate} | ${s.touchHits}/${s.touchMisses}/${s.touchUnknown}/${s.touchPending} | ${cov} | ${spd} | ${ttt} | ${s.quotesAvailable}/${s.candidatesAccepted} | ${quotedRate} | ${s.quotedHits}/${s.quotedMisses}/${s.quotedUnknown}/${s.quotedPending} | ${s.paperCost?.toFixed(4) ?? '—'} | ${pnl} | ${s.paperReturn == null ? '—' : (s.paperReturn * 100).toFixed(2) + '%'} | ${s.paperPnlLower?.toFixed(4) ?? '—'} to ${s.paperPnlUpper?.toFixed(4) ?? '—'} |`,
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
