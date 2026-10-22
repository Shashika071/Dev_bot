import { afterEach, describe, expect, it, vi } from 'vitest';
import { syntheticApi } from './synthetic';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('synthetic API contract', () => {
  it('saves the account with PUT and returns status without a second mutation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ running: false })));
    vi.stubGlobal('fetch', fetchMock);
    expect(await syntheticApi.account({ enabled: false })).toEqual({ running: false });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/synthetic\/account$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'PUT', body: '{"enabled":false}', cache: 'no-store' });
  });

  it('aborts a stalled request and never retries a control action', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, opts: RequestInit) => new Promise((_resolve, reject) => {
      opts.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetchMock);
    const result = expect(syntheticApi.start({ stake: 1, max_signals_per_day: 20, demo_auto_trade: false })).rejects.toThrow('may already have reached the server');
    await vi.advanceTimersByTimeAsync(45001);
    await result;
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('propagates caller cancellation and clears the request timer', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url: string, opts: RequestInit) => new Promise((_resolve, reject) => {
      opts.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    })));
    const controller = new AbortController();
    const result = expect(syntheticApi.status(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('shows backend validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: 'Invalid stake' }] }), { status: 422 })));
    await expect(syntheticApi.start({ stake: 100, max_signals_per_day: 20, demo_auto_trade: false })).rejects.toThrow('Invalid stake');
  });
});
