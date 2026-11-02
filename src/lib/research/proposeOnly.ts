/**
 * Research-only ONETOUCH proposal helper.
 * MUST NEVER call buy. Uses public WS if possible; failures → unavailable.
 */
import type { FrozenContractSettings } from './contractSettings'
import { formatRelativeBarrier } from './touchPaper'

export interface ProposalQuote {
  ask: number
  payout: number
  barrier: string
  responseEpoch: number
  requestEpoch?: number
  rawError?: string
}

export type ProposalTransport = {
  request: (payload: Record<string, unknown>) => Promise<Record<string, unknown>>
}

/**
 * Request a proposal only. Callers must not follow with buy.
 */
export async function proposeOneTouchOnly(
  transport: ProposalTransport,
  settings: FrozenContractSettings,
  symbol: string,
  side: 'RISE' | 'FALL',
): Promise<ProposalQuote | null> {
  const barrier = formatRelativeBarrier(settings.barrierAbs, side === 'RISE' ? 1 : -1)
  const requestEpoch = Date.now() / 1000
  try {
    const msg = await transport.request({
      proposal: 1,
      amount: settings.paperStake,
      basis: 'stake',
      contract_type: 'ONETOUCH',
      currency: settings.currency,
      duration: settings.duration,
      duration_unit: settings.durationUnit,
      underlying_symbol: symbol,
      barrier,
    })
    if (msg.error) {
      return {
        ask: NaN,
        payout: NaN,
        barrier,
        requestEpoch, responseEpoch: Date.now() / 1000,
        rawError: String((msg.error as { message?: string }).message ?? 'proposal error'),
      }
    }
    const proposal = msg.proposal as { ask_price?: number; payout?: number } | undefined
    const ask = Number(proposal?.ask_price)
    const payout = Number(proposal?.payout)
    if (!Number.isFinite(ask) || !Number.isFinite(payout)) {
      return {
        ask: NaN,
        payout: NaN,
        barrier,
        requestEpoch, responseEpoch: Date.now() / 1000,
        rawError: 'Malformed proposal',
      }
    }
    return {
      ask,
      payout,
      barrier,
      requestEpoch, responseEpoch: Date.now() / 1000,
    }
  } catch (e) {
    return {
      ask: NaN,
      payout: NaN,
      barrier,
      requestEpoch, responseEpoch: Date.now() / 1000,
      rawError: e instanceof Error ? e.message : 'proposal failed',
    }
  }
}

/** Static guarantee for audits/tests: this module source must not contain buy calls. */
export const PROPOSE_ONLY_MODULE = true
