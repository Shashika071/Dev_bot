import type { Candle } from '../types'
import { atrRma } from '../indicators/atr'
import type { FrozenAtr } from './strategies/types'

export function freezeAtr14(candles: Candle[]): FrozenAtr {
  const closed = candles.filter((c) => c.isClosed)
  const empty: FrozenAtr = { atr14: null, atrPct: null, candleRange: null, rangeOverAtr: null }
  if (!closed.length) return empty
  const last = closed[closed.length - 1]!
  const candleRange = last.high - last.low
  if (closed.length < 14) {
    return { ...empty, candleRange }
  }
  const atr = atrRma(
    closed.map((c) => c.high),
    closed.map((c) => c.low),
    closed.map((c) => c.close),
    14,
  )[closed.length - 1]
  if (atr == null || !(atr > 0)) {
    return { atr14: atr != null && Number.isFinite(atr) ? atr : null, atrPct: null, candleRange, rangeOverAtr: null }
  }
  return {
    atr14: atr,
    atrPct: last.close !== 0 ? (100 * atr) / last.close : null,
    candleRange,
    rangeOverAtr: candleRange / atr,
  }
}
