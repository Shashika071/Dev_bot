import type { ConnectionStatus } from '../types'
import { getTradingWsUrl } from './tradingAuth'

export type TradingMessage = Record<string, unknown>

type Pending = {
  resolve: (msg: TradingMessage) => void
  reject: (err: Error) => void
  timer: ReturnType<typeof setTimeout>
}

/**
 * Authenticated Options trading socket.
 * Re-fetches a fresh OTP URL on every (re)connect because OTPs are single-use.
 */
export class DerivTradingClient {
  private ws: WebSocket | null = null
  private reqId = 1
  private pending = new Map<number, Pending>()
  private handlers = new Set<(msg: TradingMessage) => void>()
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  private status: ConnectionStatus = 'DISCONNECTED'
  private onStatus: ((s: ConnectionStatus) => void) | null = null
  private token: string
  private appId: string
  private accountId: string

  constructor(token: string, appId: string, accountId: string) {
    this.token = token
    this.appId = appId
    this.accountId = accountId
  }

  get connectionStatus(): ConnectionStatus {
    return this.status
  }

  setStatusHandler(fn: (s: ConnectionStatus) => void) {
    this.onStatus = fn
  }

  subscribe(handler: (msg: TradingMessage) => void) {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  private setStatus(s: ConnectionStatus) {
    this.status = s
    this.onStatus?.(s)
  }

  async connect() {
    this.intentionalClose = false
    if (this.ws?.readyState === WebSocket.OPEN) return
    this.setStatus('RECONNECTING')
    let url: string
    try {
      url = await getTradingWsUrl(this.token, this.appId, this.accountId)
    } catch (err) {
      this.setStatus('DISCONNECTED')
      throw err
    }
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(url)
      this.ws = ws
      let settled = false
      const timer = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) {
          ws.close()
          fail(new Error('Trading WebSocket connect timed out'))
        }
      }, 15000)
      const fail = (err: Error) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (this.ws === ws) this.ws = null
        reject(err)
      }
      ws.onopen = () => {
        if (this.ws !== ws) return
        settled = true
        clearTimeout(timer)
        this.setStatus('CONNECTED')
        this.clearPing()
        this.pingTimer = setInterval(() => {
          void this.request({ ping: 1 }, 10000).catch(() => { /* keep-alive */ })
        }, 25000)
        resolve()
      }
      ws.onmessage = (ev) => {
        if (this.ws !== ws) return
        let msg: TradingMessage
        try {
          msg = JSON.parse(String(ev.data)) as TradingMessage
        } catch {
          return
        }
        if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return
        const reqId = typeof msg.req_id === 'number' ? msg.req_id : null
        if (reqId != null && this.pending.has(reqId)) {
          const p = this.pending.get(reqId)!
          this.pending.delete(reqId)
          clearTimeout(p.timer)
          if (msg.error && typeof msg.error === 'object') {
            const err = msg.error as { message?: string; code?: string }
            p.reject(new Error(err.message || err.code || 'Trading API error'))
          } else {
            p.resolve(msg)
          }
        }
        for (const h of this.handlers) h(msg)
      }
      ws.onerror = () => {
        if (this.ws !== ws) return
        fail(new Error('Trading WebSocket error'))
      }
      ws.onclose = () => {
        if (this.ws !== ws) return
        this.clearPing()
        this.rejectAll(new Error('Trading socket closed'))
        this.ws = null
        if (!settled) fail(new Error('Trading WebSocket closed before open'))
        else if (!this.intentionalClose) {
          this.setStatus('RECONNECTING')
          this.scheduleReconnect()
        } else {
          this.setStatus('DISCONNECTED')
        }
      }
    })
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || this.intentionalClose) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      void this.connect().catch(() => {
        this.setStatus('RECONNECTING')
        this.scheduleReconnect()
      })
    }, 2500)
  }

  private clearPing() {
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.pingTimer = null
  }

  private rejectAll(err: Error) {
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer)
      p.reject(err)
      this.pending.delete(id)
    }
  }

  request(payload: TradingMessage, timeoutMs = 15000): Promise<TradingMessage> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('Trading socket is not connected'))
        return
      }
      const id = this.reqId++
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`Request ${payload && Object.keys(payload)[0]} timed out`))
      }, timeoutMs)
      this.pending.set(id, { resolve, reject, timer })
      this.ws.send(JSON.stringify({ ...payload, req_id: id }))
    })
  }

  close() {
    this.intentionalClose = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.clearPing()
    this.rejectAll(new Error('Trading client closed'))
    const old = this.ws
    this.ws = null
    old?.close()
    this.setStatus('DISCONNECTED')
  }
}
