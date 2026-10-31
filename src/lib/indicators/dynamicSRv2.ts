/**
 * LonesomeTheBlue — Support Resistance Dynamic v2 (chart-only).
 * Port of the TradingView Pine logic; not used as a signal filter.
 */
import type { Candle } from '../types'

export interface DynamicSRv2Settings {
  pivotPeriod: number
  source: 'High/Low' | 'Close/Open'
  maxPivots: number
  channelWidthPct: number
  maxSR: number
  minStrength: number
  labelLocation: number
  lineStyle: 'Solid' | 'Dashed' | 'Dotted'
  lineWidth: number
  resistanceColor: string
  supportColor: string
  showPivotPoints: boolean
}

/** User / TradingView settings for this project */
export const DYNAMIC_SR_V2_DEFAULTS: DynamicSRv2Settings = {
  pivotPeriod: 7,
  source: 'High/Low',
  maxPivots: 30,
  channelWidthPct: 5,
  maxSR: 4,
  minStrength: 3,
  labelLocation: 20,
  lineStyle: 'Solid',
  lineWidth: 2,
  resistanceColor: '#ff1744',
  supportColor: '#00e676',
  showPivotPoints: false,
}

export interface DynamicSRLevel {
  /** Channel midpoint (drawn S/R line) */
  price: number
  hi: number
  lo: number
  strength: number
  type: 'support' | 'resistance'
  color: string
}

export interface DynamicSRPivot {
  time: number
  price: number
  kind: 'H' | 'L'
}

function isPivotHigh(src: number[], i: number, prd: number): boolean {
  const v = src[i]
  for (let k = i - prd; k <= i + prd; k++) {
    if (k === i) continue
    if (src[k] >= v) return false
  }
  return true
}

function isPivotLow(src: number[], i: number, prd: number): boolean {
  const v = src[i]
  for (let k = i - prd; k <= i + prd; k++) {
    if (k === i) continue
    if (src[k] <= v) return false
  }
  return true
}

function getSrVals(pivotvals: number[], ind: number, cwidth: number): [number, number, number] {
  let lo = pivotvals[ind]
  let hi = lo
  let numpp = 0
  for (let y = 0; y < pivotvals.length; y++) {
    const cpp = pivotvals[y]
    const wdth = cpp <= lo ? hi - cpp : cpp - lo
    if (wdth <= cwidth) {
      lo = cpp <= lo ? cpp : lo
      // Match Pine: hi := cpp > lo ? cpp : hi
      hi = cpp > lo ? cpp : hi
      numpp += 1
    }
  }
  return [hi, lo, numpp]
}

function findLoc(srStrength: number[], strength: number): number {
  let ret = srStrength.length
  if (ret > 0) {
    for (let i = srStrength.length - 1; i >= 0; i--) {
      if (strength <= srStrength[i]) break
      ret = i
    }
  }
  return ret
}

function checkSr(
  hi: number,
  lo: number,
  strength: number,
  srUp: number[],
  srDn: number[],
  srStrength: number[],
): boolean {
  for (let i = 0; i < srUp.length; ) {
    const included =
      (srUp[i] >= lo && srUp[i] <= hi) || (srDn[i] >= lo && srDn[i] <= hi)
    if (included) {
      if (strength >= srStrength[i]) {
        srStrength.splice(i, 1)
        srUp.splice(i, 1)
        srDn.splice(i, 1)
      } else {
        return false
      }
    } else {
      i += 1
    }
  }
  return true
}

export function computeDynamicSRv2(
  candles: Candle[],
  settings: DynamicSRv2Settings = DYNAMIC_SR_V2_DEFAULTS,
): { levels: DynamicSRLevel[]; pivots: DynamicSRPivot[] } {
  const {
    pivotPeriod: prd,
    source,
    maxPivots,
    channelWidthPct,
    maxSR,
    minStrength,
    resistanceColor,
    supportColor,
  } = settings

  const closed = candles.filter((c) => c.isClosed)
  const n = closed.length
  if (n < prd * 2 + 2) return { levels: [], pivots: [] }

  const src1 = closed.map((c) => (source === 'High/Low' ? c.high : Math.max(c.close, c.open)))
  const src2 = closed.map((c) => (source === 'High/Low' ? c.low : Math.min(c.close, c.open)))

  // Newest-first pivot list (Pine unshift), capped at maxPivots
  const pivotvals: number[] = []
  const pivots: DynamicSRPivot[] = []
  for (let i = prd; i < n - prd; i++) {
    const ph = isPivotHigh(src1, i, prd)
    const pl = isPivotLow(src2, i, prd)
    if (!ph && !pl) continue
    // Prefer high when both (matches Pine: ph ? ph : pl)
    const price = ph ? src1[i] : src2[i]
    const kind: 'H' | 'L' = ph ? 'H' : 'L'
    pivotvals.unshift(price)
    pivots.unshift({ time: closed[i].time, price, kind })
    if (pivotvals.length > maxPivots) {
      pivotvals.pop()
      pivots.pop()
    }
  }

  if (pivotvals.length === 0) return { levels: [], pivots: [] }

  const lookback = Math.min(300, n)
  let prdhighest = -Infinity
  let prdlowest = Infinity
  for (let i = n - lookback; i < n; i++) {
    prdhighest = Math.max(prdhighest, closed[i].high)
    prdlowest = Math.min(prdlowest, closed[i].low)
  }
  const cwidth = (prdhighest - prdlowest) * channelWidthPct / 100

  const srUp: number[] = []
  const srDn: number[] = []
  const srStrength: number[] = []

  for (let x = 0; x < pivotvals.length; x++) {
    const [hi, lo, strength] = getSrVals(pivotvals, x, cwidth)
    if (!checkSr(hi, lo, strength, srUp, srDn, srStrength)) continue
    const loc = findLoc(srStrength, strength)
    if (loc < maxSR && strength >= minStrength) {
      srStrength.splice(loc, 0, strength)
      srUp.splice(loc, 0, hi)
      srDn.splice(loc, 0, lo)
      if (srStrength.length > maxSR) {
        srStrength.pop()
        srUp.pop()
        srDn.pop()
      }
    }
  }

  const lastClose = closed[n - 1].close
  const levels: DynamicSRLevel[] = []
  for (let i = 0; i < srUp.length; i++) {
    const mid = (srUp[i] + srDn[i]) / 2
    const resistance = mid >= lastClose
    levels.push({
      price: mid,
      hi: srUp[i],
      lo: srDn[i],
      strength: srStrength[i],
      type: resistance ? 'resistance' : 'support',
      color: resistance ? resistanceColor : supportColor,
    })
  }

  return { levels, pivots }
}
