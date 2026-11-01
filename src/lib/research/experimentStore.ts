/**
 * Append-only JSONL research store under research-data/.
 */
import { mkdirSync, appendFileSync, readFileSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import type { ExperimentState } from './experimentEngine'
import { createInitialState } from './experimentEngine'

export const RESEARCH_DATA_ROOT = 'research-data'

export function experimentDir(id: string): string {
  return join(RESEARCH_DATA_ROOT, id)
}

export function ensureExperimentDir(id: string): string {
  const dir = experimentDir(id)
  mkdirSync(dir, { recursive: true })
  return dir
}

function appendJsonl(path: string, row: unknown): void {
  appendFileSync(path, `${JSON.stringify(row)}\n`, 'utf8')
}

export function writeManifest(id: string, state: ExperimentState): void {
  const dir = ensureExperimentDir(id)
  const path = join(dir, 'manifest.json')
  // Persist compact state without full observation arrays for quick resume meta;
  // full snapshots go to state.jsonl
  const meta = {
    id,
    mode: state.mode,
    stage: state.stage,
    startedAt: state.startedAt,
    endsAt: state.endsAt,
    finishedAt: state.finishedAt,
    codeVersion: state.codeVersion,
    contract: state.contract,
    enabledStrategyIds: state.enabledStrategyIds,
    observationCount: state.observations.length,
    rejectedCount: state.rejected.length,
    downtimeSec: state.downtimeSec,
    lastTickEpoch: state.lastTickEpoch,
    feedHealthy: state.feedHealthy,
    updatedAt: Math.floor(Date.now() / 1000),
  }
  writeFileSync(path, JSON.stringify(meta, null, 2), 'utf8')
}

export function appendTick(id: string, tick: unknown): void {
  appendJsonl(join(ensureExperimentDir(id), 'ticks.jsonl'), tick)
}

export function appendEvent(id: string, event: unknown): void {
  appendJsonl(join(ensureExperimentDir(id), 'events.jsonl'), event)
}

export function appendStateSnapshot(id: string, state: ExperimentState): void {
  // Store lean snapshot for resume
  const lean = {
    ...state,
    // keep observations; needed for resume
  }
  writeFileSync(join(ensureExperimentDir(id), 'state.json'), JSON.stringify(lean), 'utf8')
  writeManifest(id, state)
}

export function loadState(id: string): ExperimentState | null {
  const path = join(experimentDir(id), 'state.json')
  if (!existsSync(path)) return null
  try {
    const raw = JSON.parse(readFileSync(path, 'utf8')) as ExperimentState
    return raw
  } catch {
    return null
  }
}

export function listExperiments(): string[] {
  if (!existsSync(RESEARCH_DATA_ROOT)) return []
  return readdirSync(RESEARCH_DATA_ROOT, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
}

export function latestExperimentId(): string | null {
  const ids = listExperiments()
  if (!ids.length) return null
  return ids.sort().at(-1) ?? null
}

export function newExperimentId(mode: 'discovery' | 'confirmation'): string {
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  return `${mode}-${ts}`
}

export function bootstrapIfMissing(id: string): ExperimentState {
  const existing = loadState(id)
  if (existing) return existing
  const state = createInitialState()
  appendStateSnapshot(id, state)
  return state
}
