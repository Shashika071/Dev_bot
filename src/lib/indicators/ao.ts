/** Awesome Oscillator: SMA(HL2,5) - SMA(HL2,34) */
import type { Candle } from '../types'
import { sma } from './sma'

export function computeAO(candles: Candle[], fast = 5, slow = 34): (number | null)[] {
  const hl2 = candles.map((c) => (c.high + c.low) / 2)
  const f = sma(hl2, fast)
  const s = sma(hl2, slow)
  return candles.map((_, i) =>
    f[i] != null && s[i] != null ? (f[i] as number) - (s[i] as number) : null,
  )
}
