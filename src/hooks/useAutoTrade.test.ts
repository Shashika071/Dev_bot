// @vitest-environment jsdom
import { StrictMode, createElement, type ReactNode } from 'react'
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { Candle, ConnectionStatus } from '../lib/types'
import type { MarketState } from '../lib/deriv/marketFeed'

const fake = vi.hoisted(() => ({
  listAccounts: vi.fn(),
  request: vi.fn(),
  clients: [] as { connectionStatus: ConnectionStatus; close: () => void }[],
  tradingListeners: [] as Array<(msg: Record<string, unknown>) => void>,
  feedListener: null as null | ((state: MarketState, event?: { type: 'candle_close'; candle: Candle }) => void),
}))

vi.mock('../lib/deriv/tradingAuth', () => ({ listDerivAccounts: fake.listAccounts }))
vi.mock('../lib/deriv/marketFeed', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/deriv/marketFeed')>(),
  MarketFeed: class {
    subscribe(fn: NonNullable<typeof fake.feedListener>) { fake.feedListener = fn; return () => {} }
    start() {}
    stop() {}
  },
}))
vi.mock('../lib/deriv/tradingClient', () => ({
  DerivTradingClient: class {
    connectionStatus: ConnectionStatus = 'DISCONNECTED'
    statusHandler: (s: ConnectionStatus) => void = () => {}
    constructor() { fake.clients.push(this) }
    setStatusHandler(fn: (s: ConnectionStatus) => void) { this.statusHandler = fn }
    subscribe(fn: (msg: Record<string, unknown>) => void) {
      fake.tradingListeners.push(fn)
      return () => {}
    }
    async connect() { this.connectionStatus = 'CONNECTED'; this.statusHandler('CONNECTED') }
    close() { this.connectionStatus = 'DISCONNECTED'; this.statusHandler('DISCONNECTED') }
    request = fake.request
  },
}))

import { exportTradeHistoryCsv, useAutoTrade, type AutoTradeSignal } from './useAutoTrade'
import { useMarketAndSignals } from './useMarketAndSignals'
import { fixtureCandles } from '../lib/testFixtures'
import { evaluateC5All, C5_CALC_WINDOW } from '../lib/strategy/c5Strategy'
import { enrich } from '../lib/strategy/signalEngine'
import { evalMask, MASKS } from '../lib/research/strategies/maskStrategies'
import { buildResearchExtras } from '../lib/research/strategies/buildExtras'

const now = 1_800_000_061
const key = 'v100_auto_trade_v2_c5'
function savePrefs(overrides: Record<string, unknown> = {}) {
  localStorage.setItem(key, JSON.stringify({
    appId: 'test-app', token: 'test-only-token', accountId: 'demo-test',
    autoEnabled: true, wantConnected: true, useFullStake: false,
    stake: 1, cooldownSec: 0, currency: 'USD', ...overrides,
  }))
}
function signal(overrides: Partial<AutoTradeSignal> = {}): AutoTradeSignal {
  return {
    strategyId: 'C5', side: 'RISE', symbol: 'R_100',
    candleTime: now - 61, time: now - 61, candleCloseEpoch: now - 1,
    expiresAt: now + 4, isQualified: true, requiredPassed: 4, requiredTotal: 4,
    flags: { ut: true, supertrend: true, sma: true, rsi: true, ao: false },
    confidence: 1, feedReady: true,
    indicators: { close: 100, smaRising: true, smaFalling: false, ao: -1, rsi: 60, stDir: 1, rawUt: 'BUY' },
    ...overrides,
  }
}
async function flush() { await act(async () => {}) }
const buys = () => fake.request.mock.calls.filter(([p]) => 'buy' in p)

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(now * 1000)
  localStorage.clear()
  fake.clients = []
  fake.tradingListeners = []
  fake.feedListener = null
  fake.listAccounts.mockReset().mockResolvedValue([{ account_id: 'demo-test', account_type: 'demo', currency: 'USD' }])
  fake.request.mockReset().mockImplementation(async (p: Record<string, unknown>) => {
    if ('balance' in p) return { balance: { balance: 20, currency: 'USD' } }
    if ('proposal' in p) return { proposal: { id: 'test-proposal', ask_price: 1 } }
    if ('buy' in p) return { buy: { contract_id: 42, buy_price: 1, payout: 1.1, entry_spot: 100.25 } }
    return {}
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

it('restores the saved connection under StrictMode and sends one C5 auto order', async () => {
  savePrefs()
  const { result } = renderHook(() => useAutoTrade('R_100'), {
    wrapper: ({ children }: { children: ReactNode }) => createElement(StrictMode, null, children),
  })
  await flush()
  expect(result.current.status).toBe('CONNECTED')
  await act(async () => { await result.current.onTradeSignal(signal()) })
  expect(buys()).toHaveLength(1)
  expect(result.current.log.some((r) => r.contractId === 42)).toBe(true)
  expect(result.current.log.find((r) => r.contractId === 42)?.candleTime).toBe(now - 61)
  expect(result.current.log.some((r) => /C5 signal received/.test(r.message) && r.candleTime === now - 61)).toBe(true)
  await act(async () => { await result.current.onTradeSignal(signal()) })
  expect(buys()).toHaveLength(1)
})

it('restores the fallback account when the saved account no longer exists', async () => {
  savePrefs({ accountId: 'old-demo' })
  const { result } = renderHook(() => useAutoTrade('R_100'))
  await flush()
  expect(result.current.accountId).toBe('demo-test')
  expect(result.current.status).toBe('CONNECTED')
  await act(async () => { await result.current.onTradeSignal(signal()) })
  expect(buys()).toHaveLength(1)
})

it('recovers a mocked nine-minute manual purchase after refresh and emits settlement once', async () => {
  savePrefs({
    autoEnabled: false,
    wantConnected: true,
    barrierAbs: 0.01,
    duration: 9,
    durationUnit: 'm',
  })
  const first = renderHook(() => useAutoTrade('R_100'))
  await flush()

  let fills: Awaited<ReturnType<typeof first.result.current.placeManualTrade>> = []
  await act(async () => {
    fills = await first.result.current.placeManualTrade('FALL', 'attempt-9m')
  })
  expect(fills).toHaveLength(1)
  expect(fills[0]).toMatchObject({
    contractId: 42,
    accountId: 'demo-test',
    currency: 'USD',
    entrySpot: 100.25,
  })
  const proposal = fake.request.mock.calls
    .map(([payload]) => payload as Record<string, unknown>)
    .find((payload) => 'proposal' in payload)
  expect(proposal).toMatchObject({ barrier: '-0.01', duration: 9, duration_unit: 'm' })
  const receiptKey = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i))
    .find((storedKey) => storedKey?.startsWith('v100_contract_v1:'))
  expect(receiptKey).toBeTruthy()

  first.unmount()
  const refreshed = renderHook(() => useAutoTrade('R_100'))
  await flush()
  const settlements: Array<{ contractId: number; attemptId: string; profit: number | null }> = []
  act(() => {
    refreshed.result.current.setOnSettlement((row) => settlements.push(row))
  })
  const listener = fake.tradingListeners.at(-1)!
  const terminal = {
    msg_type: 'proposal_open_contract',
    proposal_open_contract: {
      contract_id: 42,
      is_sold: 1,
      status: 'won',
      entry_spot: 100.25,
      barrier: 100.24,
      buy_price: 1,
      payout: 1.8,
      profit: 0.8,
    },
  }
  act(() => listener(terminal))
  act(() => listener(terminal))

  expect(settlements).toHaveLength(1)
  expect(settlements[0]).toMatchObject({
    contractId: 42,
    attemptId: 'attempt-9m',
    profit: 0.8,
  })
  expect(refreshed.result.current.log.find((row) => row.contractId === 42)).toMatchObject({
    settled: true,
    profit: 0.8,
    entrySpot: 100.25,
  })
  act(() => refreshed.result.current.acknowledgeSettlement('demo-test', 42))
  expect(localStorage.getItem(receiptKey!)).toBeNull()
})

it('records a new C5 signal skipped while another order is in progress', async () => {
  savePrefs()
  const { result } = renderHook(() => useAutoTrade('R_100'))
  await flush()
  let finishQuote!: (value: unknown) => void
  fake.request.mockImplementationOnce(() => new Promise((resolve) => { finishQuote = resolve }))
  let pending!: Promise<void>
  act(() => { pending = result.current.onTradeSignal(signal()) })
  await act(async () => {
    await result.current.onTradeSignal(signal({ candleTime: now - 1, time: now - 1 }))
  })
  const recorded = result.current.log.some((r) => !r.ok && r.message.includes('trade already in progress'))
  await act(async () => { finishQuote({ proposal: { id: 'first', ask_price: 1 } }); await pending })
  expect(recorded).toBe(true)
  expect(buys()).toHaveLength(1)
})

it.each([
  ['expired', { expiresAt: now - 1 }, {}, /expired/],
  ['late tick', { receivedTickEpoch: now + 5 }, {}, /expired/],
  ['Auto off', {}, { autoEnabled: false }, /Auto trade OFF/],
  ['not ready', { feedReady: false }, {}, /feed not READY/],
  ['disconnected', {}, { wantConnected: false }, /socket not connected/],
  ['cooldown', {}, { cooldownUntil: (now + 60) * 1000 }, /cooldown/],
] as const)('records %s without sending a buy', async (_, changes, prefs, message) => {
  savePrefs(prefs)
  const { result } = renderHook(() => useAutoTrade('R_100'))
  await flush()
  await act(async () => { await result.current.onTradeSignal(signal(changes)) })
  expect(buys()).toHaveLength(0)
  expect(result.current.log.some((r) => !r.ok && message.test(r.message))).toBe(true)
  const saved = JSON.parse(localStorage.getItem('v100_trade_history_v1')!)
  expect(saved.some((r: { candleTime?: number }) => r.candleTime === now - 61)).toBe(true)
})

it('exports the signal candle separately from the order/log time', () => {
  const csv = exportTradeHistoryCsv([{
    id: 'test', at: now * 1000, candleTime: now - 61,
    signal: 'RISE', barrier: '—', ok: false, message: 'C5 skipped · expired',
  }])
  expect(csv).toContain('signal_candle_utc')
  expect(csv).toContain(new Date((now - 61) * 1000).toISOString())
  expect(csv).toContain(new Date(now * 1000).toISOString())
})

it('does not buy if Auto is switched off while the proposal is pending', async () => {
  savePrefs()
  const { result } = renderHook(() => useAutoTrade('R_100'))
  await flush()
  let finishQuote!: (value: unknown) => void
  fake.request.mockImplementationOnce(() => new Promise((resolve) => { finishQuote = resolve }))
  let pending!: Promise<void>
  act(() => { pending = result.current.onTradeSignal(signal()) })
  act(() => { result.current.setAutoEnabled(false) })
  await act(async () => { finishQuote({ proposal: { id: 'late', ask_price: 1 } }); await pending })
  expect(buys()).toHaveLength(0)
  expect(result.current.log.some((r) => !r.ok && /Auto trade OFF/.test(r.message))).toBe(true)
})

it.each(['balance', 'proposal'] as const)('logs expiry during %s preparation and releases cooldown', async (stage) => {
  savePrefs({ useFullStake: stage === 'balance', cooldownSec: 420 })
  const { result } = renderHook(() => useAutoTrade('R_100'))
  await flush()
  fake.request.mockImplementationOnce(async () => {
    vi.setSystemTime((now + 6) * 1000)
    return stage === 'balance'
      ? { balance: { balance: 20, currency: 'USD' } }
      : { proposal: { id: 'expired', ask_price: 1 } }
  })
  await act(async () => { await result.current.onTradeSignal(signal()) })
  expect(buys()).toHaveLength(0)
  expect(result.current.log.some((r) => !r.ok && /expired/.test(r.message))).toBe(true)
  expect(result.current.cooldownUntil).toBe(0)
})

it.each([
  { delay: 1, balanceDelay: 0, quoteDelay: 0 },
  { delay: 9, balanceDelay: 0, quoteDelay: 0 },
  { delay: 10, balanceDelay: 0, quoteDelay: 0 },
  { delay: 11, balanceDelay: 0, quoteDelay: 0 },
  { delay: 1, balanceDelay: 3, quoteDelay: 4 },
  { delay: 1, balanceDelay: 4, quoteDelay: 6 },
])('connects real C5 chart/paper detection to an order or logged skip: %j', async ({ delay, balanceDelay, quoteDelay }) => {
  const history = fixtureCandles(1000)
  const candidate = evaluateC5All(history).find((ev) => ev.isQualified && ev.time > history[120]!.time)!
  expect(candidate).toBeDefined()
  const candles = history.filter((c) => c.time <= candidate.time)
  const window = candles.slice(-C5_CALC_WINDOW)
  const paper = evalMask({
    candles: window, closedCount: window.length, index: window.length - 1,
    series: enrich(window), extras: buildResearchExtras(window),
    feedHealthy: true, nowEpoch: candidate.time + 61,
  }, MASKS.C5!, 'C5')
  expect(paper?.side).toBe(candidate.final)

  vi.setSystemTime((candidate.time + 60 + delay) * 1000)
  savePrefs({ useFullStake: true })
  const { result } = renderHook(() => {
    const trade = useAutoTrade('R_100')
    const market = useMarketAndSignals(trade.onTradeSignal)
    return { trade, market }
  })
  await flush()
  const respond = fake.request.getMockImplementation()!
  const buyTimes: number[] = []
  fake.request.mockImplementation(async (payload: Record<string, unknown>) => {
    if ('balance' in payload) vi.setSystemTime(Date.now() + balanceDelay * 1000)
    if ('proposal' in payload) vi.setSystemTime(Date.now() + quoteDelay * 1000)
    if ('buy' in payload) buyTimes.push(Date.now() / 1000)
    return respond(payload)
  })
  const state: MarketState = {
    status: 'CONNECTED', ready: true, symbol: 'R_100', candles,
    lastPrice: candles.at(-1)!.close, lastTickEpoch: candidate.time + 61,
    lastConfirmedCandleTime: candidate.time, error: null,
  }
  // Loading the same marker as historical data must never create an order.
  act(() => fake.feedListener!(state))
  expect(buys()).toHaveLength(0)
  expect(result.current.trade.log).toHaveLength(0)

  await act(async () => {
    fake.feedListener!(state, { type: 'candle_close', candle: candles.at(-1)! })
  })
  expect(result.current.market.evaluations.find((ev) => ev.time === candidate.time)?.final).toBe(paper?.side)
  const records = result.current.trade.log.filter((row) => row.candleTime === candidate.time)
  expect(records.some((row) => /C5 signal received/.test(row.message))).toBe(true)
  const withinWindow = delay + balanceDelay + quoteDelay <= 10
  if (withinWindow) {
    expect(buys()).toHaveLength(1)
    expect(records.some((row) => row.contractId === 42)).toBe(true)
    expect(buyTimes[0]).toBeLessThanOrEqual(candidate.time + 70)
  } else {
    expect(buys()).toHaveLength(0)
    expect(records.some((row) => !row.ok && /expired/.test(row.message))).toBe(true)
    if (delay > 10) expect(result.current.market.liveSignal).toBeNull()
  }
  await act(async () => {
    fake.feedListener!(state, { type: 'candle_close', candle: candles.at(-1)! })
  })
  expect(buys()).toHaveLength(withinWindow ? 1 : 0)
})
