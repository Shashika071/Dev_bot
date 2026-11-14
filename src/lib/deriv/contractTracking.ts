import type { TradingMessage } from './tradingClient'

/** One durable record per broker purchase. Browser history may be cleared separately. */
const PREFIX = 'v100_contract_v1:'

export type TrackedContract = {
  accountId: string
  contractId: number
  source: 'manual' | 'auto'
  attemptId: string | null
  side: 'RISE' | 'FALL'
  currency: string
  barrier: string
  absoluteBarrier: number | null
  entrySpot: number | null
  buyPrice: number | null
  payout: number | null
  settled: boolean
  status: string
  profit: number | null
}

export type ManualSettlement = TrackedContract & { source: 'manual'; attemptId: string }

export function trackingKey(accountId: string, contractId: number): string {
  return PREFIX + encodeURIComponent(accountId) + ':' + contractId
}

export function loadTrackedContracts(): Map<string, TrackedContract> {
  const rows = new Map<string, TrackedContract>()
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (!key?.startsWith(PREFIX)) continue
    try {
      const parsed = JSON.parse(localStorage.getItem(key)!) as Partial<TrackedContract>
      if (typeof parsed?.accountId !== 'string' || !Number.isSafeInteger(parsed.contractId)
        || key !== trackingKey(parsed.accountId, parsed.contractId!)) continue
      const row = {
        ...parsed,
        currency: typeof parsed.currency === 'string' && parsed.currency ? parsed.currency : 'USD',
      } as TrackedContract
      rows.set(key, row)
    } catch {
      // One corrupt receipt must not hide every other pending contract.
    }
  }
  return rows
}

export function brokerNumber(raw: unknown): number | null {
  const n = typeof raw === 'number' || (typeof raw === 'string' && raw.trim()) ? Number(raw) : NaN
  return Number.isFinite(n) ? n : null
}

export function reconcileContract(current: TrackedContract, detail: TradingMessage): TrackedContract {
  if (brokerNumber(detail.contract_id) !== current.contractId) return current
  const status = typeof detail.status === 'string' ? detail.status.toLowerCase() : null
  const finished = detail.is_sold === true || brokerNumber(detail.is_sold) === 1
    || status === 'won' || status === 'lost' || status === 'sold' || status === 'cancelled'
  // Ignore an older open snapshot that arrives after a terminal subscription update.
  if (current.settled && !finished) return current
  const barrier = detail.barrier
  const numericBarrier = typeof barrier === 'string' && /^[+-]/.test(barrier) ? null : brokerNumber(barrier)
  const next: TrackedContract = {
    ...current,
    status: finished ? (status && status !== 'open' ? status : 'sold') : current.status,
    settled: finished,
    profit: finished ? brokerNumber(detail.profit) ?? current.profit : null,
    entrySpot: brokerNumber(detail.entry_spot) ?? current.entrySpot,
    absoluteBarrier: numericBarrier ?? current.absoluteBarrier,
    buyPrice: brokerNumber(detail.buy_price) ?? current.buyPrice,
    payout: brokerNumber(detail.payout) ?? current.payout,
  }
  return Object.keys(next).some(k => next[k as keyof TrackedContract] !== current[k as keyof TrackedContract]) ? next : current
}

export function isManualSettlement(row: TrackedContract): row is ManualSettlement {
  return row.source === 'manual' && !!row.attemptId && row.settled
}
