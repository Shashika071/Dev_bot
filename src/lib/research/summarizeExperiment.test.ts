import { describe, expect, it } from 'vitest'
import { createInitialState } from './experimentEngine'
import { exportObservationsCsv, exportReportMarkdown, summarizeExperiment } from './summarizeExperiment'
import { makeHorizons } from './horizons'
import { maybeStartTouchPaper } from './touchPaper'
import { DEFAULT_FROZEN_CONTRACT } from './contractSettings'
import type { ResearchObservation } from './strategies/types'

function row(id: number, status: 'hit' | 'miss' | 'unknown' | 'pending', quote = true): ResearchObservation {
  const tick = { epoch: 1000, quote: 100, symbol: 'R_100' }
  const obs = maybeStartTouchPaper({
    id: `R1:${id}:RISE`, strategyId: 'R1', side: 'RISE', candleTime: 960,
    signalEpoch: 1000, receiptEpoch: 1000, entryReferencePrice: 100, entryTick: tick,
    forming: false, reason: 'Test, with "quoted" text',
    atr: { atr14: 1, atrPct: 1, candleRange: 1, rangeOverAtr: 1 },
    lastTickEpoch: 1000, pendingHorizons: false,
    horizons: makeHorizons(1000).map((h) => ({ ...h, status: 'unknown', reason: 'Feed interrupted or reload' })),
  }, DEFAULT_FROZEN_CONTRACT, tick)
  return { ...obs, touch: { ...obs.touch!, status, quoteAvailable: quote,
    quoteStatus: quote ? 'available' : 'unavailable', proposalAsk: quote ? 1 : null,
    proposalPayout: quote ? 1.03 : null, paperPnl: quote ? status === 'hit' ? 0.03 : status === 'miss' ? -1 : null : null,
    quotedPath: quote ? { paperEntryEpoch: 1002, paperEntryPrice: 100,
      absoluteBarrier: 100.05, status, reason: status === 'unknown' ? 'Incomplete tick coverage during contract window' : undefined } : undefined,
  } }
}

describe('research outcome coverage', () => {
  it('keeps settled-only costs aligned and bounds unknown quoted outcomes separately', () => {
    const state = { ...createInitialState('discovery', ['R1']),
      observations: [row(1, 'hit'), row(2, 'miss'), row(3, 'unknown'), row(4, 'pending'), row(5, 'hit', false)] }
    const s = summarizeExperiment(state).strategies[0]!
    expect(s.quotesAvailable).toBe(4)
    expect(s.quotedSettled).toBe(2)
    expect(s.paperCost).toBe(2)
    expect(s.paperPnl).toBeCloseTo(-0.97)
    expect(s.paperReturn).toBeCloseTo(-0.485)
    expect(s.quotedUnknown).toBe(1)
    expect(s.quotedPending).toBe(1)
    expect(s.pending).toBe(1)
    expect(s.known).toBe(3)
    expect(s.touchWinRate).toBeCloseTo(2 / 3)
    expect(s.quotedWinRate).toBe(0.5)
    expect(s.maturedQuotedCost).toBe(3)
    expect(s.paperPnlLower).toBeCloseTo(-1.97)
    expect(s.paperPnlUpper).toBeCloseTo(-0.94)
    expect(s.horizons.find((h) => h.minutes === 7)?.contRate).toBeNull()
  })

  it('reproduces the 592 wins / 28 unknowns issue without claiming a positive total return', () => {
    const state = { ...createInitialState('discovery', ['R1']), observations: [
      ...Array.from({ length: 592 }, (_, i) => row(i, 'hit')),
      ...Array.from({ length: 28 }, (_, i) => row(592 + i, 'unknown')),
      row(620, 'pending'),
    ] }
    const s = summarizeExperiment(state).strategies[0]!
    expect(s.paperPnl).toBeCloseTo(17.76)
    expect(s.paperPnlLower).toBeCloseTo(-10.24)
    expect(s.paperPnlUpper).toBeCloseTo(18.60)
    expect(s.maturedQuotedCost).toBe(620)
  })

  it('exports missing-outcome reasons and keeps legacy financial results unavailable', () => {
    const state = { ...createInitialState('discovery', ['R1']), observations: [row(1, 'unknown')] }
    const csv = exportObservationsCsv(state)
    expect(csv).toContain('h7Reason,h7TargetEpoch,h7ObservedEpoch')
    expect(csv).toContain('flagUt,flagSupertrend,flagSma,flagAo,flagRsi')
    expect(csv).toContain('frozenBarrierAbs')
    expect(csv).toContain('"Test, with ""quoted"" text"')
    expect(csv).toContain('"Incomplete tick coverage during contract window"')
    const summary = summarizeExperiment(state)
    const report = exportReportMarkdown(summary)
    expect(summary.primaryOutcome).toBe('touch_within_contract_window')
    expect(summary.touchWindowMinutes).toBe(7)
    expect(report).toContain('Signal Touch win%')
    expect(report).toContain('Quoted Touch win%')
    expect(report).not.toContain('7m cont%')
    expect(report).toContain('Optional endpoint diagnostics')
    const legacy = summarizeExperiment({ ...state, codeVersion: 'v100-research-engine-1' })
    expect(legacy.strategies[0]?.paperPnlLower).toBeNull()
    expect(legacy.strategies[0]?.paperPnl).toBeNull()
  })

  it('keeps every CSV row aligned for observations and rejects', () => {
    function colCount(line: string): number {
      let n = 0
      let inQ = false
      for (let i = 0; i <= line.length; i++) {
        const ch = line[i]
        if (ch === '"') inQ = !inQ
        if ((ch === ',' && !inQ) || i === line.length) n += 1
      }
      return n
    }
    const state = createInitialState('confirmation', ['R1', 'R2'])
    state.observations = [{
      ...row(1, 'hit'),
      id: 'R1:1:RISE',
      strategyId: 'R1',
      reason: 'R1 Supertrend flip signal',
      metrics: { stDir: 1, close: 100 },
    }]
    state.rejected = [{
      strategyId: 'R2',
      candleTime: 960,
      signalEpoch: 1000,
      reason: 'Cooldown active',
      side: 'RISE',
      metrics: { utBuy: true },
    }]
    const lines = exportObservationsCsv(state).split('\n')
    const width = colCount(lines[0]!)
    expect(width).toBeGreaterThan(60)
    for (const line of lines) expect(colCount(line)).toBe(width)
    expect(lines.some((l) => l.includes('R1:1:RISE'))).toBe(true)
    expect(lines.some((l) => l.includes('Cooldown active'))).toBe(true)
  })

  it('reports a Touch win while every direction diagnostic is still pending', () => {
    const obs = { ...row(1, 'hit'), horizons: makeHorizons(1000), pendingHorizons: true }
    const s = summarizeExperiment({ ...createInitialState('discovery', ['R1']), observations: [obs] }).strategies[0]!
    expect(s.pending).toBe(0)
    expect(s.known).toBe(1)
    expect(s.touchWinRate).toBe(1)
    expect(s.horizons.every((h) => h.pending === 1)).toBe(true)
  })
})
