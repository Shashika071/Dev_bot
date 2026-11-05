// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fixtureCandles } from '../lib/testFixtures'
import { enrich } from '../lib/strategy/signalEngine'
import { evaluateC5Index } from '../lib/strategy/c5Strategy'
import type { MarketState } from '../lib/deriv/marketFeed'
import type { Candle, SignalEvaluation } from '../lib/types'

const fake = vi.hoisted(() => ({
  listener: null as null | ((state: MarketState, event?: { type: 'candle_close'; candle: Candle }) => void),
  starts: 0,
  forced: null as SignalEvaluation | null,
}))

vi.mock('../lib/deriv/marketFeed', () => ({
  SIGNAL_WINDOW_SECONDS: 5,
  MarketFeed: class {
    subscribe(fn: NonNullable<typeof fake.listener>) { fake.listener = fn; return () => {} }
    start() { fake.starts++ }
    stop() {}
  },
}))

vi.mock('../lib/strategy/c5Strategy', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/strategy/c5Strategy')>()
  return {
    ...actual,
    evaluateC5LatestClosed: (candles: Candle[]) => {
      if (fake.forced && candles.some((c) => c.time === fake.forced!.time && c.isClosed)) {
        return fake.forced
      }
      return actual.evaluateC5LatestClosed(candles)
    },
    evaluateC5All: (candles: Candle[]) => {
      const base = actual.evaluateC5All(candles)
      if (fake.forced && !base.some((e) => e.time === fake.forced!.time)) {
        return [...base, fake.forced]
      }
      return base
    },
  }
})

import { useMarketAndSignals } from './useMarketAndSignals'

function buildForcedRise(): { candles: Candle[]; signal: SignalEvaluation } {
  const candles = fixtureCandles(120)
  const s = enrich(candles)
  const i = candles.length - 1
  s.utBuy[i] = true
  s.utSell[i] = false
  s.stDir[i] = 1
  s.sma20[i] = s.candles[i]!.close - 1
  s.smaRising[i] = true
  s.smaFalling[i] = false
  s.rsi[i] = 60
  s.ao[i] = -1
  s.ao[i - 1] = -2
  const signal = evaluateC5Index(s, i)
  return { candles, signal }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubEnv('VITE_SOUND_ALERTS', 'false')
  localStorage.clear()
  fake.starts = 0
  fake.forced = null
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllEnvs() })

it('does not alert on bootstrap history, alerts once on a live close, and expires it', () => {
  const { candles, signal } = buildForcedRise()
  expect(signal.isQualified).toBe(true)
  fake.forced = signal
  const i = candles.length - 1

  vi.setSystemTime((signal.time + 61) * 1000)
  const state: MarketState = {
    status: 'CONNECTED', ready: true, symbol: 'R_100',
    candles: [...candles.slice(0, i + 1), {
      ...candles[i]!,
      time: candles[i]!.time + 60,
      isClosed: false,
    }],
    lastPrice: candles[i]!.close,
    lastTickEpoch: signal.time + 61,
    lastConfirmedCandleTime: signal.time,
    error: null,
  }

  const trades: unknown[] = []
  const { result } = renderHook(() => useMarketAndSignals((sig) => { trades.push(sig) }))

  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
  expect(trades).toHaveLength(0)

  act(() => fake.listener!(state, { type: 'candle_close', candle: candles[i]! }))
  expect(result.current.liveSignal?.final).toBe('RISE')
  expect(result.current.liveSignal?.strategyId).toBe('C5')
  expect(trades).toHaveLength(1)

  act(() => fake.listener!(state, { type: 'candle_close', candle: candles[i]! }))
  expect(trades).toHaveLength(1)

  act(() => result.current.setSound(true))
  expect(fake.starts).toBe(1)

  vi.setSystemTime((signal.time + 66) * 1000)
  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
})

it('forming candles never emit trade signals', () => {
  const candles = fixtureCandles(80)
  candles[candles.length - 1]!.isClosed = false
  const state: MarketState = {
    status: 'CONNECTED', ready: true, symbol: 'R_100',
    candles,
    lastPrice: candles.at(-1)!.close,
    lastTickEpoch: candles.at(-1)!.time + 30,
    lastConfirmedCandleTime: candles[candles.length - 2]!.time,
    error: null,
  }
  vi.setSystemTime((candles.at(-1)!.time + 30) * 1000)
  const trades: unknown[] = []
  const { result } = renderHook(() => useMarketAndSignals((sig) => { trades.push(sig) }))
  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
  expect(trades).toHaveLength(0)
  expect(result.current.liveSetup.forming).toBe(true)
})
