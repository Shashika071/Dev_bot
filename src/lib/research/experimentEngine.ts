/**
 * Multi-strategy research experiment engine (paper-only).
 * Never places trades.
 */
import type { Candle, Tick } from '../types'
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
  STRATEGY_COOLDOWN_SEC,
} from './horizons'
import { advanceTouchPaper, maybeStartTouchPaper } from './touchPaper'
import { DEFAULT_FROZEN_CONTRACT, type FrozenContractSettings } from './contractSettings'

export { DEFAULT_FROZEN_CONTRACT, type FrozenContractSettings }
export const RESEARCH_CODE_VERSION = 'v100-research-engine-1'
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
    contract: { ...contract },
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
  }
}

/** Start (or restart) a 72h discovery run. Always creates a fresh collecting state. */
export function startDiscovery(state: ExperimentState, nowMs: number): ExperimentState {
  const startedAt = Math.floor(nowMs / 1000)
  return {
    ...createInitialState('discovery', state.enabledStrategyIds, state.contract),
    stage: 'collecting',
    startedAt,
    endsAt: startedAt + DISCOVERY_HOURS * 3600,
    mode: 'discovery',
  }
}

/** Resume without resetting endsAt */
export function resumeCollecting(state: ExperimentState): ExperimentState {
  if (state.stage === 'completed' || state.stage === 'not_started') return state
  if (state.endsAt != null && Math.floor(Date.now() / 1000) >= state.endsAt) {
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

function onCooldown(state: ExperimentState, id: StrategyId, epoch: number): boolean {
  const until = state.cooldowns[cooldownKey(id)] ?? 0
  return epoch < until
}

function setCooldown(state: ExperimentState, id: StrategyId, fromEpoch: number): void {
  state.cooldowns[cooldownKey(id)] = fromEpoch + STRATEGY_COOLDOWN_SEC
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
  const randomSide: ResearchObservation['side'] = Math.random() < 0.5 ? 'RISE' : 'FALL'
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
    randomSide,
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
  }

  if (!feedHealthy) {
    if (next.stage === 'collecting') next.stage = 'reconnecting'
    next.observations = next.observations.map((o) => advanceObservationHorizons(o, null, false))
    next.observations = next.observations.map((o) => advanceTouchPaper(o, null, false, next.contract))
    return next
  }
  if (next.stage === 'reconnecting') next.stage = 'collecting'

  // Deadline → finishing
  if (next.endsAt != null && tick.epoch >= next.endsAt && next.stage === 'collecting') {
    next.stage = 'finishing'
  }

  // Advance pending observations
  next.observations = next.observations.map((o) => {
    let adv = advanceObservationHorizons(o, tick, true)
    adv = advanceTouchPaper(adv, tick, true, next.contract)
    return adv
  })

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
    if (onCooldown(next, pending.strategyId, tick.epoch)) {
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
    const pending = next.observations.some((o) => o.pendingHorizons || o.touch?.status === 'pending')
      || next.awaitingCloseEntry.length > 0
    if (!pending) {
      next.stage = 'completed'
      next.finishedAt = tick.epoch
    }
    return next
  }

  if (!acceptingNew(next, tick.epoch)) return next
  if (candles.length < WARMUP_BARS + 5) return next

  const closed = candles.filter((c) => c.isClosed)
  if (closed.length < WARMUP_BARS + 1) return next

  // Detect newly closed candle: last closed time just completed
  const lastClosed = closed[closed.length - 1]!
  const forming = candles[candles.length - 1] && !candles[candles.length - 1]!.isClosed
    ? candles[candles.length - 1]!
    : null

  // Build series on window (closed + optional forming treated closed for B0)
  const forClosed = closed.length > WARMUP_BARS + 250 ? closed.slice(-(WARMUP_BARS + 250)) : closed
  const seriesClosed = enrich(forClosed)
  const extrasClosed = buildResearchExtras(forClosed)

  // Process completed-candle strategies when we see first tick of new minute after close
  // Signal candle = lastClosed; closeEpoch = lastClosed.time + 60
  const closeEpoch = lastClosed.time + 60
  if (tick.epoch >= closeEpoch && tick.epoch <= closeEpoch + 5) {
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
      if (onCooldown(next, def.id, tick.epoch)) {
        next.rejected.push({
          strategyId: def.id,
          candleTime: lastClosed.time,
          signalEpoch: tick.epoch,
          reason: 'Cooldown active',
          side: hit.side,
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
  }

  // B0 forming evaluation
  if (forming && next.enabledStrategyIds.includes('B0')) {
    const preview = [...closed, { ...forming, isClosed: true }]
    const win = preview.length > WARMUP_BARS + 250 ? preview.slice(-(WARMUP_BARS + 250)) : preview
    const series = enrich(win)
    const extras = buildResearchExtras(win)
    const index = win.length - 1
    const def = getStrategy('B0')
    const hit = def.evaluate({
      candles: win,
      closedCount: closed.length,
      index,
      series,
      extras,
      feedHealthy: true,
      nowEpoch: tick.epoch,
    })
    if (hit) {
      const key = `B0:${forming.time}:${hit.side}`
      if (!next.b0Fired.includes(key) && !next.acceptedKeys.includes(key)) {
        if (onCooldown(next, 'B0', tick.epoch)) {
          next.rejected.push({
            strategyId: 'B0',
            candleTime: forming.time,
            signalEpoch: tick.epoch,
            reason: 'Cooldown active',
            side: hit.side,
          })
        } else {
          next.b0Fired.push(key)
          pushObservation(next, {
            strategyId: 'B0',
            side: hit.side,
            candleTime: forming.time,
            signalEpoch: tick.epoch,
            receiptEpoch,
            entryReferencePrice: tick.quote,
            tick,
            forming: true,
            reason: hit.reason,
            flags: hit.flags,
            passed: hit.passed,
            total: hit.total,
            metrics: hit.metrics,
            atrCandles: candles,
          })
        }
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
  const closed = candles.filter((c) => c.isClosed)
  if (closed.length < WARMUP_BARS + 1) return []
  const forClosed = closed
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
