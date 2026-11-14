/**
 * Immutable browser decision journal for manual C5 setups.
 * Snapshots are frozen at first write; later candles never rewrite them.
 */
import type { ConfirmationFlags } from '../types'
import type { Agreement, CandleBehavior, TfTrend, ZoneDistance } from '../timeframes/timeframeContext'

export const JOURNAL_KEY = 'v100_decision_journal_v1'
export const JOURNAL_MAX_SETUPS = 400

export type JournalMilestone =
  | 'near_3_4'
  | 'qualify_4_4_forming'
  | 'qualify_4_4_confirmed'
  | 'manual_click'
  | 'manual_rejected'
  | 'skip'
  | 'buy'
  | 'settlement'

export type SetupDisposition = 'open' | 'taken' | 'skipped' | 'unanswered'

export type TfSnapshot = {
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

export type MilestoneSnapshot = {
  kind: JournalMilestone
  /** One ID per button click; shared by its rejection/buy/settlement records. */
  attemptId?: string
  accountId?: string
  frozenAtEpoch: number
  receiptEpoch: number
  price: number | null
  m1: {
    side: 'RISE' | 'FALL'
    candleTime: number
    passed: number
    total: number
    forming: boolean
    state: 'WATCHING' | 'FORMING' | 'CONFIRMED'
    flags: ConfirmationFlags | null
    missingCondition: string | null
  }
  m5: TfSnapshot
  m15: TfSnapshot
  skipReason?: string
  quote?: {
    ask: number | null
    payout: number | null
    barrier: string | null
    breakEven: number | null
    winGain: number | null
    maxLoss: number | null
  }
  execution?: {
    contractId: number | null
    entrySpot: number | null
    barrier: string | null
    buyPrice: number | null
    payout: number | null
    profit: number | null
    status: string | null
    currency: string | null
  }
}

export type DecisionSetup = {
  id: string
  side: 'RISE' | 'FALL'
  candleTime: number
  createdAt: number
  disposition: SetupDisposition
  /** Migrated records that predate durable attempt/contract linking. */
  legacy: boolean
  milestones: MilestoneSnapshot[]
}

export type DecisionJournalState = {
  version: 2
  setups: DecisionSetup[]
  storageError: boolean
}

export function setupKey(candleTime: number, side: 'RISE' | 'FALL'): string {
  return `C5:${candleTime}:${side}`
}

/** Keep a latched forming/confirmed signal on its originating candle. */
export function journalCandleTime(
  entryState: 'WAIT' | 'FORMING' | 'CONFIRMED',
  signalCandleTime: number | null | undefined,
  liveSetupCandleTime: number | null,
): number | null {
  return entryState === 'WAIT'
    ? liveSetupCandleTime
    : signalCandleTime ?? liveSetupCandleTime
}

export function emptyJournal(): DecisionJournalState {
  return { version: 2, setups: [], storageError: false }
}

export function loadJournal(): DecisionJournalState {
  try {
    const raw = localStorage.getItem(JOURNAL_KEY)
    if (!raw) return emptyJournal()
    const parsed = JSON.parse(raw) as {
      version?: number
      setups?: Array<Omit<DecisionSetup, 'legacy'> & { legacy?: boolean }>
    }
    if (!parsed || !Array.isArray(parsed.setups)) return emptyJournal()
    if (parsed.version !== 1 && parsed.version !== 2) return emptyJournal()
    return {
      version: 2,
      setups: parsed.setups.map((setup) => ({
        ...setup,
        // V1 cannot prove which click produced a buy after a refresh.
        legacy: parsed.version === 1 ? true : setup.legacy === true,
      })),
      storageError: false,
    }
  } catch {
    return emptyJournal()
  }
}

export function saveJournal(state: DecisionJournalState): boolean {
  try {
    localStorage.setItem(
      JOURNAL_KEY,
      JSON.stringify({ version: 2, setups: state.setups.slice(0, JOURNAL_MAX_SETUPS) }),
    )
    return true
  } catch {
    return false
  }
}

function milestoneIdentity(snapshot: MilestoneSnapshot): string {
  if (snapshot.kind === 'manual_click' || snapshot.kind === 'manual_rejected') {
    return `${snapshot.kind}:${snapshot.attemptId ?? 'legacy'}`
  }
  if (snapshot.kind === 'buy' || snapshot.kind === 'settlement') {
    const contractId = snapshot.execution?.contractId
    if (contractId != null) return `${snapshot.kind}:contract:${contractId}`
    return `${snapshot.kind}:attempt:${snapshot.attemptId ?? 'legacy'}`
  }
  return snapshot.kind
}

function setupForSnapshot(
  state: DecisionJournalState,
  snapshot: MilestoneSnapshot,
): DecisionSetup | undefined {
  if (snapshot.attemptId) {
    const byAttempt = state.setups.find((setup) =>
      setup.milestones.some((milestone) => milestone.attemptId === snapshot.attemptId),
    )
    if (byAttempt) return byAttempt
  }
  const contractId = snapshot.execution?.contractId
  if (contractId != null) {
    const byContract = state.setups.find((setup) =>
      setup.milestones.some((milestone) => milestone.execution?.contractId === contractId),
    )
    if (byContract) return byContract
  }
  const id = setupKey(snapshot.m1.candleTime, snapshot.m1.side)
  return state.setups.find((setup) => setup.id === id)
}

/**
 * Append an immutable milestone. Attempt and broker contract IDs route later
 * events back to the setup that existed when the button was clicked.
 */
export function appendMilestone(
  state: DecisionJournalState,
  snapshot: MilestoneSnapshot,
): DecisionJournalState {
  const existing = setupForSnapshot(state, snapshot)
  if (existing) {
    const identity = milestoneIdentity(snapshot)
    const duplicateIndex = existing.milestones.findIndex(
      (milestone) => milestoneIdentity(milestone) === identity,
    )
    if (duplicateIndex >= 0) {
      const previous = existing.milestones[duplicateIndex]!
      // A terminal update may fill a profit omitted by an earlier terminal row.
      if (snapshot.kind !== 'settlement' || previous.execution?.profit != null) return state
      const milestones = existing.milestones.map((m) =>
        m === previous ? snapshot : m,
      )
      const setups = state.setups.map((s) => (s === existing ? { ...s, milestones } : s))
      return { ...state, setups }
    }
    let disposition = existing.disposition
    if (snapshot.kind === 'skip' && disposition !== 'taken') disposition = 'skipped'
    if (snapshot.kind === 'buy' || snapshot.kind === 'settlement') disposition = 'taken'
    const setups = state.setups.map((s) =>
      s === existing
        ? { ...s, disposition, milestones: [...s.milestones, snapshot] }
        : s,
    )
    return { ...state, setups }
  }
  const id = setupKey(snapshot.m1.candleTime, snapshot.m1.side)
  const disposition: SetupDisposition =
    snapshot.kind === 'skip' ? 'skipped'
      : snapshot.kind === 'buy' || snapshot.kind === 'settlement' ? 'taken'
        : 'open'
  const setup: DecisionSetup = {
    id,
    side: snapshot.m1.side,
    candleTime: snapshot.m1.candleTime,
    createdAt: snapshot.frozenAtEpoch,
    disposition,
    legacy: false,
    milestones: [snapshot],
  }
  return {
    ...state,
    setups: [setup, ...state.setups].slice(0, JOURNAL_MAX_SETUPS),
  }
}

/** Mark open 3/4 setups from a previous candle as unanswered. */
export function markUnanswered(
  state: DecisionJournalState,
  currentCandleTime: number | null,
): DecisionJournalState {
  if (currentCandleTime == null) return state
  let changed = false
  const setups = state.setups.map((s) => {
    if (s.disposition !== 'open') return s
    if (s.candleTime >= currentCandleTime) return s
    // Only unanswered if never skipped/taken and had a watch or rejected click.
    const watched = s.milestones.some((m) =>
      m.kind === 'near_3_4' || m.kind === 'qualify_4_4_forming'
      || m.kind === 'qualify_4_4_confirmed' || m.kind === 'manual_click'
      || m.kind === 'manual_rejected',
    )
    if (!watched) return s
    changed = true
    return { ...s, disposition: 'unanswered' as const }
  })
  return changed ? { ...state, setups } : state
}

export function summarizeJournal(state: DecisionJournalState): {
  taken: number
  skipped: number
  unanswered: number
  open: number
  settledPnl: number | null
  pendingContracts: number
} {
  let taken = 0
  let skipped = 0
  let unanswered = 0
  let open = 0
  let pnl = 0
  let pnlN = 0
  let pending = 0
  for (const s of state.setups) {
    if (s.disposition === 'taken') taken += 1
    else if (s.disposition === 'skipped') skipped += 1
    else if (s.disposition === 'unanswered') unanswered += 1
    else open += 1
    const settlements = new Map<number, MilestoneSnapshot>()
    for (const milestone of s.milestones) {
      const contractId = milestone.execution?.contractId
      if (milestone.kind === 'settlement' && contractId != null) {
        settlements.set(contractId, milestone)
      }
    }
    for (const settlement of settlements.values()) {
      if (settlement.execution?.profit != null) {
        pnl += settlement.execution.profit
        pnlN += 1
      }
    }
    const buyIds = new Set(
      s.milestones
        .filter((milestone) => milestone.kind === 'buy')
        .map((milestone) => milestone.execution?.contractId)
        .filter((contractId): contractId is number => contractId != null),
    )
    for (const contractId of buyIds) {
      if (!settlements.has(contractId)) pending += 1
    }
  }
  return {
    taken,
    skipped,
    unanswered,
    open,
    settledPnl: pnlN ? pnl : null,
    pendingContracts: pending,
  }
}

export function exportJournalCsv(state: DecisionJournalState): string {
  const header = [
    'setup_id', 'disposition', 'side', 'candle_time_utc', 'milestone', 'frozen_at_utc',
    'price', 'm1_passed', 'm1_state', 'm5_trend', 'm5_agreement', 'm15_trend', 'm15_agreement',
    'skip_reason', 'attempt_id', 'account_id', 'contract_id', 'entry_spot', 'barrier',
    'buy_price', 'payout', 'profit', 'currency',
  ].join(',')
  const lines: string[] = [header]
  for (const s of state.setups) {
    for (const m of s.milestones) {
      lines.push([
        s.id,
        s.disposition,
        s.side,
        new Date(s.candleTime * 1000).toISOString(),
        m.kind,
        new Date(m.frozenAtEpoch * 1000).toISOString(),
        m.price ?? '',
        m.m1.passed,
        m.m1.state,
        m.m5.trend ?? '',
        m.m5.agreement ?? '',
        m.m15.trend ?? '',
        m.m15.agreement ?? '',
        m.skipReason ? `"${m.skipReason.replaceAll('"', '""')}"` : '',
        m.attemptId ?? '',
        m.accountId ?? '',
        m.execution?.contractId ?? '',
        m.execution?.entrySpot ?? '',
        m.execution?.barrier ?? '',
        m.execution?.buyPrice ?? '',
        m.execution?.payout ?? '',
        m.execution?.profit ?? '',
        m.execution?.currency ?? '',
      ].join(','))
    }
  }
  return lines.join('\n')
}
