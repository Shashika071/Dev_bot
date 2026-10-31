import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { createServer } from 'vite'

const root = process.cwd()
const args = process.argv.slice(2)
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback
const input = option('--input', null)
const threshold = Number(option('--threshold', '0.03'))
const minutes = Number(option('--minutes', '1'))
const directory = resolve(option('--output-dir', minutes === 1 ? 'research-output/touch-003' : `research-output/touch-003-${minutes}m`))
await mkdir(directory, { recursive: true })
let candles = [], source
const hash = createHash('sha256')
if (input) {
  const raw = await readFile(resolve(input), 'utf8')
  hash.update(raw)
  const data = JSON.parse(raw)
  if (data.symbol !== 'R_100' || data.granularity !== 60 || !Number.isFinite(data.asOfEpoch)) throw new Error('Invalid source metadata')
  candles = data.candles.map((c) => ({ ...c, isClosed: c.time + 60 <= data.asOfEpoch }))
  source = { input: resolve(input), asOfEpoch: data.asOfEpoch }
} else {
  const base = resolve('research-output/touch-003')
  const manifest = JSON.parse(await readFile(resolve(base, 'download-manifest.json'), 'utf8'))
  if (!manifest.complete) throw new Error('Maximum-history download has not finished; resume it first')
  for (const page of [...manifest.pages].reverse()) {
    const raw = await readFile(resolve(base, page.file), 'utf8')
    hash.update(raw)
    const batch = JSON.parse(raw)
    if (batch.length !== page.bars || batch[0]?.time !== page.first || batch.at(-1)?.time !== page.last) throw new Error('Page does not match manifest')
    for (const candle of batch) candles.push({ ...candle, isClosed: candle.time + 60 <= manifest.endExclusive })
  }
  if (candles.length !== manifest.bars) throw new Error('Manifest total mismatch')
  candles.sort((a, b) => a.time - b.time)
  source = { manifest: resolve(base, 'download-manifest.json'), asOfEpoch: manifest.asOfEpoch, availabilityStop: manifest.stop }
}
const server = await createServer({ root, configFile: false, logLevel: 'error', server: { middlewareMode: true, hmr: false }, appType: 'custom' })
try {
  const { runTouchBacktest } = await server.ssrLoadModule('/src/lib/research/touchBacktest.ts')
  const { report, rows } = runTouchBacktest(candles, threshold, minutes)
  report.source = { ...source, sha256: hash.digest('hex') }
  const ranges = rows.map((r) => r.nextHigh - r.nextLow).sort((a, b) => a - b)
  const mid = Math.floor(ranges.length / 2)
  report.medianOutcomeCandleRange = ranges.length ? (ranges.length % 2 ? ranges[mid] : (ranges[mid - 1] + ranges[mid]) / 2) : null
  await writeFile(resolve(directory, 'touch-report.json'), JSON.stringify(report, null, 2))
  const columns = ['signalTime', 'outcomeTime', 'outcomeEndTime', 'horizonMinutes', 'firstTouchMinute', 'side', 'signalClose', 'target', 'oppositeTarget', 'nextOpen', 'nextHigh', 'nextLow', 'nextClose', 'touched', 'oppositeTouched', 'bothTouched', 'touchedFromNextOpen', 'closeDirectionHit']
  const csv = ['signalUTC,outcomeUTC,' + columns.join(','), ...rows.map((row) =>
    [new Date(row.signalTime * 1000).toISOString(), new Date(row.outcomeTime * 1000).toISOString(), ...columns.map((key) => row[key])].join(','))].join('\n')
  await writeFile(resolve(directory, 'signal-touches.csv'), csv)
  const pct = (n) => n == null ? '—' : `${(n * 100).toFixed(2)}%`
  const tableRow = (name, stats) => `| ${name} | ${stats.n} | ${stats.touched} | ${stats.missed} | ${pct(stats.touchRate)} | ${stats.bothTouched} | ${pct(stats.closeHitRate)} |`
  const o = report.overall
  const markdown = [
    `# R_100: directional ±${threshold} touch backtest over ${minutes} minute(s)`, '',
    `**Coverage:** ${report.startUTC} to ${report.endExclusiveUTC} (UTC, end exclusive); ${report.bars.toLocaleString('en-US')} completed M1 candles; ${report.gaps} gaps.`, '',
    `**Question tested:** after a completed-candle RISE signal, does price reach signal close + ${threshold} at any time in the next ${minutes} completed minutes? After FALL, does it reach signal close − ${threshold}? The threshold is an absolute price change, not a percentage. End direction is measured at the close of minute ${minutes}.`, '',
    'Only the current five-rule strategy is used: fresh UT(2,10) trigger, matching ST(7,2), SMA20 position/slope, AO(5,34) sign/slope, RSI14 in (52,70) for RISE or (30,48) for FALL. No settings were tuned.', '',
    '| Signals | Evaluated | Touched | Missed | Touch rate | Both directions touched | Close direction correct |',
    '|---|---:|---:|---:|---:|---:|---:|',
    tableRow('All', o), tableRow('RISE', report.rise), tableRow('FALL', report.fall), '',
    `Qualified signals: ${report.qualified}; excluded without a consecutive completed outcome: ${report.missingOutcomes}.`,
    `Descriptive 95% Wilson touch-rate interval: ${o.interval95?.map(pct).join(' to ') ?? '—'}; longest touch-miss streak: ${o.longestMissStreak}. Serial correlation can make that interval too narrow.`, '',
    '## What a high touch rate does and does not mean', '',
    `- Signal-direction touch only: ${o.onlySignalDirection}. Opposite-direction touch only: ${o.onlyOppositeDirection}. Both: ${o.bothTouched}. Neither: ${o.neither}.`,
    `- The opposite direction reached the same ${threshold} distance in ${o.oppositeTouched}/${o.n} cases (${pct(o.n ? o.oppositeTouched / o.n : null)}).`,
    `- A 50/50 random direction at these exact signal times has an expected touch rate of ${pct(o.matchedRandomDirectionTouchRate)}. This is the average of up-touch and down-touch rates, not a simulated trading profit.`,
    `- Across all ${report.allCandleRandomDirectionBaseline.bars} eligible ${minutes}-minute outcome windows, the analogous random-direction touch baseline is ${pct(report.allCandleRandomDirectionBaseline.touchRate)}.`,
    `- Using the next candle OPEN as reference instead of the signal CLOSE: ${o.nextOpenTouched}/${o.n} (${pct(o.nextOpenTouchRate)}) touch the requested distance in the chosen direction.`,
    `- Median ${minutes}-minute high–low range at signals: ${report.medianOutcomeCandleRange?.toFixed(4) ?? '—'} price points.`, '',
    '**These are price-excursion measurements, not verified contract wins.** A candle may hit both barriers; OHLC data cannot generally tell which was first. Signal-close entry is not executable after notification. Next-open sensitivity still omits notification/order latency. No stake, payout, spread or execution model is applied. This does not establish a profitable Touch or Rise/Fall strategy.', '',
    '## When the target was first reached', '',
    '| Minute | First touches in this minute | Cumulative touches | Cumulative rate |',
    '|---|---:|---:|---:|',
    ...report.touchTiming.map((t) => `| ${t.minute} | ${t.firstTouches} | ${t.cumulativeTouches} | ${pct(o.n ? t.cumulativeTouches / o.n : null)} |`), '',
    'Only fully observed, gap-free windows are counted. First-touch timing has one-minute resolution; it does not establish order against the opposite barrier inside the same candle. Overlapping windows are not independent trades.', '',
    '## Monthly results (UTC; grouped by first outcome minute; first and last months may be partial)', '',
    '| Month | Evaluated | Touched | Missed | Touch rate | Both directions touched | Close direction correct |',
    '|---|---:|---:|---:|---:|---:|---:|',
    ...report.monthly.map((m) => tableRow(m.monthUTC, m)), '',
    '## Chronological comparison', '',
    '| Period | Evaluated | Touched | Missed | Touch rate | Both directions touched | Close direction correct |',
    '|---|---:|---:|---:|---:|---:|---:|',
    ...report.chronological.map((m) => tableRow(m.name, m)), '',
    'These are descriptive segments, not newly untouched holdouts; some recent history was inspected in earlier research.', '',
    '## Data and reproducibility', '',
    source.availabilityStop ? `Download stop: ${source.availabilityStop.reason}. Last attempted older end: ${new Date(source.availabilityStop.requestedEnd * 1000).toISOString()}. Newer fallback candles were rejected. This is the maximum continuous history obtained from this public endpoint in this run; it is not a claim that no other source has older data.` : 'This run uses the supplied local dataset.', '',
    `Data SHA256: \`${report.source.sha256}\`.`,
    'Signals are generated chronologically by the production indicator and evaluation functions with 60-bar warmup after each gap. Long-history seeding may differ slightly from a dashboard restart with a shorter history window.', '',
    'Every evaluated signal and its prices/targets is in `signal-touches.csv`; machine-readable totals are in `touch-report.json`. CSV nextOpen is the first outcome open; nextHigh/nextLow are the full window extremes; nextClose is the final outcome close.', '',
    '```powershell', 'node scripts/download-max-history.mjs', `node scripts/touch-backtest.mjs --minutes ${minutes}`, '```', '',
  ].join('\n')
  await writeFile(resolve(directory, 'TOUCH_REPORT.md'), markdown)
  console.log(JSON.stringify({ startUTC: report.startUTC, endExclusiveUTC: report.endExclusiveUTC, bars: report.bars, gaps: report.gaps, overall: o, rise: report.rise, fall: report.fall }, null, 2))
  console.log(`Saved ${resolve(directory, 'TOUCH_REPORT.md')}`)
} finally { await server.close() }
