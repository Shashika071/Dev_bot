// Research-only, causal chart context and candle excursion measurements.
export const HORIZON_MINUTES = 9
export const BARRIER = 0.01

export function validCandle(c) {
  return c && c.isClosed === true && [c.time, c.open, c.high, c.low, c.close].every(Number.isFinite)
    && c.time >= 0 && c.time % 60 === 0 && c.low > 0
    && c.low <= Math.min(c.open, c.close) && c.high >= Math.max(c.open, c.close)
}

export function auditCandles(candles) {
  let gaps = 0, missingMinutes = 0
  candles.forEach((c, i) => {
    if (!validCandle(c)) throw new Error(`Invalid or unfinished candle at index ${i}`)
    if (i && c.time <= candles[i - 1].time) throw new Error(`Nonascending or duplicate candle at ${i}`)
    if (i && c.time !== candles[i - 1].time + 60) {
      gaps++
      missingMinutes += (c.time - candles[i - 1].time) / 60 - 1
    }
  })
  return { bars: candles.length, gaps, missingMinutes }
}

// The current candle is deliberately excluded from its comparison channel.
export function chartContext(candles, i) {
  if (i < 20 || !validCandle(candles[i])) return null
  const previous = candles.slice(i - 20, i)
  if (previous.some((c, j) => !validCandle(c) || c.time !== candles[i].time - (20 - j) * 60)) return null
  const upper = Math.max(...previous.map(c => c.high))
  const lower = Math.min(...previous.map(c => c.low))
  const c = candles[i]
  const label = c.close > upper ? 'UP_BREAKOUT' : c.close < lower ? 'DOWN_BREAKOUT'
    : c.high > upper && c.low < lower ? 'BOTH_REJECTIONS'
      : c.high > upper ? 'UPPER_REJECTION' : c.low < lower ? 'LOWER_REJECTION' : 'INSIDE'
  return { label, upper, lower, change20: c.close - previous[0].close }
}

export function relativeContext(context, side) {
  if (context.label === 'UP_BREAKOUT') return side === 'RISE' ? 'ALIGNED_BREAKOUT' : 'OPPOSED_BREAKOUT'
  if (context.label === 'DOWN_BREAKOUT') return side === 'FALL' ? 'ALIGNED_BREAKOUT' : 'OPPOSED_BREAKOUT'
  if (context.label === 'UPPER_REJECTION') return side === 'FALL' ? 'ALIGNED_REJECTION' : 'OPPOSED_REJECTION'
  if (context.label === 'LOWER_REJECTION') return side === 'RISE' ? 'ALIGNED_REJECTION' : 'OPPOSED_REJECTION'
  return context.label
}

export function observeOpportunity(candles, i, side, minutes = HORIZON_MINUTES, barrier = BARRIER) {
  if (!['RISE', 'FALL'].includes(side) || !Number.isInteger(minutes) || minutes < 1 || !Number.isFinite(barrier) || barrier <= 0) throw new Error('Invalid observation parameters')
  const signal = candles[i]
  if (!validCandle(signal)) return null
  const future = candles.slice(i + 1, i + 1 + minutes)
  if (future.length !== minutes || future.some((c, j) => !validCandle(c) || c.time !== signal.time + (j + 1) * 60)) return null
  const entry = future[0].open
  const high = Math.max(...future.map(c => c.high)), low = Math.min(...future.map(c => c.low))
  const reaches = (a, b) => a + 1e-9 >= b
  const rise = side === 'RISE'
  const hit = reaches(rise ? high - entry : entry - low, barrier)
  const oppositeHit = reaches(rise ? entry - low : high - entry, barrier)
  const first = future.findIndex(c => reaches(rise ? c.high - entry : entry - c.low, barrier))
  return {
    signalTime: signal.time, confirmedTime: signal.time + 60, outcomeEndTime: future.at(-1).time + 60,
    side, entry, target: entry + (rise ? barrier : -barrier), signalClose: signal.close,
    hit, oppositeHit, bothHit: hit && oppositeHit, firstTouchMinute: first < 0 ? null : first + 1,
    signalCloseHit: reaches(rise ? high - signal.close : signal.close - low, barrier),
    terminalDirectionHit: rise ? future.at(-1).close > entry : future.at(-1).close < entry,
    outcomeHigh: high, outcomeLow: low,
  }
}

export function summarize(rows) {
  const n = rows.length, hits = rows.filter(r => r.hit).length, oppositeHits = rows.filter(r => r.oppositeHit).length
  return {
    n, hits, misses: n - hits, touchRate: n ? hits / n : null,
    oppositeHits, randomSideRate: n ? (hits + oppositeHits) / (2 * n) : null,
    edgeOverRandomSide: n ? (hits - oppositeHits) / (2 * n) : null,
    bothHits: rows.filter(r => r.bothHit).length,
    signalCloseTouchRate: n ? rows.filter(r => r.signalCloseHit).length / n : null,
    terminalDirectionRate: n ? rows.filter(r => r.terminalDirectionHit).length / n : null,
  }
}

// Resample whole UTC days, keeping all overlapping opportunities from a day together.
// Exploratory intervals: assumes days independent, no multiple-comparison correction.
export function dayBlockIntervals(rows, id, repetitions = 2000) {
  const byDay = new Map()
  for (const r of rows) {
    const day = Math.floor(r.confirmedTime / 86400)
    const v = byDay.get(day) ?? [0, 0, 0, 0, 0]
    v[0]++; v[1] += Number(r.hit)
    if (r.passes.includes(id)) { v[2]++; v[3] += Number(r.hit); v[4] += Number(r.oppositeHit) }
    byDay.set(day, v)
  }
  const days = [...byDay.values()], deltas = [], edges = []
  let state = 20261111
  const random = () => { state = (Math.imul(1664525, state) + 1013904223) >>> 0; return state / 4294967296 }
  for (let k = 0; k < repetitions; k++) {
    const sums = [0, 0, 0, 0, 0]
    for (let d = 0; d < days.length; d++) {
      const sample = days[Math.floor(random() * days.length)]
      for (let j = 0; j < 5; j++) sums[j] += sample[j]
    }
    if (sums[0] && sums[2]) {
      deltas.push(sums[3] / sums[2] - sums[1] / sums[0])
      edges.push((sums[3] - sums[4]) / (2 * sums[2]))
    }
  }
  const interval = arr => {
    arr.sort((a, b) => a - b)
    return arr.length ? [arr[Math.floor(arr.length * .025)], arr[Math.min(arr.length - 1, Math.floor(arr.length * .975))]] : null
  }
  return { days: days.length, repetitions, deltaVsC5: interval(deltas), edgeOverRandomSide: interval(edges) }
}
