/**
 * Multi-strategy research experiment engine (paper-only).
 * Never places trades.
 */
import type { Candle, Tick } from '../types'
import { closedSegments } from '../deriv/candles'
import { enrich, WARMUP_BARS } from '../strategy/signalEngine'
import { freezeAtr14 } from './atrFreeze'
import { buildResearchExtras } from './strategies/buildExtras'
import { STRATEGY_REGISTRY, getStrategy } from './strategies/registry'
import type {
  RejectedCandidate,
  ResearchObservation,
  SignalCandidate,
  StrategyId,
  StrategyContext,
} from './strategies/types'
import {
  advanceObservationHorizons,
  makeHorizons,
  POST_WIN_COOLDOWN_SEC,
  STRATEGY_COOLDOWN_SEC,
} from './horizons'
import { advanceTouchPaper, maybeStartTouchPaper, expireTouchPaper, hasPendingTouch } from './touchPaper'
import {
  advanceC5TouchPaper,
  expireC5TouchPaper,
  hasPendingC5Touch,
  summarizeC5CohortBundle,
  tryRecordC5ContextObservation,
  type C5CohortSummaryBundle,
  type C5ContextObservation,
} from './c5ContextStudy'
import {
  DEFAULT_FROZEN_CONTRACT,
  c5StudyContract,
  normalizeContractSettings,
  type FrozenContractSettings,
} from './contractSettings'
export { DEFAULT_FROZEN_CONTRACT, type FrozenContractSettings }
export const RESEARCH_CODE_VERSION = 'v100-research-engine-5'
export const DISCOVERY_HOURS = 72

export type ExperimentStage =
  | 'not_started'
  | 'collecting'
  | 'reconnecting'
  | 'finishing'
  | 'completed'

export type ExperimentMode = 'discovery' | 'confirmation'

export interface ExperimentState {
  mode: ExperimentMode
  stage: ExperimentStage
  startedAt: number | null
  endsAt: number | null
  finishedAt: number | null
  codeVersion: string
  contract: FrozenContractSettings
  enabledStrategyIds: StrategyId[]
  /** strategyId → cooldownUntil epoch */
  cooldowns: Record<string, number>
  /** dedupe keys already accepted: `${strategyId}:${candleTime}:${side}` */
  acceptedKeys: string[]
  /** B0 forming first-hit keys */
  b0Fired: string[]
  observations: ResearchObservation[]
  rejected: RejectedCandidate[]
  downtimeSec: number
  lastTickEpoch: number | null
  feedHealthy: boolean
  lastClosedEvaluated?: number
  awaitingCloseEntry: Array<{
    strategyId: StrategyId
    candleTime: number
    side: ResearchObservation['side']
    reason: string
    flags?: ResearchObservation['flags']
    passed?: number
    total?: number
    metrics?: ResearchObservation['metrics']
    closeEpoch: number
  }>
  c5ContextObservations: C5ContextObservation[]
  /** Shared with the manual journal: C5:candleTime:side. */
  c5AcceptedKeys: string[]
  /** Frozen discovery cohort summary when confirmation starts. */
  priorDiscoveryC5Summary: C5CohortSummaryBundle | null
}

export function createInitialState(
  mode: ExperimentMode = 'discovery',
  enabledIds: StrategyId[] = STRATEGY_REGISTRY.map((s) => s.id),
  contract: FrozenContractSettings = DEFAULT_FROZEN_CONTRACT,
): ExperimentState {
  return {
    mode,
    stage: 'not_started',
    startedAt: null,
    endsAt: null,
    finishedAt: null,
    codeVersion: RESEARCH_CODE_VERSION,
    contract: normalizeContractSettings(contract),
    enabledStrategyIds: [...enabledIds],
    cooldowns: {},
    acceptedKeys: [],
    b0Fired: [],
    observations: [],
    rejected: [],
    downtimeSec: 0,
    lastTickEpoch: null,
    feedHealthy: false,
    awaitingCloseEntry: [],
    c5ContextObservations: [],
    c5AcceptedKeys: [],
    priorDiscoveryC5Summary: null,
  }
}

/** Start confirmation: fresh 72h run with prior discovery C5 cohorts frozen. */
export function startConfirmation(
  previous: ExperimentState,
  nowMs: number,
  enabledIds: StrategyId[] = previous.enabledStrategyIds,
  contract = previous.contract,
): ExperimentState {
  if (previous.codeVersion !== RESEARCH_CODE_VERSION || previous.mode !== 'discovery' || previous.startedAt == null) {
    throw new Error('Start a discovery with the current C5 methodology before starting confirmation.')
  }
  const priorDiscoveryC5Summary = summarizeC5CohortBundle(previous.c5ContextObservations ?? [])
  const base = startDiscovery(createInitialState('confirmation', enabledIds, contract), nowMs)
  return { ...base, priorDiscoveryC5Summary }
}

/** Start (or restart) a 72h discovery run. Always creates a fresh collecting state. */
export function startDiscovery(state: ExperimentState, nowMs: number): ExperimentState {
  const startedAt = Math.floor(nowMs / 1000)
  return {
    ...createInitialState(state.mode, state.enabledStrategyIds, state.contract),
    stage: 'collecting',
    startedAt,
    endsAt: startedAt + DISCOVERY_HOURS * 3600,
    mode: state.mode,
  }
}

/** Resume without resetting endsAt */
export function resumeCollecting(state: ExperimentState, nowEpoch = Date.now() / 1000): ExperimentState {
  if (state.codeVersion !== RESEARCH_CODE_VERSION) return state
  if (state.stage === 'completed' || state.stage === 'not_started') return state
  if (state.endsAt != null && nowEpoch >= state.endsAt) {
    return { ...state, stage: 'finishing' }
  }
  return { ...state, stage: 'collecting' }
}

function cooldownKey(id: StrategyId): string {
  const def = getStrategy(id)
  if (!def.pairedWith) return id
  // Canonical pair key: lexicographically smaller id
  return [id, def.pairedWith].sort().join('+')
}

/** Post-win pause is always per strategy — never shared with siblings. */
function postWinCooldownKey(id: StrategyId): string {
  return `win:${id}`
}

function onCooldown(state: ExperimentState, id: StrategyId, epoch: number): boolean {
  const entryUntil = state.cooldowns[cooldownKey(id)] ?? 0
  const winUntil = state.cooldowns[postWinCooldownKey(id)] ?? 0
  return epoch < entryUntil || epoch < winUntil
}

function setCooldown(state: ExperimentState, id: StrategyId, fromEpoch: number): void {
  const secs = state.contract.entryCooldownSec ?? STRATEGY_COOLDOWN_SEC
  const until = fromEpoch + secs
  const key = cooldownKey(id)
  state.cooldowns[key] = Math.max(state.cooldowns[key] ?? 0, until)
}

/**
 * After a confirmed Touch win, only THAT strategy waits (research interval, not live Trade).
 * Other strategies (including focused B1/C5/C1 peers) keep collecting.
 */
function applyPostWinCooldown(state: ExperimentState, id: StrategyId, winEpoch: number): void {
  const secs = state.contract.postWinCooldownSec ?? POST_WIN_COOLDOWN_SEC
  const until = winEpoch + secs
  const key = postWinCooldownKey(id)
  state.cooldowns[key] = Math.max(state.cooldowns[key] ?? 0, until)
}

function applyNewTouchWins(
  before: ExperimentState['observations'],
  after: ExperimentState['observations'],
  state: ExperimentState,
): void {
  const prevById = new Map(before.map((o) => [o.id, o]))
  for (const obs of after) {
    const prev = prevById.get(obs.id)
    const wasHit = prev?.touch?.status === 'hit'
    const nowHit = obs.touch?.status === 'hit'
    if (!wasHit && nowHit && obs.touch?.firstTouchEpoch != null) {
      applyPostWinCooldown(state, obs.strategyId, obs.touch.firstTouchEpoch)
    }
  }
}

function acceptingNew(state: ExperimentState, nowEpoch: number): boolean {
  if (state.stage !== 'collecting') return false
  if (state.endsAt != null && nowEpoch >= state.endsAt) return false
  return state.feedHealthy
}

function pushObservation(
  state: ExperimentState,
  partial: {
    strategyId: StrategyId
    side: ResearchObservation['side']
    candleTime: number
    signalEpoch: number
    receiptEpoch: number
    entryReferencePrice: number
    tick: Tick
    forming: boolean
    reason: string
    flags?: ResearchObservation['flags']
    passed?: number
    total?: number
    metrics?: ResearchObservation['metrics']
    atrCandles: Candle[]
  },
): void {
  const key = `${partial.strategyId}:${partial.candleTime}:${partial.side}`
  if (state.acceptedKeys.includes(key)) return
  state.acceptedKeys.push(key)
  setCooldown(state, partial.strategyId, partial.signalEpoch)
  const atr = freezeAtr14(partial.atrCandles)
  let obs: ResearchObservation = {
    id: key,
    strategyId: partial.strategyId,
    side: partial.side,
    candleTime: partial.candleTime,
    signalEpoch: partial.signalEpoch,
    receiptEpoch: partial.receiptEpoch,
    entryReferencePrice: partial.entryReferencePrice,
    entryTick: partial.tick,
    forming: partial.forming,
    reason: partial.reason,
    flags: partial.flags,
    passed: partial.passed,
    total: partial.total,
    metrics: partial.metrics,
    atr,
    lastTickEpoch: partial.tick.epoch,
    horizons: makeHorizons(partial.signalEpoch),
    pendingHorizons: true,
  }
  obs = maybeStartTouchPaper(obs, state.contract, partial.tick)
  state.observations.push(obs)
}

/**
 * Process a public tick against the experiment.
 * `candles` should include forming bar as last when present.
 */
export function onTick(
  state: ExperimentState,
  tick: Tick,
  candles: Candle[],
  receiptEpoch: number,
  feedHealthy: boolean,
): ExperimentState {
  // Never mix corrected calculations into a pre-fix experiment.
  if (state.codeVersion !== RESEARCH_CODE_VERSION) return state
  if (!Number.isFinite(tick.epoch) || !Number.isFinite(tick.quote) || tick.quote <= 0
    || (state.lastTickEpoch != null && tick.epoch <= state.lastTickEpoch)) return state
  const next: ExperimentState = {
    ...state,
    feedHealthy,
    lastTickEpoch: tick.epoch,
    cooldowns: { ...state.cooldowns },
    acceptedKeys: [...state.acceptedKeys],
    b0Fired: [...state.b0Fired],
    observations: state.observations.map((o) => ({ ...o, horizons: o.horizons.map((h) => ({ ...h })) })),
    rejected: [...state.rejected],
    awaitingCloseEntry: [...state.awaitingCloseEntry],
    c5ContextObservations: (state.c5ContextObservations ?? []).map((o) => ({ ...o, touch: o.touch ? { ...o.touch } : undefined })),
    c5AcceptedKeys: [...(state.c5AcceptedKeys ?? [])],
    priorDiscoveryC5Summary: state.priorDiscoveryC5Summary ?? null,
  }
  // Every member of a pair sees cooldowns from BEFORE this tick's batch.
  const admissionState = { ...next, cooldowns: { ...next.cooldowns } }

  if (!feedHealthy) {
    if (next.stage === 'collecting') next.stage = 'reconnecting'
    next.observations = next.observations.map((o) => advanceObservationHorizons(o, null, false))
    next.observations = next.observations.map((o) => advanceTouchPaper(o, null, false, next.contract))
    next.c5ContextObservations = next.c5ContextObservations.map((o) =>
      advanceC5TouchPaper(o, null, false, c5StudyContract(next.contract)))
    return next
  }
  if (next.stage === 'reconnecting') next.stage = 'collecting'

  // Deadline → finishing
  if (next.endsAt != null && tick.epoch >= next.endsAt && next.stage === 'collecting') {
    next.stage = 'finishing'
  }

  // Advance pending observations
  const beforeTouch = next.observations
  next.observations = next.observations.map((o) => {
    let adv = advanceObservationHorizons(o, tick, true)
    adv = advanceTouchPaper(adv, tick, true, next.contract, receiptEpoch)
    return adv
  })
  applyNewTouchWins(beforeTouch, next.observations, next)
  next.c5ContextObservations = next.c5ContextObservations.map((o) => {
    const adv = advanceC5TouchPaper(o, tick, true, c5StudyContract(next.contract), receiptEpoch)
    return { ...adv, lastTickEpoch: tick.epoch }
  })
  // Post-win cooldowns must also block same-tick admissions using the snapshot.
  for (const [k, until] of Object.entries(next.cooldowns)) {
    if ((admissionState.cooldowns[k] ?? 0) < until) admissionState.cooldowns[k] = until
  }

  // Resolve awaiting close-entry (first tick after candle close)
  const stillAwaiting = []
  for (const pending of next.awaitingCloseEntry) {
    if (tick.epoch < pending.closeEpoch) {
      stillAwaiting.push(pending)
      continue
    }
    if (tick.epoch > pending.closeEpoch + 5) {
      next.rejected.push({
        strategyId: pending.strategyId,
        candleTime: pending.candleTime,
        signalEpoch: tick.epoch,
        reason: 'No timely post-close entry tick (max 5s)',
        side: pending.side,
      })
      continue
    }
    if (!acceptingNew(next, tick.epoch) || onCooldown(admissionState, pending.strategyId, tick.epoch)) {
      next.rejected.push({
        strategyId: pending.strategyId,
        candleTime: pending.candleTime,
        signalEpoch: tick.epoch,
        reason: 'Cooldown active',
        side: pending.side,
      })
      continue
    }
    pushObservation(next, {
      strategyId: pending.strategyId,
      side: pending.side,
      candleTime: pending.candleTime,
      signalEpoch: tick.epoch,
      receiptEpoch,
      entryReferencePrice: tick.quote,
      tick,
      forming: false,
      reason: pending.reason,
      flags: pending.flags,
      passed: pending.passed,
      total: pending.total,
      metrics: pending.metrics,
      atrCandles: candles,
    })
  }
  next.awaitingCloseEntry = stillAwaiting

  // Finish when no pending work
  if (next.stage === 'finishing') {
    const pending = next.observations.some((o) => o.pendingHorizons || hasPendingTouch(o))
      || next.c5ContextObservations.some(hasPendingC5Touch)
      || next.awaitingCloseEntry.length > 0
    if (!pending) {
      next.stage = 'completed'
      next.finishedAt = tick.epoch
    }
    return next
  }

  if (!acceptingNew(next, tick.epoch)) return next
  if (candles.length < WARMUP_BARS + 5) return next

  const closed = closedSegments(candles).at(-1) ?? []
  if (closed.length < WARMUP_BARS + 1) return next

  // Detect newly closed candle: last closed time just completed
  const lastClosed = closed[closed.length - 1]!

  // Build series on closed-candle window
  const forClosed = closed.slice(-(WARMUP_BARS + 200))
  const seriesClosed = enrich(forClosed)
  const extrasClosed = buildResearchExtras(forClosed)

  // Process completed-candle strategies when we see first tick of new minute after close
  // Signal candle = lastClosed; closeEpoch = lastClosed.time + 60
  const closeEpoch = lastClosed.time + 60
  if (tick.epoch >= closeEpoch && tick.epoch <= closeEpoch + 5 && next.lastClosedEvaluated !== lastClosed.time) {
    next.lastClosedEvaluated = lastClosed.time
    const index = forClosed.length - 1
    const ctxBase: StrategyContext = {
      candles: forClosed,
      closedCount: forClosed.length,
      index,
      series: seriesClosed,
      extras: extrasClosed,
      feedHealthy: true,
      nowEpoch: tick.epoch,
    }
    for (const def of STRATEGY_REGISTRY) {
      if (!next.enabledStrategyIds.includes(def.id)) continue
      if (def.usesFormingCandle) continue
      const hit = def.evaluate(ctxBase)
      if (!hit) continue
      const key = `${def.id}:${lastClosed.time}:${hit.side}`
      if (next.acceptedKeys.includes(key)) continue
      if (next.awaitingCloseEntry.some((a) => a.strategyId === def.id && a.candleTime === lastClosed.time && a.side === hit.side)) {
        continue
      }
      if (onCooldown(admissionState, def.id, tick.epoch)) {
        next.rejected.push({
          strategyId: def.id,
          candleTime: lastClosed.time,
          signalEpoch: tick.epoch,
          reason: 'Cooldown active',
          side: hit.side,
          flags: hit.flags,
          passed: hit.passed,
          total: hit.total,
          metrics: hit.metrics,
        })
        continue
      }
      // Immediate entry on this post-close tick
      pushObservation(next, {
        strategyId: def.id,
        side: hit.side,
        candleTime: lastClosed.time,
        signalEpoch: tick.epoch,
        receiptEpoch,
        entryReferencePrice: tick.quote,
        tick,
        forming: false,
        reason: hit.reason,
        flags: hit.flags,
        passed: hit.passed,
        total: hit.total,
        metrics: hit.metrics,
        atrCandles: candles,
      })
    }
    if (next.startedAt != null && closeEpoch >= next.startedAt) {
      const c5 = tryRecordC5ContextObservation(
        next.c5AcceptedKeys,
        candles,
        lastClosed.time,
        tick,
        receiptEpoch,
        c5StudyContract(next.contract),
      )
      if (c5) {
        next.c5AcceptedKeys.push(c5.key)
        next.c5ContextObservations.push(c5.observation)
      }
    }
  }

  return next
}

export function listEnabledCandidates(
  state: ExperimentState,
  candles: Candle[],
): SignalCandidate[] {
  // Diagnostic helper for tests
  const closed = closedSegments(candles).at(-1) ?? []
  if (closed.length < WARMUP_BARS + 1) return []
  const forClosed = closed.slice(-(WARMUP_BARS + 200))
  const series = enrich(forClosed)
  const extras = buildResearchExtras(forClosed)
  const index = forClosed.length - 1
  const out: SignalCandidate[] = []
  for (const def of STRATEGY_REGISTRY) {
    if (!state.enabledStrategyIds.includes(def.id) || def.usesFormingCandle) continue
    const hit = def.evaluate({
      candles: forClosed, closedCount: forClosed.length, index, series, extras,
      feedHealthy: true, nowEpoch: forClosed[index]!.time + 60,
    })
    if (!hit) continue
    out.push({
      strategyId: def.id,
      side: hit.side,
      candleTime: forClosed[index]!.time,
      signalEpoch: forClosed[index]!.time + 60,
      reason: hit.reason,
      flags: hit.flags,
      passed: hit.passed,
      total: hit.total,
      metrics: hit.metrics,
      forming: false,
    })
  }
  return out
}

/** Deadline processing also runs when the feed is silent. Pure and replayable. */
export function advanceExperimentClock(state: ExperimentState, now: number): ExperimentState {
  if (state.codeVersion !== RESEARCH_CODE_VERSION || state.stage === 'not_started' || state.stage === 'completed') return state
  const observations = state.observations.map((obs) => {
    const horizons = obs.horizons.map((h) => h.status === 'pending' && now > h.targetEpoch + 5
      ? { ...h, status: 'unknown' as const, reason: 'No timely horizon tick (clock deadline)' } : h)
    return expireTouchPaper({ ...obs, horizons, pendingHorizons: horizons.some((h) => h.status === 'pending') }, now, state.contract)
  })
  const c5ContextObservations = (state.c5ContextObservations ?? []).map((o) =>
    expireC5TouchPaper(o, now, c5StudyContract(state.contract)))
  let stage: ExperimentStage = state.stage
  if (state.endsAt != null && now >= state.endsAt) stage = 'finishing'
  const pendingWork = observations.some((o) => o.pendingHorizons || hasPendingTouch(o))
    || c5ContextObservations.some(hasPendingC5Touch)
  if (stage === 'finishing' && !pendingWork) stage = 'completed'
  return { ...state, observations, c5ContextObservations, stage, finishedAt: stage === 'completed' ? now : state.finishedAt }
}

export function interruptResearchFeed(state: ExperimentState): ExperimentState {
  if (state.codeVersion !== RESEARCH_CODE_VERSION || state.stage === 'not_started' || state.stage === 'completed') return state
  return { ...state, feedHealthy: false,
    stage: state.stage === 'collecting' ? 'reconnecting' : state.stage,
    observations: state.observations.map((o) => advanceTouchPaper(
      advanceObservationHorizons(o, null, false), null, false, state.contract)),
    c5ContextObservations: (state.c5ContextObservations ?? []).map((o) =>
      advanceC5TouchPaper(o, null, false, c5StudyContract(state.contract))),
  }
}
