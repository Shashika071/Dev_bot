// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { FOCUSED_CONFIRMATION_IDS, isFocusedConfirmation } from './focusedConfirmation'
import { getStrategy, STRATEGY_REGISTRY } from './strategies/registry'
import { MASKS } from './strategies/maskStrategies'
import type { StrategyContext } from './strategies/types'
import type { Candle } from '../types'
import type { EnrichedSeries } from '../strategy/signalEngine'
import { buildResearchExtras } from './strategies/buildExtras'
import {
  createInitialState,
  onTick,
  startDiscovery,
  RESEARCH_CODE_VERSION,
} from './experimentEngine'
import { archiveBrowserResearch, loadBrowserResearch, saveBrowserResearch } from './browserResearchStore'
import { exportObservationsCsv, summarizeExperiment } from './summarizeExperiment'
import { advanceTouchPaper, maybeStartTouchPaper } from './touchPaper'
import { DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import { makeHorizons } from './horizons'

function blankSeries(n: number, close = 100): EnrichedSeries {
  const candles: Candle[] = Array.from({ length: n }, (_, i) => ({
    time: 1_800_000_000 + i * 60,
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    isClosed: true,
  }))
  return {
    candles,
    utTrail: new Array(n).fill(close - 1),
    utBuy: new Array(n).fill(false),
    utSell: new Array(n).fill(false),
    stLine: new Array(n).fill(close - 1),
    stDir: new Array(n).fill(0),
    sma20: new Array(n).fill(close - 1),
    smaRising: new Array(n).fill(false),
    smaFalling: new Array(n).fill(false),
    ao: new Array(n).fill(0),
    rsi: new Array(n).fill(50),
  }
}

/** RISE: fresh UT + ST + SMA + RSI; AO intentionally disagrees (negative / falling). */
function riseAoDisagreesCtx(): StrategyContext {
  const series = blankSeries(80, 110)
  const i = series.candles.length - 1
  series.utBuy[i] = true
  series.utSell[i] = false
  series.stDir[i] = 1
  series.sma20[i] = 100
  series.smaRising[i] = true
  series.smaFalling[i] = false
  series.rsi[i] = 58
  series.ao[i] = -0.5
  series.ao[i - 1] = -0.2
  const candles = series.candles
  return {
    candles,
    closedCount: candles.length,
    index: i,
    series,
    extras: buildResearchExtras(candles),
    feedHealthy: true,
    nowEpoch: candles[i]!.time + 60,
  }
}

describe('focused confirmation B1 / C5 / C1', () => {
  it('registers C5 without AO and C1 with AO; B1/C5/C1 never use forming candles', () => {
    expect(MASKS.C5).toEqual(['ut', 'supertrend', 'sma', 'rsi'])
    expect(MASKS.C5).not.toContain('ao')
    expect(MASKS.C1).toEqual(['ut', 'supertrend', 'sma', 'ao', 'rsi'])
    for (const id of FOCUSED_CONFIRMATION_IDS) {
      expect(getStrategy(id).usesFormingCandle).toBe(false)
    }
    expect(isFocusedConfirmation(FOCUSED_CONFIRMATION_IDS)).toBe(true)
    expect(isFocusedConfirmation(STRATEGY_REGISTRY.map((s) => s.id))).toBe(false)
  })

  it('C5 accepts when AO disagrees; C1 rejects the same candle; B1 still qualifies at 4/5', () => {
    const ctx = riseAoDisagreesCtx()
    const c5 = getStrategy('C5').evaluate(ctx)
    const c1 = getStrategy('C1').evaluate(ctx)
    const b1 = getStrategy('B1').evaluate(ctx)
    expect(c5?.side).toBe('RISE')
    expect(c5?.flags?.ao).toBe(false)
    expect(c5?.metrics?.ao).toBe(-0.5)
    expect(c1).toBeNull()
    expect(b1?.side).toBe('RISE')
    expect(b1?.passed).toBe(4)
  })

  it('C5 rejects without a fresh UT crossover even when the other three pass', () => {
    const ctx = riseAoDisagreesCtx()
    ctx.series.utBuy[ctx.index] = false
    ctx.series.utSell[ctx.index] = false
    expect(getStrategy('C5').evaluate(ctx)).toBeNull()
    expect(getStrategy('C1').evaluate(ctx)).toBeNull()
  })

  it('C5 rejects when Supertrend, SMA, or RSI fails', () => {
    for (const sabotage of ['st', 'sma', 'rsi'] as const) {
      const ctx = riseAoDisagreesCtx()
      if (sabotage === 'st') ctx.series.stDir[ctx.index] = -1
      if (sabotage === 'sma') ctx.series.smaRising[ctx.index] = false
      if (sabotage === 'rsi') ctx.series.rsi[ctx.index] = 75
      expect(getStrategy('C5').evaluate(ctx), sabotage).toBeNull()
    }
  })

  it('forming candles cannot trigger B1/C5/C1 via the experiment engine', () => {
    const closed = Array.from({ length: 80 }, (_, i) => ({
      time: 1_700_000_000 + i * 60,
      open: 100,
      high: 101,
      low: 99,
      close: 100 + (i % 3) * 0.01,
      isClosed: true,
    }))
    const forming = {
      time: closed[closed.length - 1]!.time + 60,
      open: 100,
      high: 120,
      low: 99,
      close: 119,
      isClosed: false,
    }
    let state = startDiscovery(
      createInitialState('confirmation', [...FOCUSED_CONFIRMATION_IDS]),
      closed[0]!.time * 1000,
    )
    // Mid-minute tick on a forming bar — closed strategies only evaluate at close+0..5s.
    const mid = forming.time + 30
    state = onTick(
      state,
      { epoch: mid, quote: 119, symbol: 'R_100' },
      [...closed, forming],
      mid,
      true,
    )
    expect(state.observations.every((o) => !o.forming)).toBe(true)
    expect(state.observations.filter((o) => FOCUSED_CONFIRMATION_IDS.includes(o.strategyId))).toHaveLength(0)
  })

  it('keeps an early hit after later disconnect and rejects post-expiry touches', () => {
    const tick = { epoch: 1000, quote: 100, symbol: 'R_100' }
    let obs = maybeStartTouchPaper({
      id: 'C5:1:RISE',
      strategyId: 'C5',
      side: 'RISE',
      candleTime: 940,
      signalEpoch: 1000,
      receiptEpoch: 1000,
      entryReferencePrice: 100,
      entryTick: tick,
      forming: false,
      reason: 'C5',
      atr: { atr14: 1, atrPct: 1, candleRange: 1, rangeOverAtr: 1 },
      lastTickEpoch: 1000,
      horizons: makeHorizons(1000),
      pendingHorizons: true,
    }, DEFAULT_FROZEN_CONTRACT, tick)

    obs = advanceTouchPaper(obs, { epoch: 1004, quote: 100.1, symbol: 'R_100' }, true, DEFAULT_FROZEN_CONTRACT)
    expect(obs.touch?.status).toBe('hit')
    expect(obs.touch?.firstTouchEpoch).toBe(1004)
    obs = advanceTouchPaper(obs, null, false, DEFAULT_FROZEN_CONTRACT)
    expect(obs.touch?.status).toBe('hit')

    let miss = maybeStartTouchPaper({
      id: 'C5:2:RISE',
      strategyId: 'C5',
      side: 'RISE',
      candleTime: 940,
      signalEpoch: 2000,
      receiptEpoch: 2000,
      entryReferencePrice: 100,
      entryTick: { epoch: 2000, quote: 100, symbol: 'R_100' },
      forming: false,
      reason: 'C5',
      atr: { atr14: 1, atrPct: 1, candleRange: 1, rangeOverAtr: 1 },
      lastTickEpoch: 2000,
      horizons: makeHorizons(2000),
      pendingHorizons: true,
    }, DEFAULT_FROZEN_CONTRACT, { epoch: 2000, quote: 100, symbol: 'R_100' })
    const expiry = 2000 + 7 * 60
    for (let e = 2001; e < expiry; e += 5) {
      miss = advanceTouchPaper(miss, { epoch: e, quote: 100.01, symbol: 'R_100' }, true, DEFAULT_FROZEN_CONTRACT)
    }
    miss = advanceTouchPaper(miss, { epoch: expiry + 3, quote: 100.2, symbol: 'R_100' }, true, DEFAULT_FROZEN_CONTRACT)
    expect(miss.touch?.status).toBe('miss')
    expect(miss.touch?.firstTouchEpoch).toBeUndefined()
  })

  it('archives prior runs when starting focused confirmation and preserves discovery IDs elsewhere', () => {
    localStorage.clear()
    const prior = startDiscovery(createInitialState('discovery'), 1_700_000_000_000)
    prior.observations.push({
      id: 'S6:1:RISE',
      strategyId: 'S6',
      side: 'RISE',
      candleTime: 1,
      signalEpoch: 2,
      receiptEpoch: 2,
      entryReferencePrice: 1,
      entryTick: { epoch: 2, quote: 1, symbol: 'R_100' },
      forming: false,
      reason: 'kept',
      atr: { atr14: null, atrPct: null, candleRange: null, rangeOverAtr: null },
      lastTickEpoch: 2,
      horizons: [],
      pendingHorizons: false,
    })
    saveBrowserResearch(prior)
    archiveBrowserResearch(prior)
    const focused = startDiscovery(
      createInitialState('confirmation', [...FOCUSED_CONFIRMATION_IDS]),
      Date.now(),
    )
    saveBrowserResearch(focused)
    const live = loadBrowserResearch()
    expect(live.enabledStrategyIds).toEqual(FOCUSED_CONFIRMATION_IDS)
    expect(live.observations).toHaveLength(0)
    expect(live.codeVersion).toBe(RESEARCH_CODE_VERSION)
    const archivedKeys = Object.keys(localStorage).filter((k) => k.includes('_archive_'))
    expect(archivedKeys.length).toBeGreaterThan(0)
    const archived = JSON.parse(localStorage.getItem(archivedKeys[0]!)!)
    expect(archived.observations).toHaveLength(1)
    expect(archived.observations[0].strategyId).toBe('S6')
    localStorage.clear()
  })

  it('exports condition flags, indicator snapshot, and frozen contract settings', () => {
    const ctx = riseAoDisagreesCtx()
    const hit = getStrategy('C5').evaluate(ctx)!
    const state = {
      ...createInitialState('confirmation', [...FOCUSED_CONFIRMATION_IDS]),
      observations: [{
        id: 'C5:1:RISE',
        strategyId: 'C5' as const,
        side: 'RISE' as const,
        candleTime: 1000,
        signalEpoch: 1060,
        receiptEpoch: 1060,
        entryReferencePrice: 110,
        entryTick: { epoch: 1060, quote: 110, symbol: 'R_100' },
        forming: false,
        reason: hit.reason,
        flags: hit.flags,
        passed: hit.passed,
        total: hit.total,
        metrics: hit.metrics,
        atr: { atr14: 1, atrPct: 1, candleRange: 1, rangeOverAtr: 1 },
        lastTickEpoch: 1060,
        horizons: makeHorizons(1060),
        pendingHorizons: false,
        touch: {
          status: 'hit' as const,
          absoluteBarrier: 110.05,
          firstTouchEpoch: 1100,
          reason: 'hit',
          quoteAvailable: false,
          quoteStatus: 'unavailable' as const,
        },
      }],
    }
    const csv = exportObservationsCsv(state)
    expect(csv).toContain('flagUt,flagSupertrend,flagSma,flagAo,flagRsi')
    expect(csv).toContain('frozenBarrierAbs')
    expect(csv).toContain('"true"')
    expect(csv).toContain('"false"') // AO flag
    expect(csv).toContain('0.05')
    const summary = summarizeExperiment(state)
    expect(summary.focusedConfirmation).toBe(true)
    expect(summary.strategies.map((s) => s.id).sort()).toEqual(['B1', 'C1', 'C5'])
  })
})
