import type { Candle } from './types'

export function fixtureCandles(n: number, start = 1_800_000_000): Candle[] {
  let price = 1000
  let seed = 12345
  return Array.from({ length: n }, (_, i) => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    const open = price
    price += (seed / 4294967296 - 0.5) * 6
    return { time: start + i * 60, open, high: Math.max(open, price) + 0.5,
      low: Math.min(open, price) - 0.5, close: price, isClosed: true }
  })
}
