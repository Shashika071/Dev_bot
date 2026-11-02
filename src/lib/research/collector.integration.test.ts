import { expect, it } from 'vitest'
import { once } from 'node:events'
import { createServer } from 'node:net'
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { WebSocketServer, WebSocket } from 'ws'

it('collects both paired strategies with quotes through the actual local HTTP/WebSocket collector', async () => {
  const parent = resolve('research-output', 'tests')
  mkdirSync(parent, { recursive: true })
  const root = mkdtempSync(join(parent, 'collector-test-'))
  const reserve = createServer().listen(0, '127.0.0.1')
  await once(reserve, 'listening')
  const port = (reserve.address() as { port: number }).port
  await new Promise<void>((done) => reserve.close(() => done()))
  const feed = new WebSocketServer({ port: 0, host: '127.0.0.1' })
  await once(feed, 'listening')
  const feedPort = (feed.address() as { port: number }).port
  const epoch = 1_800_000_000
  let socket: WebSocket | undefined
  const methods: string[] = []
  const sendTick = (offset: number, quote = 126) => socket!.send(JSON.stringify({
    msg_type: 'tick', tick: { epoch: epoch + offset, quote, symbol: 'R_100' },
  }))
  feed.on('connection', (client) => {
    socket = client
    client.on('message', (raw) => {
      const m = JSON.parse(String(raw))
      methods.push(Object.keys(m)[0])
      if (m.ticks_history) client.send(JSON.stringify({ req_id: m.req_id, candles:
        Array.from({ length: 260 }, (_, i) => ({ epoch: epoch - (260 - i) * 60,
          open: 100 + i / 10, high: 100.2 + i / 10, low: 99.9 + i / 10, close: 100.1 + i / 10 })) }))
      else if (m.ticks) sendTick(0)
      else if (m.proposal) client.send(JSON.stringify({ req_id: m.req_id, proposal: { ask_price: 1, payout: 1.03 } }))
      else if (m.ping) client.send(JSON.stringify({ req_id: m.req_id, ping: 'pong' }))
    })
  })
  // Only this child gets a fixed delivery clock; no production test hook or real API is used.
  const child = spawn(process.execPath, ['--import', `data:text/javascript,Date.now=()=>${(epoch + 2) * 1000}`,
    'scripts/research-collector.mjs'], { cwd: process.cwd(), windowsHide: true, env: {
      ...process.env, RESEARCH_API_HOST: '127.0.0.1', RESEARCH_API_PORT: String(port),
      RESEARCH_DATA_ROOT: root, VITE_DERIV_PUBLIC_WS: `ws://127.0.0.1:${feedPort}`, VITE_DERIV_SYMBOL: 'R_100',
    } })
  let logs = ''
  child.stdout.on('data', (chunk) => { logs += String(chunk) })
  child.stderr.on('data', (chunk) => { logs += String(chunk) })
  const api = `http://127.0.0.1:${port}`
  async function waitFor<T>(fn: () => Promise<T>, accept: (value: T) => boolean): Promise<T> {
    const deadline = Date.now() + 15_000
    while (Date.now() < deadline) {
      try { const value = await fn(); if (accept(value)) return value } catch { /* wait for startup */ }
      if (child.exitCode != null) throw new Error(logs)
      await new Promise((done) => setTimeout(done, 50))
    }
    throw new Error(`Collector wait timed out: ${logs}`)
  }
  const status = () => fetch(`${api}/status`).then((r) => r.json())
  try {
    await waitFor(status, (s) => s.state.feedHealthy)
    const started = await fetch(`${api}/control/start-discovery`, { method: 'POST' })
    expect(started.ok).toBe(true)
    sendTick(0)
    const quoted = await waitFor(status, (s) => ['S6', 'S7'].every((id) =>
      s.summary.strategies.some((r: { id: string; quotesAvailable: number }) => r.id === id && r.quotesAvailable === 1)))
    expect(quoted.state.observationCount).toBeGreaterThanOrEqual(2)
    sendTick(2)
    sendTick(4, 126.2)
    const settled = await waitFor(status, (s) => s.summary.strategies.some((r: { id: string; quotedHits: number }) => r.id === 'S6' && r.quotedHits === 1))
    expect(settled.summary.strategies.find((r: { id: string }) => r.id === 'S7').quotedPending).toBe(1)
    const stopped = await fetch(`${api}/control/stop-entries`, { method: 'POST' })
    expect(stopped.ok).toBe(true)
    const csv = await fetch(`${api}/export/csv`).then((r) => r.text())
    expect(csv).toContain('h7Reason')
    expect(csv).toContain('"S7"')
    expect(methods).not.toContain('buy')
    expect(methods).not.toContain('authorize')
    expect(methods.filter((m) => m === 'proposal')).toHaveLength(2)
  } finally {
    const exited = child.exitCode != null ? Promise.resolve() : once(child, 'exit')
    child.kill('SIGTERM')
    await exited
    for (const client of feed.clients) client.terminate()
    await new Promise<void>((done) => feed.close(() => done()))
    if (resolve(root).startsWith(parent + '\\collector-test-') || resolve(root).startsWith(parent + '/collector-test-')) {
      rmSync(root, { recursive: true, force: true })
    }
  }
}, 30_000)
