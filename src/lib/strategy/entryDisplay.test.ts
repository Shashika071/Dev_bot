import { beforeEach, describe, expect, it, vi } from 'vitest'
import { deriveEntryDisplay, type EntryDisplayInput } from './entryDisplay'
import { evaluateC5LatestClosed } from './c5Strategy'
import { computeLiveSetupConfidence } from './liveConfidence'
import type { SignalEvaluation } from '../types'

vi.mock('./c5Strategy', async importOriginal => ({
  ...await importOriginal<typeof import('./c5Strategy')>(),
  evaluateC5LatestClosed: vi.fn(),
}))

function fixture(): EntryDisplayInput {
  const liveSetup = computeLiveSetupConfidence([])
  liveSetup.ready = true
  liveSetup.forming = true
  liveSetup.candleTime = 6000
  liveSetup.lean = 'RISE'
  liveSetup.rise.passed = 4
  liveSetup.rise.flags = { ut: true, supertrend: true, sma: true, rsi: true, ao: false }
  const liveSignal: SignalEvaluation = {
    time: 6000, final: 'RISE', rawUt: 'BUY', confirmations: { ...liveSetup.rise.flags },
    failures: [], close: 100, stDir: 1, smaRising: true, smaFalling: false,
    ao: -1, rsi: 60, isQualified: true, strategyId: 'C5', requiredPassed: 4, requiredTotal: 4,
  }
  return {
    market: { status: 'CONNECTED', ready: true, symbol: 'R_100',
      candles: [{ time: 6000, open: 100, high: 100.1, low: 99.9, close: 100.02, isClosed: false }],
      lastPrice: 100.02, lastTickEpoch: 6005, lastConfirmedCandleTime: 5940, error: null },
    liveSignal, liveSetup, signalMeta: { candleCloseEpoch: 6005, expiresAt: 6015, remainingSec: 10 },
    nowSec: 6006, barrierAbs: .01, duration: 9, durationUnit: 'm',
  }
}

beforeEach(() => vi.mocked(evaluateC5LatestClosed).mockReset())

describe('entry display freshness and source', () => {
  it('shows an agreeing open candle only as forming, with a moving reference', () => {
    const result = deriveEntryDisplay(fixture())
    expect(result.state).toBe('FORMING')
    expect(result.side).toBe('RISE')
    expect(result.target).toBeCloseTo(100.03)
    expect(result.drift).toBeCloseTo(.02)
    expect(result.expiresInSeconds).toBe(9)
    expect(evaluateC5LatestClosed).not.toHaveBeenCalled()
  })
  it('expires by the clock even when remainingSec is stale', () => {
    const input = fixture()
    input.nowSec = 6015
    expect(deriveEntryDisplay(input)).toMatchObject({ state: 'WAIT', entryReference: null, target: null })
  })
  it('expires if a tick has reached the deadline despite a lagging local clock', () => {
    const input = fixture()
    input.nowSec = 6014
    input.market!.lastTickEpoch = 6015
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
  })
  it.each(['DISCONNECTED', 'RECONNECTING'] as const)('hides entries while %s', status => {
    const input = fixture()
    input.market!.status = status
    expect(deriveEntryDisplay(input)).toMatchObject({ state: 'WAIT', side: null, target: null })
  })
  it('hides entries while syncing, stale, or the tick clock is in the future', () => {
    const input = fixture()
    input.market!.ready = false
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
    input.market!.ready = true
    input.market!.lastTickEpoch = 5990
    expect(deriveEntryDisplay(input).reason).toContain('stale')
    input.market!.lastTickEpoch = 6100
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
  })
  it('withdraws a forming alert when a required condition changes', () => {
    const input = fixture()
    input.liveSetup.rise.flags.ut = false
    expect(deriveEntryDisplay(input)).toMatchObject({ state: 'WAIT', side: null })
    expect(deriveEntryDisplay(input).reason).toContain('Setup changed')
  })
  it('does not treat a failed closed candle as a confirmed old intrabar alert', () => {
    const input = fixture()
    input.market!.candles[0].time = 5940
    input.liveSignal!.time = 5940
    input.market!.candles[0].isClosed = true
    vi.mocked(evaluateC5LatestClosed).mockReturnValue({ ...input.liveSignal!, final: 'NO_TRADE', isQualified: false })
    expect(deriveEntryDisplay(input).reason).toContain('did not confirm')
  })
  it('confirms only a newly recomputed matching closed candle', () => {
    const input = fixture()
    input.market!.candles[0].time = 5940
    input.liveSignal!.time = 5940
    input.market!.candles[0].isClosed = true
    vi.mocked(evaluateC5LatestClosed).mockReturnValue(input.liveSignal)
    expect(deriveEntryDisplay(input).state).toBe('CONFIRMED')
    vi.mocked(evaluateC5LatestClosed).mockReturnValue({ ...input.liveSignal!, time: 5880 })
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
  })
  it('does not confirm a candle marked closed before its closing time', () => {
    const input = fixture()
    input.market!.candles[0].isClosed = true
    vi.mocked(evaluateC5LatestClosed).mockReturnValue(input.liveSignal)
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
  })
  it('rejects invalid settings and never supplies an invalid target', () => {
    const input = fixture()
    input.barrierAbs = Number.NaN
    expect(deriveEntryDisplay(input).target).toBeNull()
    input.barrierAbs = .01
    input.durationUnit = 'invalid'
    expect(deriveEntryDisplay(input).state).toBe('WAIT')
    input.durationUnit = 'm'
    input.market!.lastPrice = Number.NaN
    expect(deriveEntryDisplay(input).entryReference).toBeNull()
  })
  it('keeps a partial setup as a watch direction, not an entry', () => {
    const input = fixture()
    input.liveSignal = null
    input.liveSetup.rise.passed = 3
    expect(deriveEntryDisplay(input)).toMatchObject({ state: 'WAIT', watchSide: 'RISE', side: null, entryReference: null })
  })
})
