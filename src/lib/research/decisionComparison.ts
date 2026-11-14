import type { DecisionJournalState } from '../journal/decisionJournal'
import type { C5ContextObservation } from './c5ContextStudy'
import type { TouchPathState } from './strategies/types'

/** Public-market facts needed to compare one browser's private decisions locally. */
export type C5Baseline = {
  id: string
  quoted: TouchPathState['status'] | 'unquoted'
  paperPnl: number | null
  resolvedAt: number | null
}

export function c5Baseline(row: C5ContextObservation): C5Baseline {
  const touch = row.touch
  const path = touch?.quotedPath
  const quoted = path?.status ?? (touch?.quoteAvailable || touch?.quoteStatus === 'waiting' ? 'pending' : 'unquoted')
  const expiry = path?.paperEntryEpoch != null && touch ? path.paperEntryEpoch + 60 * touch.durationMin : null
  return {
    id: row.id, quoted,
    paperPnl: (quoted === 'hit' || quoted === 'miss') && Number.isFinite(touch?.paperPnl)
      ? touch!.paperPnl! : null,
    resolvedAt: quoted === 'hit' ? path?.firstTouchEpoch ?? null : quoted === 'miss' ? expiry : null,
  }
}

type DecisionGroup = {
  reason: string; taken: boolean; setups: number; hits: number; misses: number
  pending: number; unknown: number; late: number
  paperGains: number; paperLosses: number; settledPaper: number
  brokerTrades: number; brokerByAccount: Record<string, number>
}

export function compareC5Decisions(
  baselines: C5Baseline[], journal: DecisionJournalState | undefined,
) {
  const decisions = new Map(journal?.setups.filter(s => !s.legacy).map(s => [s.id, s]))
  const groups = new Map<string, DecisionGroup>()
  let unanswered = 0
  for (const baseline of baselines) {
    const setup = decisions.get(baseline.id)
    const buy = setup?.milestones.find(m => m.kind === 'buy' && m.execution?.contractId != null)
    const skip = setup?.milestones.find(m => m.kind === 'skip')
    if (!setup || (!buy && !skip)) { unanswered++; continue }
    const click = buy && setup.milestones.find(m => m.kind === 'manual_click' && m.attemptId === buy.attemptId)
    const reason = buy ? 'Taken' : skip!.skipReason || 'Skip without a reason'
    const key = buy ? 'taken' : `skip:${reason}`
    const group = groups.get(key) ?? {
      reason, taken: Boolean(buy), setups: 0, hits: 0, misses: 0, pending: 0,
      unknown: 0, late: 0, paperGains: 0, paperLosses: 0, settledPaper: 0,
      brokerTrades: 0, brokerByAccount: {},
    }
    groups.set(key, group)
    group.setups++
    // A late skip after the baseline already resolved cannot show a loss avoided.
    const decisionTime = (click || buy || skip)!.frozenAtEpoch
    if (baseline.resolvedAt != null && decisionTime >= baseline.resolvedAt) group.late++
    else if (baseline.quoted === 'pending') group.pending++
    else if (baseline.paperPnl == null) group.unknown++
    else {
      group.settledPaper++
      if (baseline.quoted === 'hit') { group.hits++; group.paperGains += baseline.paperPnl }
      else if (baseline.quoted === 'miss') { group.misses++; group.paperLosses -= baseline.paperPnl }
    }
    for (const milestone of setup.milestones) {
      if (milestone.kind !== 'settlement' || milestone.execution?.profit == null) continue
      const account = `${milestone.accountId ?? 'Unspecified account'} · ${milestone.execution.currency ?? 'unspecified currency'}`
      group.brokerByAccount[account] = (group.brokerByAccount[account] ?? 0) + milestone.execution.profit
      group.brokerTrades++
    }
  }
  return { unanswered, groups: [...groups.values()] }
}
