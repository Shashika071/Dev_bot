import { describe, expect, it } from 'vitest'
import { barriersForSignal, formatTouchBarrier } from './touchTrade'

describe('touch barriers', () => {
  it('formats signed relative barriers', () => {
    expect(formatTouchBarrier(0.05, 1)).toBe('+0.05')
    expect(formatTouchBarrier(0.05, -1)).toBe('-0.05')
    expect(formatTouchBarrier(0.03, 1)).toBe('+0.03')
  })

  it('rejects non-positive barrier', () => {
    expect(() => formatTouchBarrier(0, 1)).toThrow()
    expect(() => formatTouchBarrier(-1, 1)).toThrow()
  })

  it('both mode returns + and −', () => {
    expect(barriersForSignal('RISE', {
      symbol: 'R_100', currency: 'USD', stake: 1, barrierAbs: 0.05,
      duration: 7, durationUnit: 'm', mode: 'both',
    })).toEqual(['+0.05', '-0.05'])
  })

  it('signal mode follows RISE/FALL', () => {
    const base = {
      symbol: 'R_100', currency: 'USD', stake: 1, barrierAbs: 0.03,
      duration: 5, durationUnit: 'm' as const, mode: 'signal' as const,
    }
    expect(barriersForSignal('RISE', base)).toEqual(['+0.03'])
    expect(barriersForSignal('FALL', base)).toEqual(['-0.03'])
    expect(barriersForSignal('NO_TRADE', base)).toEqual([])
  })
})
