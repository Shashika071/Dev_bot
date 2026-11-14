import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ConfirmationFlags } from '../lib/types'
import type { EntryDisplay } from '../lib/strategy/entryDisplay'
import type { LiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { MultiTimeframeContext } from '../lib/timeframes/timeframeContext'
import { freezeClosedContextSnapshot } from '../lib/timeframes/timeframeContext'
import { missingConditionLabel } from '../lib/timeframes/decisionSummary'
import {
  appendMilestone,
  emptyJournal,
  exportJournalCsv,
  loadJournal,
  markUnanswered,
  saveJournal,
  setupKey,
  summarizeJournal,
  type DecisionJournalState,
  type MilestoneSnapshot,
  type TfSnapshot,
} from '../lib/journal/decisionJournal'

function tfSnap(ctx: MultiTimeframeContext, which: 'm5' | 'm15'): TfSnapshot {
  return freezeClosedContextSnapshot(ctx[which])
}

function buildSnapshot(opts: {
  kind: MilestoneSnapshot['kind']
  attemptId?: string
  accountId?: string
  side: 'RISE' | 'FALL'
  candleTime: number
  price: number | null
  nowSec: number
  tickEpoch: number | null
  passed: number
  forming: boolean
  state: 'WATCHING' | 'FORMING' | 'CONFIRMED'
  flags: ConfirmationFlags | null
  missingCondition: string | null
  context: MultiTimeframeContext
  skipReason?: string
  quote?: MilestoneSnapshot['quote']
  execution?: MilestoneSnapshot['execution']
}): MilestoneSnapshot {
  return {
    kind: opts.kind,
    attemptId: opts.attemptId,
    accountId: opts.accountId,
    frozenAtEpoch: opts.nowSec,
    receiptEpoch: opts.tickEpoch ?? opts.nowSec,
    price: opts.price,
    m1: {
      side: opts.side,
      candleTime: opts.candleTime,
      passed: opts.passed,
      total: 4,
      forming: opts.forming,
      state: opts.state,
      flags: opts.flags,
      missingCondition: opts.missingCondition,
    },
    m5: tfSnap(opts.context, 'm5'),
    m15: tfSnap(opts.context, 'm15'),
    skipReason: opts.skipReason,
    quote: opts.quote,
    execution: opts.execution,
  }
}

function attemptOrigin(
  state: DecisionJournalState,
  attemptId: string,
): MilestoneSnapshot | null {
  for (const setup of state.setups) {
    const click = setup.milestones.find(
      (milestone) => milestone.kind === 'manual_click' && milestone.attemptId === attemptId,
    )
    if (click) return click
    const linked = setup.milestones.find((milestone) => milestone.attemptId === attemptId)
    if (linked) return linked
  }
  return null
}

export function useDecisionJournal(opts: {
  entry: EntryDisplay
  liveSetup: LiveSetupConfidence
  context: MultiTimeframeContext
  side: 'RISE' | 'FALL' | null
  candleTime: number | null
  price: number | null
  tickEpoch: number | null
  nowSec: number
}) {
  const { entry, liveSetup, context, side, candleTime, price, tickEpoch, nowSec } = opts
  const [journal, setJournal] = useState<DecisionJournalState>(() => loadJournal())
  const journalRef = useRef(journal)
  const latestRef = useRef(opts)
  const seenRef = useRef(new Set<string>())

  useEffect(() => {
    latestRef.current = opts
  }, [opts])

  const persist = useCallback((next: DecisionJournalState) => {
    const ok = saveJournal(next)
    const stored = { ...next, storageError: !ok }
    journalRef.current = stored
    setJournal(stored)
    return ok
  }, [])

  const append = useCallback((snapshot: MilestoneSnapshot) => {
    const previous = journalRef.current
    const next = appendMilestone(previous, snapshot)
    if (next === previous) return !previous.storageError
    return persist(next)
  }, [persist])

  // Mark unanswered on candle rollover
  useEffect(() => {
    const previous = journalRef.current
    const next = markUnanswered(previous, candleTime)
    if (next !== previous) persist(next)
  }, [candleTime, persist])

  // Auto-capture 3/4 and 4/4 milestones
  useEffect(() => {
    if (!side || candleTime == null) return
    const setup = side === 'RISE' ? liveSetup.rise : liveSetup.fall
    const idBase = setupKey(candleTime, side)

    if (setup.passed === 3 && entry.state === 'WAIT') {
      const key = `${idBase}:near_3_4`
      if (!seenRef.current.has(key)) {
        seenRef.current.add(key)
        const snap = buildSnapshot({
          kind: 'near_3_4',
          side,
          candleTime,
          price,
          nowSec,
          tickEpoch,
          passed: 3,
          forming: liveSetup.forming,
          state: 'WATCHING',
          flags: setup.flags,
          missingCondition: missingConditionLabel(setup),
          context,
        })
        append(snap)
      }
    }

    if (entry.state === 'FORMING' && entry.side === side) {
      const key = `${idBase}:qualify_4_4_forming`
      if (!seenRef.current.has(key)) {
        seenRef.current.add(key)
        const snap = buildSnapshot({
          kind: 'qualify_4_4_forming',
          side,
          candleTime,
          price,
          nowSec,
          tickEpoch,
          passed: 4,
          forming: true,
          state: 'FORMING',
          flags: setup.flags,
          missingCondition: null,
          context,
        })
        append(snap)
      }
    }

    if (entry.state === 'CONFIRMED' && entry.side === side) {
      const key = `${idBase}:qualify_4_4_confirmed`
      if (!seenRef.current.has(key)) {
        seenRef.current.add(key)
        const snap = buildSnapshot({
          kind: 'qualify_4_4_confirmed',
          side,
          candleTime,
          price,
          nowSec,
          tickEpoch,
          passed: 4,
          forming: false,
          state: 'CONFIRMED',
          flags: setup.flags,
          missingCondition: null,
          context,
        })
        append(snap)
      }
    }
  }, [side, candleTime, entry.state, entry.side, liveSetup, context, price, nowSec, tickEpoch, append])

  const recordSkip = useCallback((reason: string) => {
    if (!side || candleTime == null) return
    const setup = side === 'RISE' ? liveSetup.rise : liveSetup.fall
    const snap = buildSnapshot({
      kind: 'skip',
      side,
      candleTime,
      price,
      nowSec,
      tickEpoch,
      passed: setup.passed,
      forming: liveSetup.forming,
      state: entry.state === 'CONFIRMED' ? 'CONFIRMED' : entry.state === 'FORMING' ? 'FORMING' : 'WATCHING',
      flags: setup.flags,
      missingCondition: missingConditionLabel(setup),
      context,
      skipReason: reason.trim() || undefined,
    })
    append(snap)
  }, [side, candleTime, liveSetup, entry.state, context, price, nowSec, tickEpoch, append])

  const recordManualClick = useCallback((
    clickSide: 'RISE' | 'FALL',
    attemptId: string,
    quote?: MilestoneSnapshot['quote'],
  ) => {
    const latest = latestRef.current
    const ct = latest.candleTime ?? Math.floor(latest.nowSec / 60) * 60
    const setup = clickSide === 'RISE' ? latest.liveSetup.rise : latest.liveSetup.fall
    const snap = buildSnapshot({
      kind: 'manual_click',
      attemptId,
      side: clickSide,
      candleTime: ct,
      price: latest.price,
      nowSec: latest.nowSec,
      tickEpoch: latest.tickEpoch,
      passed: setup.passed,
      forming: latest.liveSetup.forming,
      state: latest.entry.state === 'CONFIRMED'
        ? 'CONFIRMED'
        : latest.entry.state === 'FORMING' ? 'FORMING' : 'WATCHING',
      flags: setup.flags,
      missingCondition: missingConditionLabel(setup),
      context: latest.context,
      quote,
    })
    return append(snap)
  }, [append])

  const lifecycleSnapshot = useCallback((
    kind: 'manual_rejected' | 'buy' | 'settlement',
    payload: {
      attemptId: string
      accountId?: string
      execution?: NonNullable<MilestoneSnapshot['execution']>
    },
  ): MilestoneSnapshot | null => {
    const origin = attemptOrigin(journalRef.current, payload.attemptId)
    if (!origin) return null
    const latest = latestRef.current
    return {
      ...origin,
      kind,
      attemptId: payload.attemptId,
      accountId: payload.accountId ?? origin.accountId,
      frozenAtEpoch: latest.nowSec,
      receiptEpoch: latest.tickEpoch ?? latest.nowSec,
      price: payload.execution?.entrySpot ?? latest.price,
      execution: payload.execution,
    }
  }, [])

  const recordManualFailure = useCallback((attemptId: string) => {
    const snap = lifecycleSnapshot('manual_rejected', { attemptId })
    return snap ? append(snap) : false
  }, [append, lifecycleSnapshot])

  const recordBuy = useCallback((payload: {
    attemptId: string
    accountId: string
    execution: NonNullable<MilestoneSnapshot['execution']>
  }) => {
    const snap = lifecycleSnapshot('buy', payload)
    return snap ? append(snap) : false
  }, [append, lifecycleSnapshot])

  const recordSettlement = useCallback((payload: {
    attemptId: string
    accountId: string
    execution: NonNullable<MilestoneSnapshot['execution']>
  }) => {
    const snap = lifecycleSnapshot('settlement', payload)
    return snap ? append(snap) : false
  }, [append, lifecycleSnapshot])

  const summary = useMemo(() => summarizeJournal(journal), [journal])

  const downloadCsv = useCallback(() => {
    const blob = new Blob([exportJournalCsv(journal)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'v100_decision_journal.csv'
    a.click()
    URL.revokeObjectURL(url)
  }, [journal])

  const clearJournal = useCallback(() => {
    const next = emptyJournal()
    persist(next)
    seenRef.current.clear()
  }, [persist])

  return {
    journal,
    summary,
    storageError: journal.storageError,
    recordSkip,
    recordManualClick,
    recordManualFailure,
    recordBuy,
    recordSettlement,
    downloadCsv,
    clearJournal,
  }
}
