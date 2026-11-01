/**
 * Deterministic replay from ticks.jsonl + initial history is limited:
 * this replays recorded ticks through onTick starting from empty/warmup candles
 * rebuilt from tick stream when possible.
 */
import { createServer } from 'vite'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'

const args = process.argv.slice(2)
const idArg = args.includes('--id') ? args[args.indexOf('--id') + 1] : null
const vite = await createServer({
  root: process.cwd(), configFile: false, logLevel: 'error',
  server: { middlewareMode: true, hmr: false }, appType: 'custom',
})
const { latestExperimentId, loadState, experimentDir } = await vite.ssrLoadModule('/src/lib/research/experimentStore.ts')
const { createInitialState, startDiscovery, onTick } = await vite.ssrLoadModule('/src/lib/research/experimentEngine.ts')
const { applyTick } = await vite.ssrLoadModule('/src/lib/deriv/candles.ts')

const id = idArg || latestExperimentId()
if (!id) { console.error('No experiment'); process.exit(1) }
const saved = loadState(id)
const ticksPath = resolve(experimentDir(id), 'ticks.jsonl')
if (!existsSync(ticksPath)) { console.error('No ticks.jsonl'); process.exit(1) }

const lines = readFileSync(ticksPath, 'utf8').split('\n').filter(Boolean)
let state = startDiscovery(createInitialState(saved?.mode ?? 'discovery', saved?.enabledStrategyIds), (saved?.startedAt ?? Math.floor(Date.now() / 1000)) * 1000)
// Preserve original endsAt for deadline behaviour
if (saved?.endsAt) state = { ...state, endsAt: saved.endsAt, startedAt: saved.startedAt }

let candles = []
for (const line of lines) {
  const row = JSON.parse(line)
  const tick = { epoch: row.epoch, quote: row.quote, symbol: row.symbol || 'R_100' }
  const applied = applyTick(candles, tick)
  candles = applied.candles
  state = onTick(state, tick, candles, row.receiptMs ? row.receiptMs / 1000 : tick.epoch, true)
}

console.log(JSON.stringify({
  experimentId: id,
  replayObservations: state.observations.length,
  savedObservations: saved?.observations?.length ?? null,
  stage: state.stage,
  matchCount: saved ? state.observations.length === saved.observations.length : null,
}, null, 2))
await vite.close()
