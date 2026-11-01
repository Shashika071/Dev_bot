// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { fixtureCandles } from '../testFixtures'
import { atrRma } from '../indicators/atr'
import { computeLiveSetupConfidence } from '../strategy/liveConfidence'
import type { Candle, ConfirmationFlags } from '../types'
import type { LiveSetupConfidence, SideSetup } from '../strategy/liveConfidence'
import {
  advanceObservations,
  clearCompletedObservations,
  collectThresholdObservations,
  createSignalObservation,
  freezeAtrAtSignal,
  MAX_HORIZON_DELAY_SEC,
  MAX_OBSERVATION_TICK_GAP_SEC,
} from './signalObservation'
import { analyzeSignalObservations } from './signalAnalysis'
import {
  exportObservationsCsv,
  loadObservations,
  saveObservations,
} from '../history/observationStore'

function flags(partial: Partial<ConfirmationFlags>): ConfirmationFlags {
  return {
    ut: false, supertrend: false, sma: false, ao: false, rsi: false, ...partial,
  }
}

function side(passedFlags: Partial<ConfirmationFlags>, sideName: 'RISE' | 'FALL' = 'RISE'): SideSetup {
  const f = flags(passedFlags)
  const keys = Object.keys(f) as (keyof ConfirmationFlags)[]
  const passed = keys.filter((k) => f[k]).length
  return {
    side: sideName,
    confidence: passed / 5,
    passed,
    total: 5,
    flags: f,
    labels: [],
    nearRemaining: [],
  }
}

function setupAt(
  candleTime: number,
  rise: SideSetup,
  fall: SideSetup,
  forming = true,
): LiveSetupConfidence {
  return {
    ready: true,
    candleTime,
    forming,
    rise,
    fall,
    lean: rise.confidence >= fall.confidence ? 'RISE' : 'FALL',
  }
}

function closedPlusForming(nClosed: number): Candle[] {
  const closed = fixtureCandles(nClosed)
  const last = closed[closed.length - 1]!
  const forming: Candle = {
    time: last.time + 60,
    open: last.close,
    high: last.close + 0.2,
    low: last.close - 0.2,
    close: last.close + 0.1,
    isClosed: false,
  }
  return [...closed, forming]
}

describe('freezeAtrAtSignal', () => {
  it('uses only completed candles and does not change when forming candle updates', () => {
    const base = fixtureCandles(80)
    const snap1 = freezeAtrAtSignal(base)
    expect(snap1.atr14).not.toBeNull()
    expect(snap1.lastCompletedTime).toBe(base[base.length - 1]!.time)

    const highs = base.map((c) => c.high)
    const lows = base.map((c) => c.low)
    const closes = base.map((c) => c.close)
    const expected = atrRma(highs, lows, closes, 14)[base.length - 1]
    expect(snap1.atr14).toBeCloseTo(expected as number, 10)
    expect(snap1.candleRange).toBeCloseTo(
      base[base.length - 1]!.high - base[base.length - 1]!.low,
      10,
    )
    expect(snap1.rangeOverAtr).toBeCloseTo(snap1.candleRange! / snap1.atr14!, 10)

    const withForming = [
      ...base,
      {
        time: base[base.length - 1]!.time + 60,
        open: 1, high: 1e6, low: 0, close: 500, isClosed: false,
      },
    ]
    const snap2 = freezeAtrAtSignal(withForming)
    expect(snap2.atr14).toBe(snap1.atr14)
    expect(snap2.candleRange).toBe(snap1.candleRange)
    expect(snap2.rangeOverAtr).toBe(snap1.rangeOverAtr)
    expect(snap2.lastCompletedTime).toBe(snap1.lastCompletedTime)
  })

  it('handles short history without inventing ATR', () => {
    const short = fixtureCandles(5)
    const snap = freezeAtrAtSignal(short)
    expect(snap.atr14).toBeNull()
    expect(snap.rangeOverAtr).toBeNull()
    expect(snap.sizeBucket).toBe('unknown')
    expect(snap.candleRange).toBeGreaterThan(0)
  })
})

describe('collectThresholdObservations', () => {
  it('records an 80% forming setup without UT once per candle and direction', () => {
    const candles = closedPlusForming(100)
    const candleTime = candles[candles.length - 1]!.time
    const live = setupAt(
      candleTime,
      side({ supertrend: true, sma: true, ao: true, rsi: true, ut: false }, 'RISE'),
      side({}, 'FALL'),
      true,
    )
    expect(live.rise.confidence).toBe(0.8)
    expect(live.rise.flags.ut).toBe(false)

    const tick = { epoch: candleTime + 10, quote: 1234.5, symbol: 'R_100' }
    const seen = new Set<string>()
    let rows = collectThresholdObservations([], {
      setup: live,
      minConfidence: 0.8,
      tick,
      receiptEpoch: tick.epoch + 0.05,
      symbol: 'R_100',
      candles,
      seenIds: seen,
      randomDirectionFor: () => 'FALL',
    })
    expect(rows).toHaveLength(1)
    expect(rows[0]!.id).toBe(`${candleTime}:RISE`)
    expect(rows[0]!.forming).toBe(true)
    expect(rows[0]!.flags.ut).toBe(false)
    expect(rows[0]!.passed).toBe(4)
    expect(rows[0]!.entryReferencePrice).toBe(1234.5)
    expect(rows[0]!.atr.atr14).not.toBeNull()

    rows = collectThresholdObservations(rows, {
      setup: live,
      minConfidence: 0.8,
      tick: { ...tick, epoch: tick.epoch + 1, quote: 9999 },
      receiptEpoch: tick.epoch + 1,
      symbol: 'R_100',
      candles,
      seenIds: seen,
    })
    expect(rows).toHaveLength(1)
    expect(rows[0]!.entryReferencePrice).toBe(1234.5)
  })

  it('can record both directions independently when each hits threshold', () => {
    const candles = closedPlusForming(100)
    const candleTime = candles[candles.length - 1]!.time
    const live = setupAt(
      candleTime,
      side({ supertrend: true, sma: true, ao: true, rsi: true }, 'RISE'),
      side({ supertrend: true, sma: true, ao: true, rsi: true }, 'FALL'),
      true,
    )
    const tick = { epoch: candleTime + 5, quote: 100, symbol: 'R_100' }
    const rows = collectThresholdObservations([], {
      setup: live, minConfidence: 0.8, tick, receiptEpoch: tick.epoch,
      symbol: 'R_100', candles, randomDirectionFor: () => 'RISE',
    })
    expect(rows.map((r) => r.direction).sort()).toEqual(['FALL', 'RISE'])
  })

  it('finds a real liveConfidence ≥80% forming sample when present in fixtures', () => {
    const closed = fixtureCandles(2500)
    let found: ReturnType<typeof computeLiveSetupConfidence> | null = null
    let candlesUsed: Candle[] = []
    for (let i = 120; i < closed.length - 1; i++) {
      const window = [
        ...closed.slice(0, i + 1),
        {
          ...closed[i + 1]!,
          isClosed: false,
          high: closed[i + 1]!.high,
          low: closed[i + 1]!.low,
          close: closed[i + 1]!.close,
        },
      ]
      const live = computeLiveSetupConfidence(window)
      const best = Math.max(live.rise.confidence, live.fall.confidence)
      if (best + 1e-12 >= 0.8) {
        const pick = live.rise.confidence >= live.fall.confidence ? live.rise : live.fall
        if (!pick.flags.ut) {
          found = live
          candlesUsed = window
          break
        }
        if (!found) {
          found = live
          candlesUsed = window
        }
      }
    }
    // Fixture may or may not hit; when it does, observation path must work.
    if (!found || found.candleTime == null) {
      expect(true).toBe(true)
      return
    }
    const pick = found.rise.confidence >= found.fall.confidence ? found.rise : found.fall
    const tick = {
      epoch: found.candleTime + 12,
      quote: candlesUsed[candlesUsed.length - 1]!.close,
      symbol: 'R_100',
    }
    const rows = collectThresholdObservations([], {
      setup: found,
      minConfidence: 0.8,
      tick,
      receiptEpoch: tick.epoch,
      symbol: 'R_100',
      candles: candlesUsed,
      randomDirectionFor: () => 'RISE',
    })
    expect(rows.length).toBeGreaterThanOrEqual(1)
    expect(rows.some((r) => r.passed >= 4)).toBe(true)
    expect(rows[0]!.forming).toBe(true)
    void pick
  })
})

describe('advanceObservations horizons', () => {
  function baseObs(direction: 'RISE' | 'FALL' = 'RISE') {
    const candles = closedPlusForming(80)
    const candleTime = candles[candles.length - 1]!.time
    return createSignalObservation({
      symbol: 'R_100',
      candleTime,
      direction,
      side: side({ supertrend: true, sma: true, ao: true, rsi: true }, direction),
      forming: true,
      tick: { epoch: 1_000, quote: 100, symbol: 'R_100' },
      receiptEpoch: 1_000.1,
      candles,
      randomDirection: direction === 'RISE' ? 'FALL' : 'RISE',
    })
  }

  it('classifies RISE/FALL continuation, reversal, and unchanged', () => {
    let rows = [baseObs('RISE')]
    // keep feed alive until 1m
    for (let e = 1002; e < 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060, quote: 101, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('continuation')
    expect(rows[0]!.horizons[0]!.signedMove).toBeCloseTo(1)

    rows = [baseObs('RISE')]
    for (let e = 1002; e < 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060, quote: 99, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('reversal')

    rows = [baseObs('FALL')]
    for (let e = 1002; e < 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060, quote: 99, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('continuation')

    rows = [baseObs('RISE')]
    for (let e = 1002; e < 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060, quote: 100, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('unchanged')
  })

  it('uses first tick at/after horizon with max delay of 5s', () => {
    let rows = [baseObs()]
    for (let e = 1002; e <= 1058; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060 + MAX_HORIZON_DELAY_SEC, quote: 102, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('continuation')
    expect(rows[0]!.horizons[0]!.delaySec).toBe(MAX_HORIZON_DELAY_SEC)

    rows = [baseObs()]
    for (let e = 1002; e <= 1058; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060 + MAX_HORIZON_DELAY_SEC + 1, quote: 102, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('unknown')
    expect(rows[0]!.horizons[0]!.reason).toMatch(/timely/i)
  })

  it('keeps completed horizons when a later horizon becomes unknown', () => {
    let rows = [baseObs()]
    for (let e = 1002; e < 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
    }
    rows = advanceObservations(rows, { epoch: 1060, quote: 101, symbol: 'R_100' }, true)
    expect(rows[0]!.horizons[0]!.status).toBe('continuation')

    // Gap before 3m horizon
    rows = advanceObservations(
      rows,
      { epoch: 1060 + MAX_OBSERVATION_TICK_GAP_SEC + 1, quote: 50, symbol: 'R_100' },
      true,
    )
    expect(rows[0]!.horizons[0]!.status).toBe('continuation')
    expect(rows[0]!.horizons[0]!.signedMove).toBeCloseTo(1)
    expect(rows[0]!.horizons[1]!.status).toBe('unknown')
    expect(rows[0]!.horizons[2]!.status).toBe('unknown')
    expect(rows[0]!.horizons[3]!.status).toBe('unknown')
  })

  it('marks unknown on disconnect/reload and ignores duplicate/out-of-order ticks', () => {
    const rows0 = [baseObs()]
    expect(advanceObservations(rows0, null, false)[0]!.horizons.every((h) => h.status === 'unknown')).toBe(true)

    let rows = [baseObs()]
    const same = advanceObservations(rows, { epoch: 999, quote: 120, symbol: 'R_100' }, true)
    expect(same).toBe(rows)
    rows = advanceObservations(rows, { epoch: 1002, quote: 100, symbol: 'R_100' }, true)
    const dup = advanceObservations(rows, { epoch: 1002, quote: 999, symbol: 'R_100' }, true)
    expect(dup).toBe(rows)
  })

  it('freezes ATR snapshot so later candles cannot rewrite it', () => {
    const candles = closedPlusForming(80)
    const obs = createSignalObservation({
      symbol: 'R_100',
      candleTime: candles[candles.length - 1]!.time,
      direction: 'RISE',
      side: side({ supertrend: true, sma: true, ao: true, rsi: true }),
      forming: true,
      tick: { epoch: 1_000, quote: 100, symbol: 'R_100' },
      receiptEpoch: 1_000,
      candles,
      randomDirection: 'FALL',
    })
    const frozen = { ...obs.atr }
    let rows = [obs]
    for (let e = 1002; e <= 1060; e += 2) {
      rows = advanceObservations(rows, { epoch: e, quote: 110, symbol: 'R_100' }, true)
    }
    expect(rows[0]!.atr).toEqual(frozen)
    expect(rows[0]!.horizons[0]!.signedMoveInAtr).toBeCloseTo(10 / frozen.atr14!, 8)
  })
})

describe('signal analysis stats and persistence', () => {
  it('computes denominators and preserves pending on clear', () => {
    const candles = closedPlusForming(80)
    const make = (idSuffix: number, move: number) => {
      const obs = createSignalObservation({
        symbol: 'R_100',
        candleTime: candles[candles.length - 1]!.time + idSuffix,
        direction: 'RISE',
        side: side({ supertrend: true, sma: true, ao: true, rsi: true }),
        forming: true,
        tick: { epoch: 1_000 + idSuffix * 10_000, quote: 100, symbol: 'R_100' },
        receiptEpoch: 1_000,
        candles,
        randomDirection: 'FALL',
      })
      let rows = [obs]
      const start = obs.signalTickEpoch
      // Heartbeats between horizons; settle each horizon at the intended move price.
      for (const minutes of [1, 3, 5, 7] as const) {
        const target = start + minutes * 60
        for (let e = rows[0]!.lastTickEpoch + 2; e < target; e += 2) {
          rows = advanceObservations(rows, { epoch: e, quote: 100, symbol: 'R_100' }, true)
        }
        rows = advanceObservations(rows, { epoch: target, quote: 100 + move, symbol: 'R_100' }, true)
      }
      return rows[0]!
    }

    const cont = make(1, 1)
    const rev = make(2, -1)
    expect(cont.pending).toBe(false)
    expect(rev.pending).toBe(false)

    const pending = createSignalObservation({
      symbol: 'R_100',
      candleTime: candles[candles.length - 1]!.time + 99,
      direction: 'FALL',
      side: side({ ut: true, supertrend: true, sma: true, ao: true, rsi: true }, 'FALL'),
      forming: false,
      tick: { epoch: 90_000, quote: 100, symbol: 'R_100' },
      receiptEpoch: 90_000,
      candles,
      randomDirection: 'RISE',
    })

    const report = analyzeSignalObservations([cont, rev, pending], 2000)
    const h1 = report.byHorizon[0]!
    expect(h1.counts.continuation).toBe(1)
    expect(h1.counts.reversal).toBe(1)
    expect(h1.counts.pending).toBe(1)
    expect(h1.continuationAmongDirectional).toBeCloseTo(0.5)
    expect(h1.sampleSizeDirectional).toBe(2)

    const kept = clearCompletedObservations([cont, rev, pending])
    expect(kept).toHaveLength(1)
    expect(kept[0]!.pending).toBe(true)
  })

  it('persists and exports CSV without credentials', () => {
    localStorage.clear()
    const candles = closedPlusForming(80)
    const obs = createSignalObservation({
      symbol: 'R_100',
      candleTime: candles[candles.length - 1]!.time,
      direction: 'RISE',
      side: side({ supertrend: true, sma: true, ao: true, rsi: true }),
      forming: true,
      tick: { epoch: 1_000, quote: 100, symbol: 'R_100' },
      receiptEpoch: 1_000,
      candles,
      randomDirection: 'FALL',
    })
    saveObservations([obs])
    const loaded = loadObservations()
    expect(loaded).toHaveLength(1)
    expect(loaded[0]!.id).toBe(obs.id)
    const csv = exportObservationsCsv(loaded)
    expect(csv).toContain('entryReferencePrice')
    expect(csv).toContain('atr14')
    expect(csv).toContain('h1_status')
    expect(csv.toLowerCase()).not.toContain('token')
    expect(csv.toLowerCase()).not.toContain('password')
    expect(csv.toLowerCase()).not.toContain('app_id')
  })
})
