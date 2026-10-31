import WebSocket from 'ws'

/** Read-only reusable public socket. No authentication or trading operations. */
export class PublicHistory {
  ws = null
  nextId = 1
  pending = new Map()
  async connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket('wss://api.derivws.com/trading/v1/options/ws/public')
    this.ws = ws
    ws.on('message', (raw) => {
      let msg
      try { msg = JSON.parse(String(raw)) } catch { return }
      const pending = this.pending.get(msg.req_id)
      if (!pending) return
      this.pending.delete(msg.req_id)
      clearTimeout(pending.timer)
      if (msg.error) pending.reject(Object.assign(new Error(msg.error.message), { code: msg.error.code }))
      else pending.resolve(msg)
    })
    ws.on('close', () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error('Public socket closed')) }
      this.pending.clear()
    })
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { ws.terminate(); reject(new Error('Connection timed out')) }, 15000)
      ws.once('open', () => { clearTimeout(timeout); resolve() })
      ws.on('error', (error) => { clearTimeout(timeout); reject(error) })
    })
  }
  async request(payload) {
    await this.connect()
    const req_id = this.nextId++
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(req_id)
        reject(new Error('Public data request timed out'))
      }, 20000)
      this.pending.set(req_id, { resolve, reject, timer })
      this.ws.send(JSON.stringify({ ...payload, req_id }))
    })
  }
  close() { this.ws?.close() }
}
