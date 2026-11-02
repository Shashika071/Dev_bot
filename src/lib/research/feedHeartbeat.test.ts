import { afterEach, describe, expect, it, vi } from 'vitest'
import { startResearchHeartbeat } from './feedHeartbeat'

afterEach(() => { vi.useRealTimers() })

describe('research feed heartbeat', () => {
  it('sends a ping every 30 seconds and stops on socket close', async () => {
    vi.useFakeTimers()
    const request = vi.fn().mockResolvedValue({ ping: 'pong' })
    const reconnect = vi.fn()
    const stop = startResearchHeartbeat(request, reconnect)
    await vi.advanceTimersByTimeAsync(90_000)
    expect(request).toHaveBeenCalledTimes(3)
    expect(request).toHaveBeenLastCalledWith({ ping: 1 })
    expect(reconnect).not.toHaveBeenCalled()
    stop()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(request).toHaveBeenCalledTimes(3)
  })

  it('reconnects after a rejected or malformed ping, without overlapping requests', async () => {
    vi.useFakeTimers()
    let reject: (error: Error) => void = () => {}
    const request = vi.fn(() => new Promise((_, fail) => { reject = fail }))
    const reconnect = vi.fn()
    const stop = startResearchHeartbeat(request, reconnect)
    await vi.advanceTimersByTimeAsync(90_000)
    expect(request).toHaveBeenCalledTimes(1)
    reject(new Error('Request timeout'))
    await vi.advanceTimersByTimeAsync(1)
    expect(reconnect).toHaveBeenCalledWith('Ping failed: Request timeout')
    stop()
    const stop2 = startResearchHeartbeat(async () => ({ error: 'unavailable' }), reconnect)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(reconnect).toHaveBeenLastCalledWith('Invalid ping acknowledgement')
    stop2()
  })

  it('ignores a late failure from the socket that has already closed', async () => {
    vi.useFakeTimers()
    let reject: (error: Error) => void = () => {}
    const reconnect = vi.fn()
    const stop = startResearchHeartbeat(() => new Promise((_, fail) => { reject = fail }), reconnect)
    await vi.advanceTimersByTimeAsync(30_000)
    stop()
    reject(new Error('Old socket'))
    await vi.advanceTimersByTimeAsync(1)
    expect(reconnect).not.toHaveBeenCalled()
  })
})
