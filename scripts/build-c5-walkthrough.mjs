// Inject compact, audited local chart examples into the literal visualization fragment.
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const source = resolve('research-output/c5-filter-screen-2026-11-11/chart-examples.json')
const output = 'C:/Users/Admin/.codex/visualizations/2026/11/05/01a1ac7f-0751-7af0-af26-60d04ebf7b05/c5-market-context.html'
const rows = JSON.parse(await readFile(source, 'utf8'))
const data = rows.map(row => ({
  signalTime: row.signalTime, confirmedTime: row.confirmedTime, side: row.side,
  entry: row.entry, target: row.target, hit: row.hit, firstTouchMinute: row.firstTouchMinute,
  chop14: row.chop14, context: row.context,
  selection: row.selection,
  candles: row.candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })),
}))
if (!data.length || data.some(row => row.candles.filter(c => c.time > row.signalTime).length !== 9)) throw new Error('Incomplete chart examples')
const fragment = await readFile(output, 'utf8')
const json = JSON.stringify(data).replaceAll('<', '\\u003c')
const injected = fragment.replace(/(<script type="application\/json" id="c5-example-data">)[\s\S]*?(<\/script>)/, (_, prefix, suffix) => prefix + json + suffix)
if (injected === fragment && fragment.includes('__C5_EXAMPLE_DATA__')) throw new Error('Data injection failed')
await writeFile(output, injected)
console.log(JSON.stringify({ output, examples: data.length, bytes: Buffer.byteLength(injected) }))
