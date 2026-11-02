import { afterAll, describe, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { spawn } from 'node:child_process'
import type { Candle } from '../types'
import { createInitialState, startDiscovery } from './experimentEngine'
import { applyResearchEvent, emptyResearchSession, type ResearchEvent } from './researchSession'

const testParent = resolve('research-output', 'tests')
mkdirSync(testParent, { recursive: true })
const root = mkdtempSync(join(testParent, 'deriv-research-test-'))
afterAll(() => {
  vi.unstubAllEnvs()
  if (resolve(root).startsWith(testParent + '\\deriv-research-test-')
    || resolve(root).startsWith(testParent + '/deriv-research-test-')) {
    rmSync(root, { recursive: true, force: true })
  }
})

function sampleEvents(): ResearchEvent[] {
  const entry = 1_700_000_040
  const candles: Candle[] = Array.from({ length: 260 }, (_, i) => ({
    time: entry - (260 - i) * 60, open: 100 + i / 10,
    high: 100.2 + i / 10, low: 99.9 + i / 10, close: 100.1 + i / 10, isClosed: true,
  }))
  const events: ResearchEvent[] = [{ type: 'start',
    state: startDiscovery(createInitialState('discovery', ['S6', 'S7']), entry * 1000), candles },
  { type: 'tick', tick: { epoch: entry, quote: 126, symbol: 'R_100' }, receiptEpoch: entry + 0.1, healthy: true },
  { type: 'quote', ids: [`S6:${entry - 60}:RISE`, `S7:${entry - 60}:FALL`],
    quote: { ask: 1, payout: 1.03, requestEpoch: entry + 0.1, responseEpoch: entry + 0.3 } },
  { type: 'stop_entries' }]
  for (let offset = 2; offset <= 424; offset += 2) {
    events.push({ type: 'tick', tick: { epoch: entry + offset,
      quote: offset < 4 ? 126 : 126.2, symbol: 'R_100' }, receiptEpoch: entry + offset + 0.1, healthy: true })
  }
  events.push({ type: 'clock', now: entry + 440 })
  return events
}

describe('research persistence and replay', () => {
  it('replays history, paired entries, quotes and settlement exactly across a checkpoint', async () => {
    vi.stubEnv('RESEARCH_DATA_ROOT', root)
    const store = await import('./experimentStore')
    const id = 'discovery-2026-11-02T00-00-00-000Z'
    const events = sampleEvents()
    let session = emptyResearchSession()
    for (const [i, event] of events.entries()) {
      store.appendJournal(id, event)
      session = applyResearchEvent(session, event)
      if (i === 4) store.appendSessionSnapshot(id, session)
    }
    const checkpoint = store.loadSession(id)!
    let resumed = checkpoint
    for (const event of store.readJournal(id).slice(checkpoint.sequence)) resumed = applyResearchEvent(resumed, event)
    expect(JSON.parse(JSON.stringify(resumed))).toEqual(JSON.parse(JSON.stringify(session)))
    expect(session.state.observations.map((o) => o.strategyId)).toEqual(['S6', 'S7'])
    expect(session.state.observations.map((o) => o.touch?.quotedPath?.status)).toEqual(['hit', 'miss'])
    expect(session.state.stage).toBe('completed')
    store.appendSessionSnapshot(id, session)

    const result = await new Promise<{ code: number | null; output: string }>((done, fail) => {
      const child = spawn(process.execPath, ['scripts/research-replay.mjs', '--id', id], {
        cwd: process.cwd(), env: { ...process.env, RESEARCH_DATA_ROOT: root }, windowsHide: true,
      })
      let output = ''
      child.stdout.on('data', (s) => { output += String(s) })
      child.stderr.on('data', (s) => { output += String(s) })
      child.on('error', fail)
      child.on('exit', (code) => done({ code, output }))
    })
    expect(result.output).toContain('"fullCheckpointMatch": true')
    expect(result.code).toBe(0)
  }, 30_000)

  it('preserves active run selection and repairs only an incomplete journal tail', async () => {
    const store = await import('./experimentStore')
    const older = 'discovery-2026-11-02T00-00-00-000Z'
    const newer = 'confirmation-2026-11-02T01-00-00-000Z'
    store.appendSessionSnapshot(newer, emptyResearchSession())
    expect(store.latestExperimentId()).toBe(newer)
    store.setActiveExperiment(older)
    expect(store.latestExperimentId()).toBe(older)
    store.setActiveExperiment(newer)
    expect(store.latestExperimentId()).toBe(newer)
    const path = join(store.experimentDir(newer), 'journal.jsonl')
    writeFileSync(path, '{"type":"clock","now":1000}\n{"type":')
    expect(store.readJournal(newer, true)).toEqual([{ type: 'clock', now: 1000 }])
    expect(readFileSync(path, 'utf8')).toBe('{"type":"clock","now":1000}\n')
    expect(store.loadSession(older)?.state.observations).toHaveLength(2)
    expect(() => store.experimentDir('../elsewhere')).toThrow('Invalid experiment ID')
  })
})
