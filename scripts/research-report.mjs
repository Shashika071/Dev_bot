/** Write summary JSON + markdown for latest (or --id) experiment. */
import { createServer } from 'vite'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const args = process.argv.slice(2)
const idArg = args.includes('--id') ? args[args.indexOf('--id') + 1] : null
const vite = await createServer({
  root: process.cwd(), configFile: false, logLevel: 'error',
  server: { middlewareMode: true, hmr: false }, appType: 'custom',
})
const { latestExperimentId, loadState } = await vite.ssrLoadModule('/src/lib/research/experimentStore.ts')
const { summarizeExperiment, exportObservationsCsv, exportReportMarkdown } =
  await vite.ssrLoadModule('/src/lib/research/summarizeExperiment.ts')

const id = idArg || latestExperimentId()
if (!id) {
  console.error('No experiment found under research-data/')
  process.exit(1)
}
const state = loadState(id)
if (!state) {
  console.error('Missing state for', id)
  process.exit(1)
}
const outDir = resolve('research-output', id)
await mkdir(outDir, { recursive: true })
const summary = summarizeExperiment(state)
await writeFile(resolve(outDir, 'summary.json'), JSON.stringify(summary, null, 2))
await writeFile(resolve(outDir, 'REPORT.md'), exportReportMarkdown(summary))
await writeFile(resolve(outDir, 'observations.csv'), exportObservationsCsv(state))
console.log('Wrote', outDir)
await vite.close()
