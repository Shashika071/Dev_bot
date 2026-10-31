import type { FinalSignal } from '../types'
import type { DerivTradingClient } from './tradingClient'

export type TouchSideMode = 'both' | 'signal'

export type TouchTradeSettings = {
  symbol: string
  currency: string
  stake: number
  /** Absolute barrier distance, e.g. 0.05 → "+0.05" / "-0.05" */
  barrierAbs: number
  duration: number
  durationUnit: 's' | 'm' | 'h' | 'd'
  mode: TouchSideMode
}

export type TouchBuyResult = {
  barrier: string
  proposalId: string
  contractId: number | null
  buyPrice: number | null
  payout: number | null
  raw: Record<string, unknown>
}

/** Format absolute distance as Deriv relative barrier ("+0.05" / "-0.05"). */
export function formatTouchBarrier(abs: number, sign: 1 | -1): string {
  if (!(abs > 0) || !Number.isFinite(abs)) throw new Error('Barrier must be a positive number')
  const body = String(abs)
  return sign > 0 ? `+${body}` : `-${body}`
}

export function barriersForSignal(signal: FinalSignal, settings: TouchTradeSettings): string[] {
  if (signal !== 'RISE' && signal !== 'FALL') return []
  const up = formatTouchBarrier(settings.barrierAbs, 1)
  const down = formatTouchBarrier(settings.barrierAbs, -1)
  if (settings.mode === 'both') return [up, down]
  return [signal === 'RISE' ? up : down]
}

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
  return Number.isFinite(n) ? n : null
}

/** Request ONETOUCH proposal then buy it. */
export async function buyOneTouch(
  client: DerivTradingClient,
  settings: TouchTradeSettings,
  barrier: string,
): Promise<TouchBuyResult> {
  const proposalMsg = await client.request({
    proposal: 1,
    amount: settings.stake,
    basis: 'stake',
    contract_type: 'ONETOUCH',
    currency: settings.currency,
    duration: settings.duration,
    duration_unit: settings.durationUnit,
    underlying_symbol: settings.symbol,
    barrier,
  })

  const proposal = proposalMsg.proposal as Record<string, unknown> | undefined
  const proposalId = typeof proposal?.id === 'string' ? proposal.id : null
  if (!proposalId) throw new Error(`No proposal id for barrier ${barrier}`)

  const ask = num(proposal?.ask_price) ?? settings.stake
  const buyMsg = await client.request({
    buy: proposalId,
    price: ask,
  })

  const buy = buyMsg.buy as Record<string, unknown> | undefined
  return {
    barrier,
    proposalId,
    contractId: num(buy?.contract_id),
    buyPrice: num(buy?.buy_price),
    payout: num(buy?.payout),
    raw: buyMsg,
  }
}

export async function placeTouchTrades(
  client: DerivTradingClient,
  settings: TouchTradeSettings,
  signal: FinalSignal,
): Promise<TouchBuyResult[]> {
  const barriers = barriersForSignal(signal, settings)
  if (!barriers.length) return []
  const results: TouchBuyResult[] = []
  // Sequential buys avoid rate-limit / proposal races on the same socket.
  for (const barrier of barriers) {
    results.push(await buyOneTouch(client, settings, barrier))
  }
  return results
}
