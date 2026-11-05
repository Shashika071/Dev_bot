import type { Candle, ConnectionStatus, Tick } from '../types'
import { applyTick, candlesFromHistory, finalizeByClock, minuteBucket } from './candles'
import { DerivPublicClient, type DerivMessageHandler } from './websocket'
import { evaluateLatestClosed, WARMUP_BARS } from '../strategy/signalEngine'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'
const configuredHistory = Number(import.meta.env.VITE_HISTORY_COUNT || 5000)
const HISTORY = Number.isFinite(configuredHistory) ? Math.max(120, Math.min(5000, Math.floor(configuredHistory))) : 5000
/** Public Deriv API usually caps each ticks_history response around this size. */
const HISTORY_BATCH = 1000
/** Live trailing streak needed for signals (warmup is 60). */
const MIN_HISTORY = WARMUP_BARS + 5
/** Live C5 deadline, including balance/proposal preparation before buy. */
export const SIGNAL_WINDOW_SECONDS = 10
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

/** Split into contiguous minute runs (oldest → newest). */
export function consecutiveRuns(batch: Candle[], maxTime: number): Candle[][] {
  const sorted = [...batch].filter((c) => c.time <= maxTime).sort((a, b) => a.time - b.time)
  if (!sorted.length) return []
  const runs: Candle[][] = []
  let cur: Candle[] = []
  for (const c of sorted) {
    if (!cur.length || c.time === cur[cur.length - 1]!.time + 60) {
      cur.push(c)
      continue
    }
    runs.push(cur)
    cur = [c]
  }
  if (cur.length) runs.push(cur)
  return runs
}

/** Newest contiguous streak (what live ticks / signals continue from). */
export function bestHistorySegment(
  batch: Candle[],
  maxTime: number,
  _minLen = MIN_HISTORY,
): Candle[] {
  const runs = consecutiveRuns(batch, maxTime)
  return runs[runs.length - 1] ?? []
}

/** @deprecated */
export function longestConsecutive(batch: Candle[], maxTime: number): Candle[] {
  return bestHistorySegment(batch, maxTime, 1)
}

/**
 * Load up to HISTORY candles for the chart (gaps allowed).
 * Signals only need the newest consecutive streak (closedSegments / evaluateLatestClosed).
 */
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
  private historyTargetEnd = 0
  private historyPages = new Map<number, Candle>()
  private buffered: Tick[] = []
  private syncing = true
  private lastTick: Tick | null = null
  private lastReceipt = 0
  private syncStarted = 0
  private lastRecoverAt = 0
  private recoverAttempts = 0
  private watchdog: ReturnType<typeof setInterval> | null = null

  constructor(client: FeedClient = new DerivPublicClient(), now = Date.now) {
    this.client = client
    this.now = now
    client.setStatusHandler((status) => {
      this.syncing = true
      this.historyId = null
      this.historyPages.clear()
      this.buffered = []
      this.lastTick = null
      this.syncStarted = this.now()
      this.lastReceipt = this.now()
      this.recoverAttempts = 0
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
    const syncBudget = this.historyPages.size > 0 ? 120_000 : 20_000
    if (age > 15_000 || (this.syncing && this.now() - this.syncStarted > syncBudget)) {
      this.client.reconnect()
    }
  }

  private beginSync(tick: Tick) {
    this.syncing = true
    this.syncStarted = this.now()
    this.buffered = [tick]
    this.historyPages.clear()
    this.historyTargetEnd = tick.epoch - 1
    this.historyEnd = this.historyTargetEnd
    this.state = { ...this.state, ready: false, error: 'Recovering candle history — signals paused' }
    this.requestHistoryPage()
    this.emit()
  }

  private requestHistoryPage() {
    const remaining = HISTORY - this.historyPages.size
    if (remaining <= 0) {
      this.finishHistorySync()
      return
    }
    const count = Math.min(HISTORY_BATCH, remaining)
    const id = this.client.send({
      ticks_history: SYMBOL, style: 'candles', granularity: 60,
      count, end: String(this.historyEnd),
    })
    this.historyId = id
    if (id == null) {
      this.state = { ...this.state, ready: false, error: 'History request failed — retrying' }
      this.emit()
    }
  }

  private recoverHistory(reason: string) {
    const latest = this.buffered.at(-1) ?? this.lastTick
    this.historyId = null
    this.historyPages.clear()
    this.syncing = true
    this.recoverAttempts += 1
    this.state = { ...this.state, ready: false, error: reason }
    this.emit()
    const now = this.now()
    if (!latest || now - this.lastRecoverAt < 2500 || this.recoverAttempts > 8) return
    this.lastRecoverAt = now
    this.beginSync(latest)
  }

  private signalReady(candles: Candle[], latest: Tick): boolean {
    const lastClosed = candles.filter((c) => c.isClosed).at(-1)
    return !!evaluateLatestClosed(candles)
      && lastClosed?.time === minuteBucket(latest.epoch) - 60
      && this.now() - this.lastReceipt <= MAX_TICK_GAP_SECONDS * 1000
      && Math.abs(this.now() / 1000 - latest.epoch) <= MAX_TICK_GAP_SECONDS
  }

  private finishHistorySync() {
    const firstTick = this.buffered[0]
    if (!firstTick) return
    const maxTime = minuteBucket(this.historyTargetEnd)
    const all = [...this.historyPages.values()]
      .filter((c) => c.time <= maxTime)
      .sort((a, b) => a.time - b.time)
    const trailing = bestHistorySegment(all, maxTime)
    const liveMinute = minuteBucket(firstTick.epoch)
    const trailEnd = trailing.at(-1)?.time
    if (
      trailing.length < MIN_HISTORY
      || trailEnd == null
      || liveMinute - trailEnd > 60
    ) {
      this.recoverHistory(
        `Live history streak too short (${trailing.length}/${MIN_HISTORY}) — retrying`,
      )
      return
    }

    // Chart: full loaded history (gaps OK). Signals use newest segment via closedSegments.
    let candles = finalizeByClock(all, firstTick.epoch)
    for (const tick of this.buffered) candles = applyTick(candles, tick).candles
    const latest = this.buffered.at(-1)!
    const lastClosed = candles.filter((c) => c.isClosed).at(-1)
    const ready = this.signalReady(candles, latest)
    this.state = {
      ...this.state,
      candles: candles.slice(-HISTORY),
      ready,
      lastPrice: latest.quote,
      lastTickEpoch: latest.epoch,
      lastConfirmedCandleTime: lastClosed?.time ?? null,
      error: ready ? null : 'History loaded — waiting for signal warmup / fresh ticks',
    }
    this.syncing = false
    this.recoverAttempts = 0
    this.historyId = null
    this.historyPages.clear()
    this.buffered = []
    this.emit()
  }

  private onMessage(msg: Record<string, unknown>) {
    if (msg.error) {
      const err = msg.error as { message?: string }
      this.state = { ...this.state, ready: false, error: err.message || 'Market data request failed' }
      this.emit()
      if (this.syncing) this.client.reconnect()
      return
    }
    if (Array.isArray(msg.candles)) {
      if (!this.syncing || this.historyId == null || msg.req_id !== this.historyId || !this.buffered.length) return
      const batch = candlesFromHistory(
        msg.candles as Array<{ epoch: number; open: number; high: number; low: number; close: number }>,
      )
      const maxTime = minuteBucket(this.historyTargetEnd)
      const before = this.historyPages.size
      for (const c of batch) {
        if (c.time <= maxTime) this.historyPages.set(c.time, c)
      }
      this.syncStarted = this.now()

      // First page must include a usable live trailing streak.
      if (before === 0) {
        const trailing = bestHistorySegment([...this.historyPages.values()], maxTime)
        const liveMinute = minuteBucket(this.buffered[0]!.epoch)
        const trailEnd = trailing.at(-1)?.time
        if (
          trailing.length < MIN_HISTORY
          || trailEnd == null
          || liveMinute - trailEnd > 60
        ) {
          this.recoverHistory(
            `Live history streak too short (${trailing.length}/${MIN_HISTORY}) — retrying`,
          )
          return
        }
      }

      const added = this.historyPages.size - before
      const needMore = this.historyPages.size < HISTORY && added >= Math.min(HISTORY_BATCH, HISTORY - before) * 0.85
      if (needMore) {
        const earliest = Math.min(...this.historyPages.keys())
        this.historyEnd = earliest - 60
        this.state = {
          ...this.state,
          error: `Loading history ${this.historyPages.size}/${HISTORY}…`,
        }
        this.requestHistoryPage()
        this.emit()
        return
      }
      this.finishHistorySync()
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
    const gap = this.lastTick != null && tick.epoch - this.lastTick.epoch > MAX_TICK_GAP_SECONDS
    this.lastTick = tick
    this.lastReceipt = this.now()
    if (this.syncing) {
      if (this.historyId == null || gap) this.beginSync(tick)
      else this.buffered.push(tick)
      return
    }
    // Resync only on real gaps — chart may have mid-history gaps from Deriv.
    if (gap) {
      this.beginSync(tick)
      return
    }
    const trailingLen = consecutiveRuns(this.state.candles, minuteBucket(tick.epoch)).at(-1)?.length ?? 0
    if (trailingLen < MIN_HISTORY) {
      this.beginSync(tick)
      return
    }
    const { candles, closedCandle } = applyTick(this.state.candles, tick)
    const ready = this.signalReady(candles, tick)
    this.state = {
      ...this.state,
      candles: candles.slice(-HISTORY),
      lastPrice: tick.quote,
      lastTickEpoch: tick.epoch,
      lastConfirmedCandleTime: closedCandle?.time ?? this.state.lastConfirmedCandleTime,
      ready,
      error: ready ? null : this.state.error,
    }
    // A live close is an observation, not permission to buy. Always deliver it
    // so the Auto handler can record why a qualified signal was skipped.
    // History synchronization still emits no closes; expiry is checked by Auto
    // against both receipt time and tick time, and again immediately before buy.
    if (closedCandle) this.emit({ type: 'candle_close', candle: closedCandle })
    else this.emit()
  }
}
