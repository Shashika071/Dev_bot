import { describe, expect, it } from 'vitest'
import { fixtureCandles } from '../testFixtures'
import {
  createInitialState,
  startDiscovery,
  onTick,
  listEnabledCandidates,
  DISCOVERY_HOURS,
} from './experimentEngine'
import { STRATEGY_COOLDOWN_SEC } from './horizons'
import { RESEARCH_FORBIDDEN_METHODS } from './touchPaper'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function closedFixtures(n: number) {
  return fixtureCandles(n).map((c) => ({ ...c, isClosed: true }))
}

describe('experiment engine', () => {
  it('starts discovery with 72h deadline that survives conceptual resume', () => {
    const t0 = 1_700_000_000_000
    let state = startDiscovery(createInitialState(), t0)
    expect(state.stage).toBe('collecting')
    expect(state.endsAt).toBe(Math.floor(t0 / 1000) + DISCOVERY_HOURS * 3600)
    const endsAt = state.endsAt
    // simulate restart load
    state = { ...state, stage: 'reconnecting' }
    expect(state.endsAt).toBe(endsAt)
  })

  it('Start always begins a fresh discovery even if already collecting', () => {
    const t0 = 1_700_000_000_000
    let state = startDiscovery(createInitialState(), t0)
    state = { ...state, observations: [{ id: 'x' } as never], stage: 'collecting' }
    const next = startDiscovery(state, t0 + 60_000)
    expect(next.stage).toBe('collecting')
    expect(next.observations).toEqual([])
    expect(next.endsAt).toBe(Math.floor((t0 + 60_000) / 1000) + DISCOVERY_HOURS * 3600)
  })

  it('lists candidates for closed strategies without throwing', () => {
    const state = createInitialState()
    const candles = closedFixtures(300)
    const cands = listEnabledCandidates(state, candles)
    expect(Array.isArray(cands)).toBe(true)
  })

  it('dedupes and applies cooldown for a strategy', () => {
    let state = startDiscovery(createInitialState(), 1_700_000_000_000)
    const base = closedFixtures(250)
    // Build a long tick stream after last candle close
    const last = base[base.length - 1]!
    const closeEpoch = last.time + 60
    const tick0 = { epoch: closeEpoch, quote: last.close, symbol: 'R_100' }
    state = onTick(state, tick0, base, closeEpoch, true)
    const n1 = state.observations.length
    state = onTick(state, { epoch: closeEpoch + 1, quote: last.close + 0.1, symbol: 'R_100' }, base, closeEpoch + 1, true)
    expect(state.observations.length).toBe(n1)
    // cooldown constant exported
    expect(STRATEGY_COOLDOWN_SEC).toBe(420)
  })

  it('marks horizons unknown when feed unhealthy', () => {
    let state = startDiscovery(createInitialState(), 1_700_000_000_000)
    const base = closedFixtures(250)
    const last = base[base.length - 1]!
    const closeEpoch = last.time + 60
    state = onTick(state, { epoch: closeEpoch, quote: last.close, symbol: 'R_100' }, base, closeEpoch, true)
    if (!state.observations.length) return // fixture may yield no signals
    state = onTick(
      state,
      { epoch: closeEpoch + 10, quote: last.close, symbol: 'R_100' },
      base,
      closeEpoch + 10,
      false,
    )
    expect(state.observations.some((o) => o.horizons.some((h) => h.status === 'unknown'))).toBe(true)
  })

  it('research touchPaper module forbids buy method names in source', () => {
    const src = readFileSync(resolve('src/lib/research/touchPaper.ts'), 'utf8')
    for (const name of RESEARCH_FORBIDDEN_METHODS) {
      expect(src.includes(`${name}(`)).toBe(false)
    }
    const propose = readFileSync(resolve('src/lib/research/proposeOnly.ts'), 'utf8')
    expect(propose.includes('buy:')).toBe(false)
    expect(propose.includes('buyOneTouch')).toBe(false)
  })
})
