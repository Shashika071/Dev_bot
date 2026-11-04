import type { Candle, ConfirmationFlags, Tick } from '../../types'
import type { EnrichedSeries } from '../../strategy/signalEngine'

export type StrategyId =
  | 'B0' | 'B1'
  | 'C1' | 'C2' | 'C3' | 'C4' | 'C5' | 'C6'
  | 'S1' | 'S2' | 'S3' | 'S4' | 'S5' | 'S6' | 'S7'
  | 'T1' | 'T2' | 'T3'
  | 'O1' | 'O2'
  | 'N1' | 'N2'
  | 'H1'

export type StrategyFamily =
  | 'baseline_live'
  | 'baseline_closed'
  | 'confirmation_mask'
  | 'pattern'
  | 'statistical'
  | 'stochastic'
  | 'cci_dmi'
  | 'hybrid'

export type ResearchSide = 'RISE' | 'FALL'

export interface StrategyDefinition {
  id: StrategyId
  name: string
  family: StrategyFamily
  version: string
  /** Human-readable exact rules */
  rules: string
  /** Forming candle (B0) vs completed only */
  usesFormingCandle: boolean
  /** Share cooldown/qualify schedule with this id (if any) */
  pairedWith?: StrategyId
  evaluate: (ctx: StrategyContext) => StrategyHit | null
}

export interface StrategyContext {
  /** All candles including optional forming last bar */
  candles: Candle[]
  /** Closed-only prefix length (candles[0..closedCount-1] are closed) */
  closedCount: number
  /** Index to evaluate (B0: last; others: last closed) */
  index: number
  series: EnrichedSeries
  /** Extra series caches built by engine */
  extras: ResearchExtras
  feedHealthy: boolean
  nowEpoch: number
}

export interface ResearchExtras {
  ema20: (number | null)[]
  ema50: (number | null)[]
  bbUpper: (number | null)[]
  bbLower: (number | null)[]
  bbMid: (number | null)[]
  stochK: (number | null)[]
  stochD: (number | null)[]
  cci20: (number | null)[]
  plusDi: (number | null)[]
  minusDi: (number | null)[]
  adx: (number | null)[]
  logReturns: (number | null)[]
}

export interface StrategyHit {
  side: ResearchSide
  reason: string
  flags?: ConfirmationFlags
  passed?: number
  total?: number
  metrics?: Record<string, number | boolean | string | null>
}

export interface SignalCandidate {
  strategyId: StrategyId
  side: ResearchSide
  candleTime: number
  signalEpoch: number
  reason: string
  flags?: ConfirmationFlags
  passed?: number
  total?: number
  metrics?: Record<string, number | boolean | string | null>
  forming: boolean
}

export type HorizonMin = 1 | 3 | 5 | 7

export type MoveClass = 'continuation' | 'reversal' | 'unchanged' | 'unknown' | 'pending'

export interface HorizonResult {
  minutes: HorizonMin
  targetEpoch: number
  status: MoveClass
  observedEpoch?: number
  observedPrice?: number
  delaySec?: number
  signedMove?: number
  signedMoveInAtr?: number | null
  randomSignedMove?: number
  randomClass?: Exclude<MoveClass, 'pending'>
  reason?: string
}

export interface FrozenAtr {
  atr14: number | null
  atrPct: number | null
  candleRange: number | null
  rangeOverAtr: number | null
}

export interface ResearchObservation {
  id: string
  strategyId: StrategyId
  side: ResearchSide
  candleTime: number
  signalEpoch: number
  receiptEpoch: number
  entryReferencePrice: number
  entryTick: Tick
  forming: boolean
  reason: string
  flags?: ConfirmationFlags
  passed?: number
  total?: number
  metrics?: Record<string, number | boolean | string | null>
  atr: FrozenAtr
  /** Legacy v1 sampled benchmark; v2 uses an exact equally weighted benchmark. */
  randomSide?: ResearchSide
  lastTickEpoch: number
  horizons: HorizonResult[]
  pendingHorizons: boolean
  /** Touch paper fields */
  touch?: TouchPaperState
}

export interface TouchPathState {
  paperEntryEpoch: number | null
  paperEntryPrice: number | null
  absoluteBarrier: number | null
  status: 'pending' | 'hit' | 'miss' | 'unknown'
  firstTouchEpoch?: number
  reason?: string
  lastTickEpoch?: number
  coverageLost?: boolean
}

export interface TouchPaperState extends TouchPathState {
  barrierAbs: number
  durationMin: number
  stake: number
  proposalAsk?: number | null
  proposalPayout?: number | null
  proposalAgeSec?: number | null
  paperPnl?: number | null
  quoteAvailable: boolean
  quoteStatus?: 'waiting' | 'available' | 'unavailable' | 'expired'
  quoteReason?: string
  quoteRequestEpoch?: number
  quoteResponseEpoch?: number
  /** Separate simulation entered on the first tick after quote receipt. */
  quotedPath?: TouchPathState
}

export interface RejectedCandidate {
  strategyId: StrategyId
  candleTime: number
  signalEpoch: number
  reason: string
  side?: ResearchSide
}
