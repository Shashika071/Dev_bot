import type { Candle, Tick } from '../types'
import { applyTick } from '../deriv/candles'
import { createInitialState, onTick, advanceExperimentClock, interruptResearchFeed, resumeCollecting,
  RESEARCH_CODE_VERSION, type ExperimentState } from './experimentEngine'
import { attachProposalQuote, type PaperQuote } from './touchPaper'

export interface ResearchSession { state: ExperimentState; candles: Candle[]; sequence: number }
export type ResearchEvent =
  | { type: 'start'; state: ExperimentState; candles: Candle[] }
  | { type: 'history'; candles: Candle[] }
  | { type: 'tick'; tick: Tick; receiptEpoch: number; healthy: boolean }
  | { type: 'quote'; ids: string[]; quote: PaperQuote }
  | { type: 'clock'; now: number }
  | { type: 'feed_unhealthy' }
  | { type: 'resume'; now: number }
  | { type: 'stop_entries' }

export function emptyResearchSession(): ResearchSession {
  return { state: createInitialState(), candles: [], sequence: 0 }
}

/** The collector and offline replay apply the same ordered event journal. */
export function applyResearchEvent(session: ResearchSession, event: ResearchEvent): ResearchSession {
  let { state, candles } = session
  if (event.type === 'start') return { state: event.state, candles: event.candles, sequence: session.sequence + 1 }
  if (state.codeVersion !== RESEARCH_CODE_VERSION) return session
  switch (event.type) {
    case 'history': candles = event.candles; break
    case 'tick': {
      const t = event.tick
      if (!Number.isFinite(t.epoch) || !Number.isFinite(t.quote) || t.quote <= 0
        || (state.lastTickEpoch != null && t.epoch <= state.lastTickEpoch)) break
      candles = applyTick(candles, t).candles.slice(-1000)
      state = onTick(state, t, candles, event.receiptEpoch, event.healthy)
      break
    }
    case 'quote': {
      const ids = new Set(event.ids)
      state = { ...state, observations: state.observations.map((o) => ids.has(o.id)
        ? attachProposalQuote(o, event.quote, state.contract) : o) }
      break
    }
    case 'clock': state = advanceExperimentClock(state, event.now); break
    case 'feed_unhealthy': state = interruptResearchFeed(state); break
    case 'resume': state = resumeCollecting(state, event.now); break
    case 'stop_entries': state = { ...state, stage: 'finishing' }; break
  }
  return { state, candles, sequence: session.sequence + 1 }
}
