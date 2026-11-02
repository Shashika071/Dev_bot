import { wilsonInterval } from './statistics'
import { STRATEGY_REGISTRY } from './strategies/registry'
import type { ExperimentState } from './experimentEngine'
import { RESEARCH_CODE_VERSION } from './experimentEngine'
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
  quotesAvailable: number
  quotedSettled: number
  quotedHits: number
  quotedMisses: number
  quotedUnknown: number
  quotedPending: number
  paperCost: number | null
  paperPnl: number | null
  paperReturn: number | null
  pnlUnavailable: boolean
}

function mean(xs: number[]): number | null {
  if (!xs.length) return null
  return xs.reduce((a, b) => a + b, 0) / xs.length
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
  notes: string[]
} {
  const byId = new Map<StrategyId, ResearchObservation[]>()
  for (const o of state.observations) {
    const list = byId.get(o.strategyId) ?? []
    list.push(o)
    byId.set(o.strategyId, list)
  }

  const strategies: StrategySummary[] = STRATEGY_REGISTRY
    .filter((d) => state.enabledStrategyIds.includes(d.id))
    .map((d) => {
      const rows = byId.get(d.id) ?? []
      const touchHits = rows.filter((r) => r.touch?.status === 'hit').length
      const touchMisses = rows.filter((r) => r.touch?.status === 'miss').length
      const touchUnknown = rows.filter((r) => r.touch?.status === 'unknown').length
      const quoted = rows.filter((r) => r.touch?.quoteAvailable)
      const settled = state.codeVersion === RESEARCH_CODE_VERSION ? quoted.filter((r) =>
        (r.touch?.quotedPath?.status === 'hit' || r.touch?.quotedPath?.status === 'miss')
        && Number.isFinite(r.touch.paperPnl) && r.touch.proposalAsk != null && r.touch.proposalAsk > 0) : []
      const cost = settled.reduce((s, r) => s + r.touch!.proposalAsk!, 0)
      const pnlVals = settled.map((r) => r.touch!.paperPnl!)
      const pnl = pnlVals.length ? pnlVals.reduce((a, b) => a + b, 0) : null
      return {
        id: d.id,
        name: d.name,
        family: d.family,
        rules: d.rules,
        candidatesAccepted: rows.length,
        pending: rows.filter((r) => r.pendingHorizons).length,
        known: rows.filter((r) => !r.pendingHorizons).length,
        horizons: HORIZON_MINUTES.map((m) => horizonSummary(rows, m)),
        touchHits,
        touchMisses,
        touchUnknown,
        touchPending: rows.filter((r) => r.touch?.status === 'pending').length,
        quotesAvailable: quoted.length,
        quotedSettled: settled.length,
        quotedHits: settled.filter((r) => r.touch?.quotedPath?.status === 'hit').length,
        quotedMisses: settled.filter((r) => r.touch?.quotedPath?.status === 'miss').length,
        quotedUnknown: quoted.filter((r) => r.touch?.quotedPath?.status === 'unknown').length,
        quotedPending: quoted.filter((r) => !r.touch?.quotedPath || r.touch.quotedPath.status === 'pending').length,
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
    notes: [
      ...(state.codeVersion !== RESEARCH_CODE_VERSION ? ['PRE-FIX RUN: preserved read-only. Paired entries, quote coverage and expiry were affected by known bugs; legacy P&L is not comparable. Start a new v2 run.'] : []),
      'Rankings are descriptive/exploratory; observations can overlap and correlate.',
      '80% baseline score means 4/5 conditions — not an 80% win probability.',
      'B0 uses forming-candle timing; compare completed strategies primarily to B1.',
      '7-minute horizon is the predeclared primary direction horizon.',
      'Touch paper P&L is quote-based only when proposals were available; otherwise unavailable.',
      'Reference Touch counts start at the signal tick. Quoted Touch counts start at a fresh tick after quote receipt; earlier touches never earn quoted P&L.',
      'P&L and purchase cost include the SAME settled quoted cohort only; pending, unknown and unquoted outcomes are excluded and counted separately.',
      'The random reference is the exact equally weighted RISE/FALL expectation. Directional percentages exclude unchanged, pending and unknown endpoints.',
      'The <20 marker only flags a very small sample; removing it does not establish statistical significance.',
      'Never auto-enables a top strategy in production.',
      'Prior filter-comparison reports that passed filterConfig into runTouchBacktest did not apply filters.',
    ],
  }
}

export function exportObservationsCsv(state: ExperimentState): string {
  const header = [
    'id', 'strategyId', 'side', 'candleTime', 'signalEpoch', 'entryPrice', 'forming', 'reason',
    'atr14', 'h1', 'h3', 'h5', 'h7', 'referenceTouchStatus', 'quotePnl', 'quoteAvailable',
    'quoteStatus', 'quoteReason', 'quoteRequestEpoch', 'quoteResponseEpoch', 'quoteEntryEpoch',
    'quoteEntryPrice', 'quoteBarrier', 'quoteTouchStatus', 'quoteFirstTouchEpoch', 'ask', 'payout', 'codeVersion',
  ]
  const lines = [header.join(',')]
  for (const o of state.observations) {
    const hs = Object.fromEntries(o.horizons.map((h) => [h.minutes, h.status]))
    lines.push([
      o.id, o.strategyId, o.side, o.candleTime, o.signalEpoch, o.entryReferencePrice, o.forming,
      o.reason,
      o.atr.atr14 ?? '',
      hs[1] ?? '', hs[3] ?? '', hs[5] ?? '', hs[7] ?? '',
      o.touch?.status ?? '', o.touch?.paperPnl ?? '', o.touch?.quoteAvailable ?? '',
      o.touch?.quoteStatus ?? '', o.touch?.quoteReason ?? '', o.touch?.quoteRequestEpoch ?? '',
      o.touch?.quoteResponseEpoch ?? '', o.touch?.quotedPath?.paperEntryEpoch ?? '',
      o.touch?.quotedPath?.paperEntryPrice ?? '', o.touch?.quotedPath?.absoluteBarrier ?? '',
      o.touch?.quotedPath?.status ?? '', o.touch?.quotedPath?.firstTouchEpoch ?? '',
      o.touch?.proposalAsk ?? '', o.touch?.proposalPayout ?? '', state.codeVersion,
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
    '',
    '## Strategies (descriptive)',
    '',
    `Code version: ${summary.codeVersion}${summary.legacy ? ' (pre-fix, read-only)' : ''}`,
    '',
    '| ID | Name | n | 7m cont% | Reference H/M/U/P | Quotes/n | Quoted H/M/U/P | Settled cost | Quote P&L | Return |',
    '|----|------|---|----------|-------------------|----------|----------------|--------------|-----------|--------|',
  ]
  for (const s of summary.strategies) {
    const h7 = s.horizons.find((h) => h.minutes === 7)
    const cont = h7?.contRate == null ? '—' : `${(100 * h7.contRate).toFixed(1)}%`
    const pnl = s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')
    lines.push(
      `| ${s.id} | ${s.name} | ${s.candidatesAccepted} | ${cont} | ${s.touchHits}/${s.touchMisses}/${s.touchUnknown}/${s.touchPending} | ${s.quotesAvailable}/${s.candidatesAccepted} | ${s.quotedHits}/${s.quotedMisses}/${s.quotedUnknown}/${s.quotedPending} | ${s.paperCost?.toFixed(4) ?? '—'} | ${pnl} | ${s.paperReturn == null ? '—' : (s.paperReturn * 100).toFixed(2) + '%'} |`,
    )
  }
  lines.push('', '## Notes', '')
  for (const n of summary.notes) lines.push(`- ${n}`)
  return lines.join('\n')
}
