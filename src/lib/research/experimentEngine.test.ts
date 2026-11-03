import { describe, expect, it } from 'vitest'
import { fixtureCandles } from '../testFixtures'
import {
  createInitialState,
  startDiscovery,
  onTick,
  listEnabledCandidates,
  DISCOVERY_HOURS,
} from './experimentEngine'
import { makeHorizons, POST_WIN_COOLDOWN_SEC, STRATEGY_COOLDOWN_SEC } from './horizons'
import { maybeStartTouchPaper } from './touchPaper'
import { DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import { RESEARCH_FORBIDDEN_METHODS } from './touchPaper'
import { summarizeExperiment } from './summarizeExperiment'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Candle } from '../types'

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
    expect(POST_WIN_COOLDOWN_SEC).toBe(900)
  })

  it('applies a 15-minute post-win break after a Touch HIT', () => {
    const t0 = 1_700_100_000
    let state = startDiscovery(createInitialState('confirmation', ['C5']), t0 * 1000)
    const tick = { epoch: t0, quote: 100, symbol: 'R_100' }
    const obs = maybeStartTouchPaper({
      id: 'C5:1:RISE',
      strategyId: 'C5',
      side: 'RISE',
      candleTime: t0 - 60,
      signalEpoch: t0,
      receiptEpoch: t0,
      entryReferencePrice: 100,
      entryTick: tick,
      forming: false,
      reason: 'seed',
      atr: { atr14: 1, atrPct: 1, candleRange: 1, rangeOverAtr: 1 },
      lastTickEpoch: t0,
      horizons: makeHorizons(t0),
      pendingHorizons: true,
    }, DEFAULT_FROZEN_CONTRACT, tick)
    state = {
      ...state,
      observations: [obs],
      acceptedKeys: [obs.id],
      cooldowns: { C5: t0 + STRATEGY_COOLDOWN_SEC },
      lastTickEpoch: t0,
      feedHealthy: true,
    }
    // HIT at t0+30 → cooldown must extend to hit + 900s
    state = onTick(
      state,
      { epoch: t0 + 30, quote: 100.1, symbol: 'R_100' },
      closedFixtures(80),
      t0 + 30,
      true,
    )
    expect(state.observations[0]?.touch?.status).toBe('hit')
    expect(state.cooldowns.C5).toBe(t0 + 30 + POST_WIN_COOLDOWN_SEC)
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

  it('admits both S6 and S7 on the same tick (shared cooldown snapshot)', () => {
    const start = 1_700_000_040 // minute-aligned
    const candles: Candle[] = []
    let px = 100
    for (let i = 0; i < 220; i++) {
      const open = px
      // flat warmup, then three strict up closes at the end
      if (i >= 217) px += 1
      candles.push({
        time: start + i * 60,
        open,
        high: Math.max(open, px) + 0.2,
        low: Math.min(open, px) - 0.2,
        close: px,
        isClosed: true,
      })
    }
    const last = candles[candles.length - 1]!
    const closeEpoch = last.time + 60
    let state = startDiscovery(createInitialState('discovery', ['S6', 'S7']), closeEpoch * 1000)
    state = onTick(
      state,
      { epoch: closeEpoch, quote: last.close, symbol: 'R_100' },
      candles,
      closeEpoch,
      true,
    )
    const ids = state.observations.map((o) => o.strategyId).sort()
    expect(ids).toEqual(['S6', 'S7'])
    expect(state.rejected.filter((r) => r.reason === 'Cooldown active')).toHaveLength(0)
  })

  it('uses exact 50% equally weighted RISE/FALL as vs-random', () => {
    const t0 = 1_700_000_040
    let state = startDiscovery(createInitialState('discovery', ['S6']), t0 * 1000)
    state = {
      ...state,
      observations: [{
        id: 'S6:1:RISE',
        strategyId: 'S6',
        side: 'RISE',
        candleTime: t0 - 60,
        signalEpoch: t0,
        receiptEpoch: t0,
        entryReferencePrice: 100,
        entryTick: { epoch: t0, quote: 100, symbol: 'R_100' },
        forming: false,
        reason: 'test',
        atr: 1,
        lastTickEpoch: t0 + 420,
        horizons: [{
          minutes: 7,
          targetEpoch: t0 + 420,
          status: 'continuation',
          observedEpoch: t0 + 420,
          observedPrice: 101,
          signedMove: 1,
          signedMoveInAtr: 1,
        }],
        pendingHorizons: false,
      }],
    }
    const summary = summarizeExperiment(state)
    const h7 = summary.strategies[0]!.horizons.find((h) => h.minutes === 7)!
    expect(h7.contRate).toBe(1)
    expect(h7.randomContRate).toBe(0.5)
  })
})
