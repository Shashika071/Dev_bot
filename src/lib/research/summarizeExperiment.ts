import { wilsonInterval } from './statistics'
import { STRATEGY_REGISTRY } from './strategies/registry'
import type { ExperimentState } from './experimentEngine'
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
  quotesAvailable: number
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
  let randCont = 0, randRev = 0
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
    if (h.randomClass === 'continuation') randCont += 1
    if (h.randomClass === 'reversal') randRev += 1
  }
  const dirN = continuation + reversal
  const contRate = dirN ? continuation / dirN : null
  const randN = randCont + randRev
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
    randomContRate: randN ? randCont / randN : null,
    descriptiveWilson: dirN ? wilsonInterval(continuation, dirN) : null,
    insufficient: dirN < INSUFFICIENT,
  }
}

export function summarizeExperiment(state: ExperimentState): {
  generatedAt: number
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
      const cost = quoted.reduce((s, r) => s + (r.touch?.proposalAsk ?? 0), 0)
      const pnlVals = quoted.map((r) => r.touch?.paperPnl).filter((x): x is number => x != null)
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
        quotesAvailable: quoted.length,
        paperCost: quoted.length ? cost : null,
        paperPnl: pnl,
        paperReturn: pnl != null && cost > 0 ? pnl / cost : null,
        pnlUnavailable: quoted.length === 0,
      }
    })

  return {
    generatedAt: Math.floor(Date.now() / 1000),
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
      'Rankings are descriptive/exploratory; observations can overlap and correlate.',
      '80% baseline score means 4/5 conditions — not an 80% win probability.',
      'B0 uses forming-candle timing; compare completed strategies primarily to B1.',
      '7-minute horizon is the predeclared primary direction horizon.',
      'Touch paper P&L is quote-based only when proposals were available; otherwise unavailable.',
      'Never auto-enables a top strategy in production.',
      'Prior filter-comparison reports that passed filterConfig into runTouchBacktest did not apply filters.',
    ],
  }
}

export function exportObservationsCsv(state: ExperimentState): string {
  const header = [
    'id', 'strategyId', 'side', 'candleTime', 'signalEpoch', 'entryPrice', 'forming', 'reason',
    'atr14', 'h1', 'h3', 'h5', 'h7', 'touchStatus', 'touchPnl', 'quoteAvailable',
  ]
  const lines = [header.join(',')]
  for (const o of state.observations) {
    const hs = Object.fromEntries(o.horizons.map((h) => [h.minutes, h.status]))
    lines.push([
      o.id, o.strategyId, o.side, o.candleTime, o.signalEpoch, o.entryReferencePrice, o.forming,
      JSON.stringify(o.reason),
      o.atr.atr14 ?? '',
      hs[1] ?? '', hs[3] ?? '', hs[5] ?? '', hs[7] ?? '',
      o.touch?.status ?? '', o.touch?.paperPnl ?? '', o.touch?.quoteAvailable ?? '',
    ].join(','))
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
    '| ID | Name | n | 7m cont% | Touch hit/miss/unk | Quote P&L |',
    '|----|------|---|----------|--------------------|-----------|',
  ]
  for (const s of summary.strategies) {
    const h7 = s.horizons.find((h) => h.minutes === 7)
    const cont = h7?.contRate == null ? '—' : `${(100 * h7.contRate).toFixed(1)}%`
    const pnl = s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')
    lines.push(
      `| ${s.id} | ${s.name} | ${s.candidatesAccepted} | ${cont} | ${s.touchHits}/${s.touchMisses}/${s.touchUnknown} | ${pnl} |`,
    )
  }
  lines.push('', '## Notes', '')
  for (const n of summary.notes) lines.push(`- ${n}`)
  return lines.join('\n')
}
