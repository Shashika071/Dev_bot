import {
  MAX_OBSERVATIONS,
  type HorizonObservation,
  type SignalObservation,
  type FrozenAtrSnapshot,
  OBSERVATION_HORIZONS_MIN,
} from '../research/signalObservation'
import type { ConfirmationFlags } from '../types'

const KEY = 'v100_signal_observations_v1'
let memory: SignalObservation[] = []
let storageError: string | null = null

export function getObservationStorageError() {
  return storageError
}

export function getObservationRetentionLimit() {
  return MAX_OBSERVATIONS
}

function validFlags(f: unknown): f is ConfirmationFlags {
  if (!f || typeof f !== 'object') return false
  const o = f as ConfirmationFlags
  return (['ut', 'supertrend', 'sma', 'ao', 'rsi'] as const).every((k) => typeof o[k] === 'boolean')
}

function validAtr(a: unknown): a is FrozenAtrSnapshot {
  if (!a || typeof a !== 'object') return false
  const o = a as FrozenAtrSnapshot
  const numOrNull = (v: unknown) => v == null || Number.isFinite(v)
  return numOrNull(o.atr14) && numOrNull(o.atrPct) && numOrNull(o.candleRange)
    && numOrNull(o.rangeOverAtr) && numOrNull(o.lastCompletedClose) && numOrNull(o.lastCompletedTime)
    && ['below_1_atr', '1_to_2_atr', 'above_2_atr', 'unknown'].includes(o.sizeBucket)
}

function validHorizon(h: unknown): h is HorizonObservation {
  if (!h || typeof h !== 'object') return false
  const o = h as HorizonObservation
  return OBSERVATION_HORIZONS_MIN.includes(o.minutes as never)
    && Number.isFinite(o.targetEpoch)
    && ['continuation', 'reversal', 'unchanged', 'unknown', 'pending'].includes(o.status)
}

function validObservation(value: unknown): value is SignalObservation {
  if (!value || typeof value !== 'object') return false
  const r = value as SignalObservation
  return typeof r.id === 'string'
    && typeof r.symbol === 'string'
    && Number.isFinite(r.candleTime)
    && Number.isFinite(r.signalTickEpoch)
    && Number.isFinite(r.receiptEpoch)
    && (r.direction === 'RISE' || r.direction === 'FALL')
    && Number.isFinite(r.entryReferencePrice)
    && Number.isFinite(r.score)
    && Number.isFinite(r.passed)
    && Number.isFinite(r.total)
    && validFlags(r.flags)
    && typeof r.conditionKey === 'string'
    && Array.isArray(r.missingConditions)
    && typeof r.forming === 'boolean'
    && validAtr(r.atr)
    && (r.randomDirection === 'RISE' || r.randomDirection === 'FALL')
    && Number.isFinite(r.lastTickEpoch)
    && Array.isArray(r.horizons) && r.horizons.length === 4 && r.horizons.every(validHorizon)
    && typeof r.pending === 'boolean'
}

export function loadObservations(): SignalObservation[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return memory
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('Invalid observations')
    memory = [...new Map(parsed.filter(validObservation).map((r) => [r.id, r])).values()]
      .slice(-MAX_OBSERVATIONS)
    return memory
  } catch {
    storageError = 'Signal analysis storage unavailable or damaged. Export CSV to keep this session.'
    return memory
  }
}

export function saveObservations(rows: SignalObservation[]) {
  memory = rows.slice(-MAX_OBSERVATIONS)
  try {
    localStorage.setItem(KEY, JSON.stringify(memory))
    storageError = null
  } catch {
    storageError = 'Signal analysis cannot be saved in this browser. Export CSV before closing.'
  }
}

function csvEscape(value: string): string {
  return `"${value.replace(/"/g, '""')}"`
}

export function exportObservationsCsv(rows: SignalObservation[]): string {
  const header = [
    'id', 'symbol', 'candleTime', 'candleIso', 'signalTickEpoch', 'signalTickIso',
    'receiptEpoch', 'receiptIso', 'direction', 'entryReferencePrice',
    'score', 'passed', 'total', 'forming', 'conditionKey', 'missingConditions',
    'flags_ut', 'flags_supertrend', 'flags_sma', 'flags_ao', 'flags_rsi',
    'atr14', 'atrPct', 'candleRange', 'rangeOverAtr', 'sizeBucket',
    'lastCompletedClose', 'lastCompletedTime', 'randomDirection',
    'pending',
    ...OBSERVATION_HORIZONS_MIN.flatMap((m) => [
      `h${m}_status`, `h${m}_targetEpoch`, `h${m}_observedEpoch`, `h${m}_observedPrice`,
      `h${m}_delaySec`, `h${m}_signedMove`, `h${m}_signedMoveInAtr`,
      `h${m}_randomSignedMove`, `h${m}_randomClass`, `h${m}_reason`,
    ]),
  ]
  const lines = [header.join(',')]
  for (const r of rows) {
    const byMin = new Map(r.horizons.map((h) => [h.minutes, h]))
    const cells: (string | number | boolean)[] = [
      r.id, r.symbol, r.candleTime, new Date(r.candleTime * 1000).toISOString(),
      r.signalTickEpoch, new Date(r.signalTickEpoch * 1000).toISOString(),
      r.receiptEpoch, new Date(r.receiptEpoch * 1000).toISOString(),
      r.direction, r.entryReferencePrice,
      r.score, r.passed, r.total, r.forming,
      csvEscape(r.conditionKey), csvEscape(r.missingConditions.join('+')),
      r.flags.ut, r.flags.supertrend, r.flags.sma, r.flags.ao, r.flags.rsi,
      r.atr.atr14 ?? '', r.atr.atrPct ?? '', r.atr.candleRange ?? '',
      r.atr.rangeOverAtr ?? '', r.atr.sizeBucket,
      r.atr.lastCompletedClose ?? '', r.atr.lastCompletedTime ?? '',
      r.randomDirection, r.pending,
    ]
    for (const m of OBSERVATION_HORIZONS_MIN) {
      const h = byMin.get(m)
      cells.push(
        h?.status ?? '',
        h?.targetEpoch ?? '',
        h?.observedEpoch ?? '',
        h?.observedPrice ?? '',
        h?.delaySec ?? '',
        h?.signedMove ?? '',
        h?.signedMoveInAtr ?? '',
        h?.randomSignedMove ?? '',
        h?.randomClass ?? '',
        csvEscape(h?.reason ?? ''),
      )
    }
    lines.push(cells.join(','))
  }
  return lines.join('\n')
}
