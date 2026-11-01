import { trueRange } from './atr'

/**
 * Wilder DMI / ADX.
 * Seeding: first smoothed TR/+DM/-DM = sum of first `period` values (after bar 0).
 * Subsequent: wilder = prev - prev/period + current.
 * +DI = 100 * smooth+DM / smoothTR; -DI similar.
 * DX = 100 * |+DI - -DI| / (+DI + -DI); zero sum → DX unavailable.
 * ADX seeded as SMA of first `period` DX values, then Wilder-smoothed.
 */
export function computeDmiAdx(
  highs: number[],
  lows: number[],
  closes: number[],
  period = 14,
): {
  plusDi: (number | null)[]
  minusDi: (number | null)[]
  dx: (number | null)[]
  adx: (number | null)[]
} {
  const n = closes.length
  const plusDi: (number | null)[] = new Array(n).fill(null)
  const minusDi: (number | null)[] = new Array(n).fill(null)
  const dx: (number | null)[] = new Array(n).fill(null)
  const adx: (number | null)[] = new Array(n).fill(null)
  if (n < period + 1) return { plusDi, minusDi, dx, adx }

  const tr: number[] = new Array(n).fill(0)
  const pdm: number[] = new Array(n).fill(0)
  const mdm: number[] = new Array(n).fill(0)
  for (let i = 1; i < n; i++) {
    tr[i] = trueRange(highs[i]!, lows[i]!, closes[i - 1]!)
    const up = highs[i]! - highs[i - 1]!
    const down = lows[i - 1]! - lows[i]!
    if (up > down && up > 0) pdm[i] = up
    else if (down > up && down > 0) mdm[i] = down
  }

  // First smoothed values at index `period` use bars 1..period
  let str = 0
  let spdm = 0
  let smdm = 0
  for (let i = 1; i <= period; i++) {
    str += tr[i]!
    spdm += pdm[i]!
    smdm += mdm[i]!
  }

  const smoothTr: (number | null)[] = new Array(n).fill(null)
  const smoothPdm: (number | null)[] = new Array(n).fill(null)
  const smoothMdm: (number | null)[] = new Array(n).fill(null)
  smoothTr[period] = str
  smoothPdm[period] = spdm
  smoothMdm[period] = smdm

  for (let i = period + 1; i < n; i++) {
    str = str - str / period + tr[i]!
    spdm = spdm - spdm / period + pdm[i]!
    smdm = smdm - smdm / period + mdm[i]!
    smoothTr[i] = str
    smoothPdm[i] = spdm
    smoothMdm[i] = smdm
  }

  for (let i = period; i < n; i++) {
    const sTr = smoothTr[i]
    if (sTr == null || !(sTr > 0)) continue
    const pdi = 100 * (smoothPdm[i]! / sTr)
    const mdi = 100 * (smoothMdm[i]! / sTr)
    plusDi[i] = pdi
    minusDi[i] = mdi
    const den = pdi + mdi
    if (!(den > 0)) {
      dx[i] = null
      continue
    }
    dx[i] = 100 * Math.abs(pdi - mdi) / den
  }

  // ADX: need `period` DX values starting at index `period`
  const firstAdxIndex = period * 2 - 1
  if (n <= firstAdxIndex) return { plusDi, minusDi, dx, adx }
  let adxSum = 0
  let adxCount = 0
  for (let i = period; i <= firstAdxIndex; i++) {
    if (dx[i] == null) return { plusDi, minusDi, dx, adx }
    adxSum += dx[i]!
    adxCount += 1
  }
  if (adxCount !== period) return { plusDi, minusDi, dx, adx }
  let prevAdx = adxSum / period
  adx[firstAdxIndex] = prevAdx
  for (let i = firstAdxIndex + 1; i < n; i++) {
    if (dx[i] == null) continue
    prevAdx = (prevAdx * (period - 1) + dx[i]!) / period
    adx[i] = prevAdx
  }
  return { plusDi, minusDi, dx, adx }
}
