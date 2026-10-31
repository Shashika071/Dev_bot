import type { SignalHistoryRow, Tick } from '../types'

export const PAPER_DURATION = 60
const MAX_GAP = 10
const MAX_EXIT_DELAY = 5

export function startPaper(epoch: number, price: number): NonNullable<SignalHistoryRow['paper']> {
  return {
    entryEpoch: epoch, entryPrice: price, expiryEpoch: epoch + PAPER_DURATION,
    lastTickEpoch: epoch, status: 'PENDING',
  }
}

/** Simulated public-tick observations, never broker contract settlements. */
export function advancePaper(rows: SignalHistoryRow[], tick: Tick | null, healthy: boolean): SignalHistoryRow[] {
  let changed = false
  const next = rows.map((row) => {
    const p = row.paper
    if (!p || p.status !== 'PENDING') return row
    let reason: string | undefined
    if (!healthy) reason = 'Feed interrupted or page reloaded; outcome unknown'
    else if (tick && tick.epoch - p.lastTickEpoch > MAX_GAP) reason = 'Missing ticks during observation'
    else if (tick && tick.epoch > p.expiryEpoch + MAX_EXIT_DELAY) reason = 'No timely expiry tick'
    if (reason) {
      changed = true
      return { ...row, paper: { ...p, status: 'INVALID' as const, reason } }
    }
    if (!tick || tick.epoch <= p.lastTickEpoch) return row
    changed = true
    if (tick.epoch < p.expiryEpoch) return { ...row, paper: { ...p, lastTickEpoch: tick.epoch } }
    const won = row.signal === 'RISE' ? tick.quote > p.entryPrice : tick.quote < p.entryPrice
    return { ...row, paper: {
      ...p, lastTickEpoch: tick.epoch, exitEpoch: tick.epoch, exitPrice: tick.quote,
      status: won ? 'WIN' as const : 'LOSS' as const,
    } }
  })
  return changed ? next : rows
}
