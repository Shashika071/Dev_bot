// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fixtureCandles } from '../lib/testFixtures'
import { evaluateAll } from '../lib/strategy/signalEngine'
import type { MarketState } from '../lib/deriv/marketFeed'
import type { Candle } from '../lib/types'

const fake = vi.hoisted(() => ({
  listener: null as null | ((state: MarketState, event?: { type: 'candle_close'; candle: Candle }) => void),
  starts: 0,
}))
vi.mock('../lib/deriv/marketFeed', () => ({
  SIGNAL_WINDOW_SECONDS: 5,
  MarketFeed: class {
    subscribe(fn: NonNullable<typeof fake.listener>) { fake.listener = fn; return () => {} }
    start() { fake.starts++ }
    stop() {}
  },
}))
import { useMarketAndSignals } from './useMarketAndSignals'

const candles = fixtureCandles(4000)
const signal = evaluateAll(candles).find((ev) => ev.isQualified)!
const index = candles.findIndex((c) => c.time === signal.time)
const state: MarketState = {
  status: 'CONNECTED', ready: true, symbol: 'R_100',
  candles: [...candles.slice(0, index + 1), { ...candles[index + 1], isClosed: false }],
  lastPrice: candles[index + 1].open, lastTickEpoch: signal.time + 61,
  lastConfirmedCandleTime: signal.time, error: null,
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime((signal.time + 61) * 1000)
  vi.stubEnv('VITE_SOUND_ALERTS', 'false')
  localStorage.clear()
  fake.starts = 0
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllEnvs() })

it('does not alert on bootstrap history, creates one paper entry on a live close, and expires it', () => {
  const { result } = renderHook(() => useMarketAndSignals())
  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
  expect(result.current.history).toHaveLength(0)
  act(() => fake.listener!(state, { type: 'candle_close', candle: candles[index] }))
  expect(result.current.liveSignal?.final).toBe(signal.final)
  expect(result.current.history).toHaveLength(1)
  expect(result.current.history[0].paper?.entryPrice).toBe(state.lastPrice)
  act(() => fake.listener!(state, { type: 'candle_close', candle: candles[index] }))
  expect(result.current.history).toHaveLength(1)
  act(() => result.current.setSound(true))
  expect(fake.starts).toBe(1)
  vi.setSystemTime((signal.time + 66) * 1000)
  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
  act(() => fake.listener!({ ...state, ready: false, status: 'RECONNECTING' }))
  expect(result.current.history[0].paper?.status).toBe('INVALID')
})
