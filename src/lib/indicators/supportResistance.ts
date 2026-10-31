/**
 * Confirmed swing pivots — Left=8, Right=5.
 * A pivot at i is only known after i+right closes. Chart-only (not a signal filter).
 */
import type { Candle, SRLevel } from '../types'
import { atrRma } from './atr'

export function computeSupportResistance(
  candles: Candle[],
  left = 8,
  right = 5,
  mergeAtrMult = 0.25,
): SRLevel[] {
  const n = candles.length
  if (n < left + right + 2) return []

  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr = atrRma(highs, lows, closes, 14)

  const raw: SRLevel[] = []
  for (let i = left; i < n - right; i++) {
    const windowH = highs.slice(i - left, i + right + 1)
    const windowL = lows.slice(i - left, i + right + 1)
    const maxH = Math.max(...windowH)
    const minL = Math.min(...windowL)
    if (highs[i] === maxH && windowH.filter((x) => x === maxH).length === 1) {
      raw.push({
        price: highs[i],
        type: 'resistance',
        confirmTime: candles[i + right].time,
        touches: 1,
        broken: false,
      })
    }
    if (lows[i] === minL && windowL.filter((x) => x === minL).length === 1) {
      raw.push({
        price: lows[i],
        type: 'support',
        confirmTime: candles[i + right].time,
        touches: 1,
        broken: false,
      })
    }
  }

  // Merge nearby same-type levels
  const lastAtr = [...atr].reverse().find((x) => x != null) ?? 1
  const tol = (lastAtr as number) * mergeAtrMult
  const merged: SRLevel[] = []
  for (const lvl of raw) {
    const near = merged.find(
      (m) => m.type === lvl.type && Math.abs(m.price - lvl.price) <= tol,
    )
    if (near) {
      near.price = (near.price * near.touches + lvl.price) / (near.touches + 1)
      near.touches += 1
      near.confirmTime = Math.max(near.confirmTime, lvl.confirmTime)
    } else merged.push({ ...lvl })
  }

  const lastClose = closes[n - 1]
  for (const m of merged) {
    if (m.type === 'resistance' && lastClose > m.price) m.broken = true
    if (m.type === 'support' && lastClose < m.price) m.broken = true
  }

  return merged
}

export function nearestLevels(
  levels: SRLevel[],
  price: number,
): { support: number | null; resistance: number | null } {
  const supports = levels
    .filter((l) => l.type === 'support' && !l.broken && l.price <= price)
    .map((l) => l.price)
  const resistances = levels
    .filter((l) => l.type === 'resistance' && !l.broken && l.price >= price)
    .map((l) => l.price)
  return {
    support: supports.length ? Math.max(...supports) : null,
    resistance: resistances.length ? Math.min(...resistances) : null,
  }
}
