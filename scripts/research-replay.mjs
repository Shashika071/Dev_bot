/** Replay the ordered history/tick/quote/control journal through the collector's reducer. */
import { createServer } from 'vite'
import { isDeepStrictEqual } from 'node:util'

const args = process.argv.slice(2)
const idArg = args.includes('--id') ? args[args.indexOf('--id') + 1] : null
const vite = await createServer({
  root: process.cwd(), configFile: false, logLevel: 'error',
  server: { middlewareMode: true, hmr: false }, appType: 'custom',
})

try {
  const { latestExperimentId, loadSession, readJournal } = await vite.ssrLoadModule('/src/lib/research/experimentStore.ts')
  const { RESEARCH_CODE_VERSION } = await vite.ssrLoadModule('/src/lib/research/experimentEngine.ts')
  const { emptyResearchSession, applyResearchEvent } = await vite.ssrLoadModule('/src/lib/research/researchSession.ts')
  const id = idArg || latestExperimentId()
  if (!id) throw new Error('No experiment found')
  const saved = loadSession(id)
  if (!saved || saved.state.codeVersion !== RESEARCH_CODE_VERSION) {
    throw new Error('Exact replay requires a current-engine session checkpoint and its journal. Legacy tick-only exports cannot reproduce quotes or initial history.')
  }
  const events = readJournal(id)
  if (!Number.isInteger(saved.sequence) || saved.sequence < 1 || events.length < saved.sequence) {
    throw new Error('Journal is missing or shorter than the saved checkpoint; exact replay cannot be verified')
  }
  if (saved.state.startedAt != null && !events.slice(0, saved.sequence).some((e) => e.type === 'start')) {
    throw new Error('Journal is missing the initial experiment settings and candle history')
  }
  let replay = emptyResearchSession()
  for (const event of events.slice(0, saved.sequence)) replay = applyResearchEvent(replay, event)
  // Normalize exactly as JSON checkpoints do (optional undefined fields are omitted).
  const normalized = JSON.parse(JSON.stringify(replay))
  const matches = isDeepStrictEqual(normalized, saved)
  console.log(JSON.stringify({
    experimentId: id, checkpointEvents: saved.sequence,
    journalEventsAfterCheckpoint: events.length - saved.sequence,
    replayObservations: replay.state.observations.length,
    savedObservations: saved.state.observations.length,
    fullCheckpointMatch: matches,
    stateMatch: isDeepStrictEqual(normalized.state, saved.state),
    candlesMatch: isDeepStrictEqual(normalized.candles, saved.candles),
  }, null, 2))
  if (!matches) process.exitCode = 1
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
} finally {
  await vite.close()
}
