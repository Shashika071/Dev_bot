import { describe, expect, it, vi } from 'vitest'
import {
  barriersForSignal,
  buyOneTouch,
  formatTouchBarrier,
  placeTouchTrades,
  type TouchTradeSettings,
} from './touchTrade'
import type { DerivTradingClient } from './tradingClient'
import { isC5EntryExpired } from '../strategy/c5AutoAuth'

describe('touch barriers', () => {
  it('formats signed relative barriers', () => {
    expect(formatTouchBarrier(0.05, 1)).toBe('+0.05')
    expect(formatTouchBarrier(0.05, -1)).toBe('-0.05')
    expect(formatTouchBarrier(0.03, 1)).toBe('+0.03')
  })

  it('rejects non-positive barrier', () => {
    expect(() => formatTouchBarrier(0, 1)).toThrow()
    expect(() => formatTouchBarrier(-1, 1)).toThrow()
  })

  it('both mode returns + and −', () => {
    expect(barriersForSignal('RISE', {
      symbol: 'R_100', currency: 'USD', stake: 1, barrierAbs: 0.05,
      duration: 7, durationUnit: 'm', mode: 'both',
    })).toEqual(['+0.05', '-0.05'])
  })

  it('signal mode follows RISE/FALL', () => {
    const base = {
      symbol: 'R_100', currency: 'USD', stake: 1, barrierAbs: 0.03,
      duration: 5, durationUnit: 'm' as const, mode: 'signal' as const,
    }
    expect(barriersForSignal('RISE', base)).toEqual(['+0.03'])
    expect(barriersForSignal('FALL', base)).toEqual(['-0.03'])
    expect(barriersForSignal('NO_TRADE', base)).toEqual([])
  })
})

describe('buyOneTouch expiry after delayed proposal', () => {
  const settings: TouchTradeSettings = {
    symbol: 'R_100',
    currency: 'USD',
    stake: 1,
    barrierAbs: 0.05,
    duration: 7,
    durationUnit: 'm',
    mode: 'signal',
  }

  function mockClient(handlers: {
    onProposal?: () => void | Promise<void>
    onBuy?: () => void
  }): DerivTradingClient {
    return {
      connectionStatus: 'CONNECTED',
      request: vi.fn(async (payload: Record<string, unknown>) => {
        if ('proposal' in payload) {
          await handlers.onProposal?.()
          return {
            proposal: { id: 'prop-1', ask_price: 1 },
          }
        }
        if ('buy' in payload) {
          handlers.onBuy?.()
          return {
            buy: { contract_id: 42, buy_price: 1, payout: 1.8 },
          }
        }
        return {}
      }),
    } as unknown as DerivTradingClient
  }

  it('does not send buy when the entry window expires during a slow proposal', async () => {
    const expiresAt = 1_700_000_065
    let nowSec = 1_700_000_061 // still inside the 5s window when proposal starts
    const buys: unknown[] = []

    const client = mockClient({
      onProposal: async () => {
        // Quote arrives after the entry window.
        nowSec = expiresAt + 1
      },
      onBuy: () => { buys.push(true) },
    })

    await expect(buyOneTouch(client, settings, '+0.05', {
      beforeBuy: () => {
        if (isC5EntryExpired(expiresAt, nowSec)) {
          throw new Error('C5 skipped · entry window expired after proposal')
        }
      },
    })).rejects.toThrow(/expired after proposal/)

    expect(buys).toHaveLength(0)
    const requests = vi.mocked(client.request).mock.calls.map((c) => c[0] as Record<string, unknown>)
    expect(requests.some((r) => 'proposal' in r)).toBe(true)
    expect(requests.some((r) => 'buy' in r)).toBe(false)
  })

  it('placeTouchTrades still buys when the gate passes after proposal', async () => {
    const buys: unknown[] = []
    const client = mockClient({
      onBuy: () => { buys.push(true) },
    })
    const results = await placeTouchTrades(client, settings, 'RISE', {
      beforeBuy: () => { /* still valid */ },
    })
    expect(results).toHaveLength(1)
    expect(buys).toHaveLength(1)
    expect(results[0]!.contractId).toBe(42)
  })

  it('aborts placeTouchTrades when beforeBuy fails after a delayed quote', async () => {
    const expiresAt = 100
    let nowSec = 99
    const buys: unknown[] = []
    const client = mockClient({
      onProposal: () => { nowSec = 101 },
      onBuy: () => { buys.push(true) },
    })
    await expect(placeTouchTrades(client, settings, 'FALL', {
      beforeBuy: () => {
        if (isC5EntryExpired(expiresAt, nowSec)) {
          throw new Error('C5 skipped · entry window expired after proposal')
        }
      },
    })).rejects.toThrow(/expired after proposal/)
    expect(buys).toHaveLength(0)
  })
})
