import type { FinalSignal } from '../types'
import type { DerivTradingClient } from './tradingClient'
import { brokerNumber } from './contractTracking'

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
  contractId: number
  buyPrice: number | null
  payout: number | null
  /** Broker entry spot when the API provides it */
  entrySpot: number | null
  raw: Record<string, unknown>
}

export type TouchProposalQuote = {
  barrier: string
  proposalId: string
  ask: number | null
  payout: number | null
  spot: number | null
  responseEpoch: number
  raw: Record<string, unknown>
}

/**
 * Optional gate after the proposal quote returns and immediately before buy.
 * Throw to abort the purchase (e.g. C5 entry window expired during a slow quote).
 */
export type TouchBuyGate = {
  beforeBuy?: () => void | Promise<void>
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

/** Request a fresh ONETOUCH proposal without buying. */
export async function proposeOneTouch(
  client: DerivTradingClient,
  settings: TouchTradeSettings,
  barrier: string,
): Promise<TouchProposalQuote> {
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
  const proposalId = typeof proposal?.id === 'string' ? proposal.id : ''
  return {
    barrier,
    proposalId,
    ask: brokerNumber(proposal?.ask_price),
    payout: brokerNumber(proposal?.payout),
    spot: brokerNumber(proposal?.spot),
    responseEpoch: Date.now() / 1000,
    raw: proposalMsg,
  }
}

/** Request ONETOUCH proposal then buy it. */
export async function buyOneTouch(
  client: DerivTradingClient,
  settings: TouchTradeSettings,
  barrier: string,
  gate?: TouchBuyGate,
): Promise<TouchBuyResult> {
  const quoted = await proposeOneTouch(client, settings, barrier)
  if (!quoted.proposalId) throw new Error(`No proposal id for barrier ${barrier}`)

  // Final readiness gate: after quote, before purchase (covers delayed proposals).
  if (gate?.beforeBuy) await gate.beforeBuy()

  const ask = quoted.ask ?? settings.stake
  const buyMsg = await client.request({
    buy: quoted.proposalId,
    price: ask,
  })

  const buy = buyMsg.buy as Record<string, unknown> | undefined
  const contractId = brokerNumber(buy?.contract_id)
  if (contractId == null || !Number.isSafeInteger(contractId) || contractId <= 0) {
    throw new Error('Broker did not confirm a purchase with a contract ID')
  }
  return {
    barrier,
    proposalId: quoted.proposalId,
    contractId,
    buyPrice: brokerNumber(buy?.buy_price),
    payout: brokerNumber(buy?.payout),
    entrySpot: brokerNumber(buy?.entry_spot),
    raw: buyMsg,
  }
}

export async function placeTouchTrades(
  client: DerivTradingClient,
  settings: TouchTradeSettings,
  signal: FinalSignal,
  gate?: TouchBuyGate,
): Promise<TouchBuyResult[]> {
  const barriers = barriersForSignal(signal, settings)
  if (!barriers.length) return []
  const results: TouchBuyResult[] = []
  // Sequential buys avoid rate-limit / proposal races on the same socket.
  for (const barrier of barriers) {
    results.push(await buyOneTouch(client, settings, barrier, gate))
  }
  return results
}
