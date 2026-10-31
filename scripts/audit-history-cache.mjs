import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { PublicHistory } from './public-history.mjs'

const directory = resolve('research-output/touch-003')
const manifestPath = resolve(directory, 'download-manifest.json')
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
if (!manifest.complete) throw new Error('Finish backward download first')
const api = new PublicHistory()
const times = []
for (const page of manifest.pages) {
  const candles = JSON.parse(await readFile(resolve(directory, page.file), 'utf8'))
  for (const c of candles) times.push(c.time)
}
times.sort((a, b) => a - b)
const gaps = []
for (let i = 1; i < times.length; i++) {
  if (times[i] > times[i - 1] + 60) gaps.push({ start: times[i - 1] + 60, end: times[i] - 1 })
}
const convert = (msg) => (msg.candles || []).map((c) => ({ time: Number(c.epoch), open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close), isClosed: true }))
const valid = (c) => [c.time, c.open, c.high, c.low, c.close].every(Number.isFinite) && c.time % 60 === 0
  && c.low > 0 && c.low <= Math.min(c.open, c.close) && c.high >= Math.max(c.open, c.close)
async function savePage(candles, prefix) {
  if (!candles.length) return
  const file = `pages/${prefix}-${manifest.pages.length}.json`
  await writeFile(resolve(directory, file), JSON.stringify(candles))
  manifest.pages.push({ file, first: candles[0].time, last: candles.at(-1).time, bars: candles.length })
  manifest.bars += candles.length
  manifest.pages.sort((a, b) => b.first - a.first)
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2))
}
try {
  manifest.gapAudit = []
  for (const gap of gaps) {
    if (gap.end - gap.start >= 60000) throw new Error('Large gap requires paginated recovery')
    const response = await api.request({ ticks_history: 'R_100', style: 'candles', granularity: 60, count: 1000, start: gap.start, end: String(gap.end) })
    const recovered = convert(response).filter((c) => valid(c) && c.time >= gap.start && c.time <= gap.end)
    manifest.gapAudit.push({ ...gap, recovered: recovered.length })
    await savePage(recovered, 'gap')
    console.log(JSON.stringify({ gap, recovered: recovered.length }))
  }
  // Capture candles completed while the long backward download was running.
  const { time } = await api.request({ time: 1 })
  const newEnd = Math.floor(time / 60) * 60
  const previousEnd = manifest.endExclusive
  let end = newEnd - 1
  while (end >= previousEnd) {
    const response = await api.request({ ticks_history: 'R_100', style: 'candles', granularity: 60, count: 1000, end: String(end) })
    const page = convert(response)
    if (!page.length || page.some((c) => !valid(c) || c.time > end)) throw new Error('Invalid latest history page')
    const additions = page.filter((c) => c.time >= previousEnd && c.time + 60 <= newEnd)
    await savePage(additions, 'latest')
    if (page[0].time > end) throw new Error('Latest pagination did not advance')
    end = page[0].time - 1
  }
  manifest.originalAsOfEpoch ??= manifest.asOfEpoch
  manifest.asOfEpoch = time
  manifest.endExclusive = newEnd
  manifest.auditComplete = true
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2))
  console.log(JSON.stringify({ bars: manifest.bars, start: manifest.pages.at(-1).first, endExclusive: newEnd, gapAudit: manifest.gapAudit }))
} finally { api.close() }
