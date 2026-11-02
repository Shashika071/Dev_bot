import type { Candle, Tick } from '../types'

/** UTC minute open epoch for a tick timestamp */
export function minuteBucket(epochSec: number): number {
  return Math.floor(epochSec / 60) * 60
}

export function isValidCandle(c: Candle): boolean {
  return [c.time, c.open, c.high, c.low, c.close].every(Number.isFinite)
    && c.time % 60 === 0 && c.time >= 0
    && c.low > 0 && c.high >= Math.max(c.open, c.close)
    && c.low <= Math.min(c.open, c.close)
}

/** Never run recursive indicators across a missing or invalid candle. */
export function closedSegments(candles: Candle[]): Candle[][] {
  const segments: Candle[][] = []
  let segment: Candle[] = []
  for (const c of candles) {
    if (!c.isClosed || !isValidCandle(c)) {
      segment = []
      continue
    }
    if (!segment.length || c.time !== segment[segment.length - 1].time + 60) {
      segment = []
      segments.push(segment)
    }
    segment.push(c)
  }
  return segments
}

export function candlesFromHistory(
  rows: Array<{ epoch: number; open: number; high: number; low: number; close: number }>,
): Candle[] {
  return rows
    .map((r) => {
      const open = Number(r.open)
      const close = Number(r.close)
      const high = Number(r.high)
      const low = Number(r.low)
      // Snap epoch to minute and repair tiny OHLC float inversions from the feed.
      const time = Math.floor(Number(r.epoch) / 60) * 60
      return {
        time,
        open,
        high: Math.max(open, close, high),
        low: Math.min(open, close, low),
        close,
        isClosed: true,
      }
    })
    .filter(isValidCandle)
    .sort((a, b) => a.time - b.time)
}

/**
 * Apply a live tick into the candle series.
 * Returns { candles, closedCandle } where closedCandle is set when a minute rolls.
 */
export function applyTick(
  candles: Candle[],
  tick: Tick,
): { candles: Candle[]; closedCandle: Candle | null } {
  const bucket = minuteBucket(tick.epoch)
  const quote = tick.quote
  if (!Number.isFinite(tick.epoch) || !Number.isFinite(quote) || quote <= 0) {
    return { candles, closedCandle: null }
  }
  const next = candles.map((c) => ({ ...c }))
  let closedCandle: Candle | null = null

  if (next.length === 0) {
    next.push({
      time: bucket,
      open: quote,
      high: quote,
      low: quote,
      close: quote,
      isClosed: false,
    })
    return { candles: next, closedCandle: null }
  }

  const last = next[next.length - 1]

  if (bucket === last.time) {
    last.high = Math.max(last.high, quote)
    last.low = Math.min(last.low, quote)
    last.close = quote
    last.isClosed = false
    return { candles: next, closedCandle: null }
  }

  if (bucket > last.time) {
    // Finalize previous candle
    last.isClosed = true
    closedCandle = { ...last }
    // Fill any missing empty minutes? Prefer recover via history on reconnect.
    next.push({
      time: bucket,
      open: quote,
      high: quote,
      low: quote,
      close: quote,
      isClosed: false,
    })
    return { candles: next, closedCandle }
  }

  // Late/out-of-order tick for older bucket — ignore for OHLC integrity
  return { candles: next, closedCandle: null }
}

export function mergeHistory(existing: Candle[], history: Candle[]): Candle[] {
  const map = new Map<number, Candle>()
  for (const c of existing) map.set(c.time, { ...c })
  for (const c of history) {
    const prev = map.get(c.time)
    if (!prev || c.isClosed) map.set(c.time, { ...c })
  }
  return [...map.values()].sort((a, b) => a.time - b.time)
}

/** Mark all but the current UTC minute as closed */
export function finalizeByClock(candles: Candle[], nowEpoch = Math.floor(Date.now() / 1000)): Candle[] {
  const current = minuteBucket(nowEpoch)
  return candles.map((c) =>
    ({ ...c, isClosed: c.time < current }),
  )
}
