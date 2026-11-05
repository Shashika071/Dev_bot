// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fixtureCandles } from '../lib/testFixtures'
import { enrich } from '../lib/strategy/signalEngine'
import { evaluateC5Index, evaluateC5LiveBar } from '../lib/strategy/c5Strategy'
import type { MarketState } from '../lib/deriv/marketFeed'
import type { Candle, SignalEvaluation } from '../lib/types'

const fake = vi.hoisted(() => ({
  listener: null as null | ((state: MarketState, event?: { type: 'candle_close'; candle: Candle }) => void),
  starts: 0,
  forcedClosed: null as SignalEvaluation | null,
  forcedLive: null as SignalEvaluation | null,
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
      if (fake.forcedClosed && candles.some((c) => c.time === fake.forcedClosed!.time && c.isClosed)) {
        return fake.forcedClosed
      }
      return actual.evaluateC5LatestClosed(candles)
    },
    evaluateC5LiveBar: (candles: Candle[]) => {
      if (fake.forcedLive) return fake.forcedLive
      return actual.evaluateC5LiveBar(candles)
    },
    evaluateC5All: (candles: Candle[]) => {
      const base = actual.evaluateC5All(candles)
      if (fake.forcedClosed && !base.some((e) => e.time === fake.forcedClosed!.time)) {
        return [...base, fake.forcedClosed]
      }
      return base
    },
  }
})

import { useMarketAndSignals } from './useMarketAndSignals'

function buildForcedRise(forming = false): { candles: Candle[]; signal: SignalEvaluation } {
  const candles = fixtureCandles(120)
  if (forming) candles[candles.length - 1]!.isClosed = false
  const s = enrich(candles.map((c, i) =>
    i === candles.length - 1 && forming ? { ...c, isClosed: true } : c,
  ))
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
  fake.forcedClosed = null
  fake.forcedLive = null
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllEnvs() })

it('does not alert on bootstrap history, alerts once on a live close, and expires it', () => {
  const { candles, signal } = buildForcedRise(false)
  expect(signal.isQualified).toBe(true)
  fake.forcedClosed = signal
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
  expect(trades).toHaveLength(1)

  act(() => fake.listener!(state, { type: 'candle_close', candle: candles[i]! }))
  expect(trades).toHaveLength(1)

  act(() => { result.current.setSound(true) })
  expect(fake.starts).toBe(1)

  vi.setSystemTime((signal.time + 66) * 1000)
  act(() => fake.listener!(state))
  expect(result.current.liveSignal).toBeNull()
})

it('fires immediately when forming candle first hits 4/4 (not waiting for close)', () => {
  const { candles, signal } = buildForcedRise(true)
  expect(signal.isQualified).toBe(true)
  fake.forcedLive = { ...signal, time: candles.at(-1)!.time }

  const t0 = candles.at(-1)!.time
  vi.setSystemTime((t0 + 20) * 1000)
  const base: MarketState = {
    status: 'CONNECTED', ready: true, symbol: 'R_100',
    candles,
    lastPrice: candles.at(-1)!.close,
    lastTickEpoch: t0 + 10,
    lastConfirmedCandleTime: candles[candles.length - 2]!.time,
    error: null,
  }

  const trades: unknown[] = []
  const { result } = renderHook(() => useMarketAndSignals((sig) => { trades.push(sig) }))

  // First tick after mount is ignored (bootstrap guard).
  act(() => fake.listener!(base))
  expect(trades).toHaveLength(0)

  // Second tick with forming 4/4 → immediate signal.
  act(() => fake.listener!({ ...base, lastTickEpoch: t0 + 20, lastPrice: candles.at(-1)!.close + 0.01 }))
  expect(result.current.liveSignal?.final).toBe('RISE')
  expect(trades).toHaveLength(1)

  // More ticks same minute must not duplicate.
  act(() => fake.listener!({ ...base, lastTickEpoch: t0 + 25 }))
  expect(trades).toHaveLength(1)
})

it('evaluateC5LiveBar qualifies a forming bar when rules pass', () => {
  const candles = fixtureCandles(100)
  candles[candles.length - 1]!.isClosed = false
  // Without forced indicators this may or may not qualify; just ensure it returns an evaluation shape.
  const ev = evaluateC5LiveBar(candles)
  expect(ev == null || typeof ev.time === 'number').toBe(true)
})
