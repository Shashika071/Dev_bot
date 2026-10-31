import type { SignalHistoryRow } from '../types'

const KEY = 'v100_smart_signal_history_v1'
let memory: SignalHistoryRow[] = []
let storageError: string | null = null

export function getStorageError() { return storageError }

function validRow(value: unknown): value is SignalHistoryRow {
  if (!value || typeof value !== 'object') return false
  const r = value as SignalHistoryRow
  const p = r.paper
  return typeof r.id === 'string' && Number.isFinite(r.timestamp)
    && Number.isFinite(r.candleClose) && (r.notifyPrice == null || Number.isFinite(r.notifyPrice))
    && ['RISE', 'FALL', 'NO_TRADE', 'UT_BUY_REJECTED', 'UT_SELL_REJECTED'].includes(r.signal)
    && [-1, 0, 1].includes(r.stDir) && ['BULLISH', 'BEARISH', 'FLAT'].includes(r.smaDir)
    && (r.ao == null || Number.isFinite(r.ao)) && (r.rsi == null || Number.isFinite(r.rsi))
    && Array.isArray(r.failures) && r.failures.every((s) => typeof s === 'string')
    && typeof r.isQualified === 'boolean'
    && (!p || ([p.entryEpoch, p.entryPrice, p.expiryEpoch, p.lastTickEpoch].every(Number.isFinite)
      && ['PENDING', 'WIN', 'LOSS', 'INVALID'].includes(p.status)
      && (p.exitPrice == null || Number.isFinite(p.exitPrice))))
}

export function loadHistory(): SignalHistoryRow[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return memory
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('Invalid history')
    memory = [...new Map(parsed.filter(validRow).map((r) => [r.id, r])).values()].slice(-2000)
    return memory
  } catch {
    storageError = 'History storage unavailable or damaged. Export CSV to keep this session.'
    return memory
  }
}

export function saveHistory(rows: SignalHistoryRow[]) {
  memory = rows.slice(-2000)
  try {
    localStorage.setItem(KEY, JSON.stringify(memory))
    storageError = null
  } catch {
    storageError = 'History cannot be saved in this browser. Export CSV before closing.'
  }
}

export function appendHistory(row: SignalHistoryRow): SignalHistoryRow[] {
  const next = [...loadHistory().filter((r) => r.id !== row.id), row].slice(-2000)
  saveHistory(next)
  return next
}

export function exportCsv(rows: SignalHistoryRow[]): string {
  const header = [
    'timestamp',
    'iso',
    'signal',
    'candleClose',
    'notifyPrice',
    'stDir',
    'smaDir',
    'ao',
    'rsi',
    'connection',
    'isQualified',
    'failures',
    'notifyEpoch', 'paperStatus', 'paperEntryEpoch', 'paperEntryPrice',
    'paperExpiryEpoch', 'paperExitEpoch', 'paperExitPrice', 'paperReason',
  ]
  const lines = [header.join(',')]
  for (const r of rows) {
    lines.push(
      [
        r.timestamp,
        new Date(r.timestamp * 1000).toISOString(),
        r.signal,
        r.candleClose,
        r.notifyPrice ?? '',
        r.stDir,
        r.smaDir,
        r.ao ?? '',
        r.rsi ?? '',
        r.connection,
        r.isQualified,
        `"${r.failures.join('; ').replace(/"/g, '""')}"`,
        r.notifyEpoch ?? '', r.paper?.status ?? '', r.paper?.entryEpoch ?? '',
        r.paper?.entryPrice ?? '', r.paper?.expiryEpoch ?? '', r.paper?.exitEpoch ?? '',
        r.paper?.exitPrice ?? '', `"${(r.paper?.reason ?? '').replace(/"/g, '""')}"`,
      ].join(','),
    )
  }
  return lines.join('\n')
}
