import type { Candle, ConnectionStatus, Tick } from '../types'
import { applyTick, candlesFromHistory, finalizeByClock, minuteBucket } from './candles'
import { DerivPublicClient, type DerivMessageHandler } from './websocket'
import { evaluateLatestClosed } from '../strategy/signalEngine'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'
const configuredHistory = Number(import.meta.env.VITE_HISTORY_COUNT || 5000)
const HISTORY = Number.isFinite(configuredHistory) ? Math.max(120, Math.min(5000, Math.floor(configuredHistory))) : 5000
export const SIGNAL_WINDOW_SECONDS = 5
export const MAX_TICK_GAP_SECONDS = 10

export interface MarketState {
  status: ConnectionStatus
  ready: boolean
  symbol: string
  candles: Candle[]
  lastPrice: number | null
  lastTickEpoch: number | null
  lastConfirmedCandleTime: number | null
  error: string | null
}

export interface FeedClient {
  setStatusHandler(fn: (s: ConnectionStatus) => void): void
  subscribe(fn: DerivMessageHandler): unknown
  connect(): void
  close(): void
  reconnect(): void
  send(payload: Record<string, unknown>): number | null
}

type Listener = (state: MarketState, event?: { type: 'candle_close'; candle: Candle }) => void

/** History ends before the first buffered tick, so replay cannot overwrite newer prices. */
export class MarketFeed {
  private client: FeedClient
  private now: () => number
  private listeners = new Set<Listener>()
  private state: MarketState = {
    status: 'DISCONNECTED', ready: false, symbol: SYMBOL, candles: [],
    lastPrice: null, lastTickEpoch: null, lastConfirmedCandleTime: null, error: null,
  }
  private historyId: number | null = null
  private historyEnd = 0
  private buffered: Tick[] = []
  private syncing = true
  private lastTick: Tick | null = null
  private lastReceipt = 0
  private syncStarted = 0
  private watchdog: ReturnType<typeof setInterval> | null = null

  constructor(client: FeedClient = new DerivPublicClient(), now = Date.now) {
    this.client = client
    this.now = now
    client.setStatusHandler((status) => {
      this.syncing = true
      this.historyId = null
      this.buffered = []
      this.lastTick = null
      this.syncStarted = this.now()
      this.lastReceipt = this.now()
      this.state = { ...this.state, status, ready: false, error: 'Waiting for synchronized history and fresh ticks' }
      if (status === 'CONNECTED') client.send({ ticks: SYMBOL, subscribe: 1 })
      this.emit()
    })
    client.subscribe((msg) => this.onMessage(msg))
  }

  getSnapshot(): MarketState { return this.state }

  subscribe(fn: Listener) {
    this.listeners.add(fn)
    fn(this.state)
    return () => this.listeners.delete(fn)
  }

  start() {
    if (SYMBOL !== 'R_100') {
      this.state = { ...this.state, error: `This strategy requires R_100, not ${SYMBOL}.`, ready: false }
      this.emit()
      return
    }
    if (!this.watchdog) this.watchdog = setInterval(() => this.checkHealth(), 1000)
    this.client.connect()
  }

  stop() {
    if (this.watchdog) clearInterval(this.watchdog)
    this.watchdog = null
    this.client.close()
  }

  private emit(event?: { type: 'candle_close'; candle: Candle }) {
    for (const listener of this.listeners) listener(this.state, event)
  }

  private checkHealth() {
    if (this.state.status !== 'CONNECTED') return
    const age = this.now() - this.lastReceipt
    if (age > MAX_TICK_GAP_SECONDS * 1000) {
      this.state = { ...this.state, ready: false, error: 'Feed stale — signals paused' }
    }
    this.emit()
    if (age > 15000 || (this.syncing && this.now() - this.syncStarted > 15000)) {
      this.client.reconnect()
    }
  }

  private beginSync(tick: Tick) {
    this.syncing = true
    this.syncStarted = this.now()
    this.buffered = [tick]
    this.historyEnd = tick.epoch - 1
    this.state = { ...this.state, ready: false, error: 'Recovering candle history — signals paused' }
    this.historyId = this.client.send({
      ticks_history: SYMBOL, style: 'candles', granularity: 60,
      count: HISTORY, end: String(this.historyEnd),
    })
    this.emit()
  }

  private onMessage(msg: Record<string, unknown>) {
    if (msg.error) {
      const err = msg.error as { message?: string }
      this.state = { ...this.state, ready: false, error: err.message || 'Market data request failed' }
      this.emit()
      return
    }
    if (Array.isArray(msg.candles)) {
      if (!this.syncing || this.historyId == null || msg.req_id !== this.historyId || !this.buffered.length) return
      const history = candlesFromHistory(msg.candles)
      const firstTick = this.buffered[0]
      if (history.length !== msg.candles.length || history.some((c, i) =>
        c.time > minuteBucket(this.historyEnd) || (i > 0 && c.time !== history[i - 1].time + 60))) {
        this.state = { ...this.state, ready: false, error: 'Invalid or incomplete history — waiting for recovery' }
        this.emit()
        return
      }
      let candles = finalizeByClock(history, firstTick.epoch)
      for (const tick of this.buffered) candles = applyTick(candles, tick).candles
      const latest = this.buffered.at(-1)!
      const lastClosed = candles.filter((c) => c.isClosed).at(-1)
      const ready = !!evaluateLatestClosed(candles)
        && lastClosed?.time === minuteBucket(latest.epoch) - 60
        && this.now() - this.lastReceipt <= MAX_TICK_GAP_SECONDS * 1000
        && Math.abs(this.now() / 1000 - latest.epoch) <= MAX_TICK_GAP_SECONDS
      this.state = {
        ...this.state, candles: candles.slice(-HISTORY), ready,
        lastPrice: latest.quote, lastTickEpoch: latest.epoch,
        lastConfirmedCandleTime: lastClosed?.time ?? null,
        error: ready ? null : 'Insufficient consecutive candles — signals paused',
      }
      this.syncing = !ready
      this.historyId = null
      this.buffered = []
      this.emit()
      return
    }
    if (!msg.tick || this.state.status !== 'CONNECTED') return
    const raw = msg.tick as { epoch: number; quote: number; symbol?: string }
    const tick: Tick = { epoch: Number(raw.epoch), quote: Number(raw.quote), symbol: raw.symbol || SYMBOL }
    if (tick.symbol !== SYMBOL || !Number.isInteger(tick.epoch) || !Number.isFinite(tick.quote) || tick.quote <= 0) return
    if (this.lastTick && (tick.epoch < this.lastTick.epoch
      || (tick.epoch === this.lastTick.epoch && tick.quote === this.lastTick.quote))) return
    if (Math.abs(this.now() / 1000 - tick.epoch) > MAX_TICK_GAP_SECONDS) {
      this.state = { ...this.state, ready: false, error: 'Delayed tick or incorrect device clock — signals paused' }
      this.emit()
      return
    }
    const gap = this.lastTick && tick.epoch - this.lastTick.epoch > MAX_TICK_GAP_SECONDS
    this.lastTick = tick
    this.lastReceipt = this.now()
    if (this.syncing) {
      if (this.historyId == null || gap) this.beginSync(tick)
      else this.buffered.push(tick)
      return
    }
    if (gap || !this.state.ready) {
      this.beginSync(tick)
      return
    }
    const { candles, closedCandle } = applyTick(this.state.candles, tick)
    this.state = {
      ...this.state, candles: candles.slice(-HISTORY), lastPrice: tick.quote,
      lastTickEpoch: tick.epoch, lastConfirmedCandleTime: closedCandle?.time ?? this.state.lastConfirmedCandleTime,
    }
    if (closedCandle && tick.epoch - (closedCandle.time + 60) <= SIGNAL_WINDOW_SECONDS
      && this.now() / 1000 - (closedCandle.time + 60) <= SIGNAL_WINDOW_SECONDS) {
      this.emit({ type: 'candle_close', candle: closedCandle })
    } else this.emit()
  }
}
