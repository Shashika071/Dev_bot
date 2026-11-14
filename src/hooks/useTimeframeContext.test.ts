// @vitest-environment jsdom
import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { MarketState } from '../lib/deriv/marketFeed'

vi.mock('../lib/timeframes/timeframeContext', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/timeframes/timeframeContext')>()
  return {
    ...original,
    computeMultiTimeframeContext: vi.fn((opts: {
      m1Side: 'RISE' | 'FALL' | null
      livePrice: number | null
    }) => {
      const closed = {
        timeframe: 15 as const,
        label: '15m' as const,
        candleTime: 1_800_000_000,
        trend: 'Up' as const,
        sma20: 100,
        atr14: 1,
        support: {
          type: 'support' as const,
          mid: 98,
          hi: 98.2,
          lo: 97.8,
          distance: 'Away' as const,
          present: true,
        },
        resistance: {
          type: 'resistance' as const,
          mid: 102,
          hi: 102.2,
          lo: 101.8,
          distance: 'Away' as const,
          present: true,
        },
        behavior: 'breakout' as const,
        behaviorText: 'Last candle closed above resistance.',
        reactionSide: 'RISE' as const,
        agreement: opts.m1Side == null
          ? null
          : opts.m1Side === 'RISE' ? 'Supports' as const : 'Opposes' as const,
        agreementReason: `computed for ${opts.m1Side ?? 'none'}`,
      }
      const card = {
        timeframe: 15 as const,
        label: '15m' as const,
        status: 'ready' as const,
        statusText: 'Ready',
        closed,
        forming: {
          timeframe: 15 as const,
          openTime: 1_800_000_900,
          closeInSeconds: 600,
          countdownLabel: '10m 00s',
          behavior: 'testing' as const,
          text: 'M15 is testing resistance; 10m 00s left.',
          supportDistance: 'Away' as const,
          resistanceDistance: 'Near' as const,
          livePrice: opts.livePrice,
        },
      }
      return {
        status: 'ready' as const,
        statusText: 'Ready',
        m1Side: opts.m1Side,
        m5: { ...card, timeframe: 5 as const, label: '5m' as const },
        m15: card,
      }
    }),
  }
})

import { useTimeframeContext } from './useTimeframeContext'

describe('useTimeframeContext', () => {
  it('updates the M15 answer when M1 switches RISE to FALL in the same open M15 candle', () => {
    const nowSec = 1_800_001_000
    const market: MarketState = {
      status: 'CONNECTED',
      ready: true,
      symbol: 'R_100',
      candles: [],
      lastPrice: 100,
      lastTickEpoch: nowSec,
      lastConfirmedCandleTime: 1_800_000_960,
      error: null,
    }
    const { result, rerender } = renderHook(
      ({ side }: { side: 'RISE' | 'FALL' | null }) =>
        useTimeframeContext(market, side, nowSec),
      { initialProps: { side: null as 'RISE' | 'FALL' | null } },
    )
    expect(result.current.m15.closed!.agreement).toBeNull()

    rerender({ side: 'RISE' })
    const rise = result.current.m15.closed!
    expect(rise.agreement).toBe('Supports')
    expect(rise.trend).toBe('Up')

    rerender({ side: 'FALL' })
    const fall = result.current.m15.closed!
    expect(fall.agreement).toBe('Opposes')
    expect(fall.candleTime).toBe(rise.candleTime)
    expect(fall.trend).toBe(rise.trend)
    expect(fall.behavior).toBe(rise.behavior)
    expect(result.current.m15.forming?.openTime).toBeDefined()
  })
})
