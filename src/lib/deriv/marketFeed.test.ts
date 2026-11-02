import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketFeed, type FeedClient } from './marketFeed'
import type { ConnectionStatus } from '../types'
import type { DerivMessageHandler } from './websocket'
import { fixtureCandles } from '../testFixtures'

class FakeClient implements FeedClient {
  statusHandler: (s: ConnectionStatus) => void = () => {}
  handler: DerivMessageHandler = () => {}
  requests: Record<string, unknown>[] = []
  reconnects = 0
  setStatusHandler(fn: (s: ConnectionStatus) => void) { this.statusHandler = fn }
  subscribe(fn: DerivMessageHandler) { this.handler = fn }
  connect() { this.statusHandler('CONNECTED') }
  close() { this.statusHandler('DISCONNECTED') }
  reconnect() { this.reconnects++; this.close(); this.connect() }
  send(payload: Record<string, unknown>) { this.requests.push(payload); return this.requests.length }
}

const minute = 1_800_000_000
let client: FakeClient
let feed: MarketFeed
let closes: number[]
function tick(seconds: number, price = 1000) {
  vi.setSystemTime((minute + seconds) * 1000)
  client.handler({ tick: { epoch: minute + seconds, quote: price, symbol: 'R_100' } })
}
function history(lastMinute = minute, reqId = client.requests.length) {
  const candles = fixtureCandles(120, lastMinute - 119 * 60).map((c) => ({ ...c, epoch: c.time }))
  client.handler({ req_id: reqId, candles })
}
function ready() { feed.start(); tick(55); history() }

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(minute * 1000)
  client = new FakeClient()
  feed = new MarketFeed(client)
  closes = []
  feed.subscribe((_, event) => { if (event) closes.push(event.candle.time) })
})
afterEach(() => { feed.stop(); vi.useRealTimers() })

describe('history synchronization and signal timing', () => {
  it('buffers ticks until matching history, replays once without alerts', () => {
    feed.start()
    tick(55, 1001)
    expect(feed.getSnapshot().ready).toBe(false)
    expect(client.requests.at(-1)?.end).toBe(String(minute + 54))
    tick(58, 1002)
    history(minute, 999)
    expect(feed.getSnapshot().ready).toBe(false)
    history()
    expect(feed.getSnapshot().ready).toBe(true)
    expect(feed.getSnapshot().lastPrice).toBe(1002)
    expect(feed.getSnapshot().candles.at(-1)?.close).toBe(1002)
    expect(closes).toEqual([])
    tick(61, 1003)
    expect(closes).toEqual([minute])
  })
  it('does not replay a close that happens while history is loading', () => {
    feed.start(); tick(55); tick(59); tick(61); history()
    expect(feed.getSnapshot().ready).toBe(true)
    expect(closes).toEqual([])
  })
  it('ignores duplicate, out-of-order, wrong-symbol, and malformed ticks', () => {
    ready(); tick(58, 1001)
    const before = feed.getSnapshot()
    client.handler({ tick: { epoch: minute + 57, quote: 999, symbol: 'R_100' } })
    client.handler({ tick: { epoch: minute + 58, quote: 1001, symbol: 'R_100' } })
    client.handler({ tick: { epoch: minute + 59, quote: 777, symbol: 'OTHER' } })
    client.handler({ tick: { epoch: minute + 59, quote: NaN, symbol: 'R_100' } })
    expect(feed.getSnapshot()).toBe(before)
  })
  it('rejects delayed ticks without changing the displayed price', () => {
    ready()
    vi.setSystemTime((minute + 100) * 1000)
    client.handler({ tick: { epoch: minute + 58, quote: 9999 } })
    expect(feed.getSnapshot().ready).toBe(false)
    expect(feed.getSnapshot().lastPrice).toBe(1000)
    expect(closes).toEqual([])
  })
  it('recovers history after a tick gap and never alerts on a stale candle', () => {
    ready(); tick(121)
    expect(feed.getSnapshot().ready).toBe(false)
    expect(closes).toEqual([])
    history(minute + 120)
    expect(feed.getSnapshot().ready).toBe(true)
    expect(closes).toEqual([])
  })
  it('suppresses an otherwise valid close received outside the five-second window', () => {
    ready(); tick(59); tick(66)
    expect(feed.getSnapshot().ready).toBe(true)
    expect(closes).toEqual([])
  })
  it('also checks delivery time, not just the timestamp inside the tick', () => {
    ready(); tick(58)
    vi.setSystemTime((minute + 69) * 1000)
    client.handler({ tick: { epoch: minute + 61, quote: 1001 } })
    expect(closes).toEqual([])
  })
  it('blocks on disconnect and ignores old history after reconnect', () => {
    ready()
    const oldId = client.requests.length
    client.reconnect()
    expect(feed.getSnapshot().ready).toBe(false)
    tick(58); history(minute, oldId)
    expect(feed.getSnapshot().ready).toBe(false)
    history()
    expect(feed.getSnapshot().ready).toBe(true)
    expect(closes).toEqual([])
  })
  it('rejects malformed OHLC and missing history candles', () => {
    feed.start(); tick(55)
    const candles = fixtureCandles(120, minute - 119 * 60).map((c) => ({ ...c, epoch: c.time }))
    candles[50].high = NaN
    client.handler({ req_id: client.requests.length, candles })
    expect(feed.getSnapshot().ready).toBe(false)
    expect(feed.getSnapshot().error).toMatch(/Invalid|Recovering/)
  })
  it('retries history after a bad page instead of staying stuck', () => {
    feed.start()
    tick(55)
    const bad = fixtureCandles(120, minute - 119 * 60).map((c) => ({ ...c, epoch: c.time }))
    bad[50].high = NaN
    const badId = client.requests.length
    client.handler({ req_id: badId, candles: bad })
    expect(feed.getSnapshot().ready).toBe(false)
    // Recovery schedules a fresh history request from the latest tick.
    expect(client.requests.length).toBeGreaterThan(badId)
    history(minute)
    expect(feed.getSnapshot().ready).toBe(true)
    expect(feed.getSnapshot().error).toBeNull()
  })
  it('does not re-sync forever after history loads when signals are still warming up', () => {
    feed.start()
    tick(55)
    history()
    const afterSync = client.requests.length
    expect(feed.getSnapshot().candles.length).toBeGreaterThanOrEqual(120)
    tick(56)
    tick(57)
    tick(58)
    // Live ticks must not spam new history requests once candles are loaded.
    expect(client.requests.length).toBe(afterSync)
    expect(feed.getSnapshot().candles.length).toBeGreaterThan(0)
    expect(feed.getSnapshot().lastPrice).not.toBeNull()
  })
  it('accepts a page with a mid-history gap by using the longest consecutive streak', () => {
    feed.start()
    tick(55)
    const rows = fixtureCandles(200, minute - 199 * 60).map((c) => ({ ...c, epoch: c.time }))
    // Punch a gap near the end so a trailing-only strategy would fail.
    rows.splice(180, 1)
    client.handler({ req_id: client.requests.length, candles: rows })
    expect(feed.getSnapshot().candles.length).toBeGreaterThanOrEqual(120)
    expect(feed.getSnapshot().lastPrice).not.toBeNull()
  })
  it('detects silence and reconnects even if the socket stays open', () => {
    ready()
    vi.advanceTimersByTime(11000)
    expect(feed.getSnapshot().ready).toBe(false)
    vi.advanceTimersByTime(5000)
    expect(client.reconnects).toBe(1)
  })
})
