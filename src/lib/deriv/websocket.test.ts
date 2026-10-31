import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DerivPublicClient } from './websocket'

class Socket {
  static OPEN = 1
  static CONNECTING = 0
  static instances: Socket[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  send = vi.fn()
  close = vi.fn(() => { this.readyState = 3 })
  constructor() { Socket.instances.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
}
let client: DerivPublicClient
beforeEach(() => {
  vi.useFakeTimers()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  client = new DerivPublicClient('wss://test.invalid')
})
afterEach(() => { client.close(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('old socket callbacks cannot close or contaminate a new connection', () => {
  const received = vi.fn()
  client.subscribe(received)
  client.connect()
  const old = Socket.instances[0]
  old.open()
  client.reconnect()
  const next = Socket.instances[1]
  next.open()
  old.onclose?.()
  old.onmessage?.({ data: '{"tick": {"quote": 1}}' })
  expect(client.connectionStatus).toBe('CONNECTED')
  expect(received).not.toHaveBeenCalled()
  expect(client.send({ ping: 1 })).toBe(1)
  expect(next.send).toHaveBeenCalled()
})

it('times out a hung handshake and cancels all timers on stop', () => {
  client.connect()
  vi.advanceTimersByTime(17000)
  expect(Socket.instances).toHaveLength(2)
  client.close()
  vi.advanceTimersByTime(60000)
  expect(Socket.instances).toHaveLength(2)
  expect(client.connectionStatus).toBe('DISCONNECTED')
})

it('ignores invalid JSON and non-object payloads', () => {
  const received = vi.fn()
  client.subscribe(received)
  client.connect()
  const ws = Socket.instances[0]
  ws.open()
  for (const data of ['oops', 'null', '[]', '3']) ws.onmessage?.({ data })
  expect(received).not.toHaveBeenCalled()
})
