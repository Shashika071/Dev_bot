import type { ConnectionStatus } from '../types'

export type DerivMessageHandler = (msg: Record<string, unknown>) => void

const DEFAULT_WS =
  import.meta.env.VITE_DERIV_PUBLIC_WS ||
  'wss://api.derivws.com/trading/v1/options/ws/public'

export class DerivPublicClient {
  private ws: WebSocket | null = null
  private reqId = 1
  private handlers = new Set<DerivMessageHandler>()
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connectTimer: ReturnType<typeof setTimeout> | null = null
  private intentionalClose = false
  private status: ConnectionStatus = 'DISCONNECTED'
  private onStatus: ((s: ConnectionStatus) => void) | null = null
  private url: string

  constructor(url = DEFAULT_WS) {
    this.url = url
  }

  get connectionStatus(): ConnectionStatus {
    return this.status
  }

  setStatusHandler(fn: (s: ConnectionStatus) => void) {
    this.onStatus = fn
  }

  subscribe(handler: DerivMessageHandler) {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  private setStatus(s: ConnectionStatus) {
    this.status = s
    this.onStatus?.(s)
  }

  connect() {
    this.intentionalClose = false
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    this.setStatus('RECONNECTING')
    const ws = new WebSocket(this.url)
    this.ws = ws
    this.connectTimer = setTimeout(() => {
      if (this.ws !== ws) return
      this.close()
      this.intentionalClose = false
      this.setStatus('RECONNECTING')
      this.scheduleReconnect()
    }, 15000)

    ws.onopen = () => {
      if (this.ws !== ws) return
      this.clearConnectTimer()
      this.setStatus('CONNECTED')
      this.clearPing()
      this.pingTimer = setInterval(() => this.send({ ping: 1 }), 25000)
    }

    ws.onmessage = (ev) => {
      if (this.ws !== ws) return
      let msg: Record<string, unknown>
      try {
        msg = JSON.parse(String(ev.data))
      } catch {
        return
      }
      if (msg && typeof msg === 'object' && !Array.isArray(msg)) {
        for (const h of this.handlers) h(msg)
      }
    }

    ws.onerror = () => {
      // onclose will handle reconnect
    }

    ws.onclose = () => {
      if (this.ws !== ws) return
      this.clearConnectTimer()
      this.clearPing()
      this.ws = null
      if (!this.intentionalClose) {
        this.setStatus('RECONNECTING')
        this.scheduleReconnect()
      } else {
        this.setStatus('DISCONNECTED')
      }
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 2000)
  }

  private clearPing() {
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.pingTimer = null
  }

  private clearConnectTimer() {
    if (this.connectTimer) clearTimeout(this.connectTimer)
    this.connectTimer = null
  }

  send(payload: Record<string, unknown>) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return null
    const id = this.reqId++
    this.ws.send(JSON.stringify({ ...payload, req_id: id }))
    return id
  }

  reconnect() {
    this.close()
    this.connect()
  }

  close() {
    this.intentionalClose = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    this.clearPing()
    this.clearConnectTimer()
    const old = this.ws
    this.ws = null
    old?.close()
    this.setStatus('DISCONNECTED')
  }
}
