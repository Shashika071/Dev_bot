/**
 * Compare baseline vs optional filter presets on cached max history (±0.03 / 7m).
 * Usage: node scripts/compare-filters.mjs [--minutes 7] [--threshold 0.03]
 */
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { createServer } from 'vite'

const root = process.cwd()
const args = process.argv.slice(2)
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback)
const threshold = Number(option('--threshold', '0.03'))
const minutes = Number(option('--minutes', '7'))
const directory = resolve(option('--output-dir', `research-output/filter-compare-${minutes}m`))
await mkdir(directory, { recursive: true })

const hash = createHash('sha256')
const base = resolve('research-output/touch-003')
const manifest = JSON.parse(await readFile(resolve(base, 'download-manifest.json'), 'utf8'))
if (!manifest.complete) throw new Error('Maximum-history download has not finished; resume it first')

const candles = []
for (const page of [...manifest.pages].reverse()) {
  const raw = await readFile(resolve(base, page.file), 'utf8')
  hash.update(raw)
  const batch = JSON.parse(raw)
  if (batch.length !== page.bars || batch[0]?.time !== page.first || batch.at(-1)?.time !== page.last) {
    throw new Error('Page does not match manifest')
  }
  for (const candle of batch) candles.push({ ...candle, isClosed: candle.time + 60 <= manifest.endExclusive })
}
if (candles.length !== manifest.bars) throw new Error('Manifest total mismatch')
candles.sort((a, b) => a.time - b.time)
const sha256 = hash.digest('hex')

const server = await createServer({
  root,
  configFile: false,
  logLevel: 'error',
  server: { middlewareMode: true, hmr: false },
  appType: 'custom',
})

try {
  const { runTouchBacktest } = await server.ssrLoadModule('/src/lib/research/touchBacktest.ts')
  const { FILTER_PRESETS } = await server.ssrLoadModule('/src/lib/strategy/filters.ts')

  const closed = candles.filter((c) => c.isClosed)
  const spanDays = (closed.at(-1).time + 60 - closed[0].time) / 86400
  const pct = (n) => (n == null ? '—' : `${(n * 100).toFixed(2)}%`)
  const edgePp = (touch, random) =>
    touch == null || random == null ? null : (touch - random) * 100

  const results = []
  for (const [name, preset] of Object.entries(FILTER_PRESETS)) {
    const t0 = Date.now()
    console.error(`Running preset: ${name}…`)
    const { report } = runTouchBacktest(candles, threshold, minutes, preset)
    const o = report.overall
    const days = spanDays
    results.push({
      name,
      filterConfig: report.filterConfig,
      qualified: report.qualified,
      evaluated: o.n,
      missingOutcomes: report.missingOutcomes,
      signalsPerDay: days > 0 ? report.qualified / days : null,
      risePerDay: days > 0 ? report.rise.n / days : null,
      fallPerDay: days > 0 ? report.fall.n / days : null,
      touchRate: o.touchRate,
      randomDirectionTouchRate: o.matchedRandomDirectionTouchRate,
      edgeVsRandomPp: edgePp(o.touchRate, o.matchedRandomDirectionTouchRate),
      closeHitRate: o.closeHitRate,
      bothTouched: o.bothTouched,
      missed: o.missed,
      rise: { n: report.rise.n, touchRate: report.rise.touchRate, closeHitRate: report.rise.closeHitRate },
      fall: { n: report.fall.n, touchRate: report.fall.touchRate, closeHitRate: report.fall.closeHitRate },
      chronological: report.chronological.map((c) => ({
        name: c.name,
        n: c.n,
        touchRate: c.touchRate,
        randomDirectionTouchRate: c.matchedRandomDirectionTouchRate,
        edgeVsRandomPp: edgePp(c.touchRate, c.matchedRandomDirectionTouchRate),
        closeHitRate: c.closeHitRate,
      })),
      elapsedMs: Date.now() - t0,
    })
  }

  const baseline = results.find((r) => r.name === 'baseline')
  const comparison = {
    symbol: 'R_100',
    threshold,
    horizonMinutes: minutes,
    startUTC: new Date(closed[0].time * 1000).toISOString(),
    endExclusiveUTC: new Date((closed.at(-1).time + 60) * 1000).toISOString(),
    bars: closed.length,
    spanDays,
    sha256,
    note: 'Touch rates are candle-excursion proxies, not contract settlement. Keep a filter only if edge vs matched random-direction holds on the latest 30% segment and later forward/demo data.',
    results,
  }

  await writeFile(resolve(directory, 'filter-compare.json'), JSON.stringify(comparison, null, 2))

  const row = (r) =>
    `| ${r.name} | ${r.qualified} | ${r.signalsPerDay?.toFixed(2) ?? '—'} | ${pct(r.touchRate)} | ${pct(r.randomDirectionTouchRate)} | ${r.edgeVsRandomPp == null ? '—' : r.edgeVsRandomPp.toFixed(2)} | ${pct(r.closeHitRate)} | ${r.missed} |`

  const holdoutRow = (r) => {
    const late = r.chronological.find((c) => c.name.startsWith('Latest'))
    if (!late) return `| ${r.name} | — | — | — | — |`
    return `| ${r.name} | ${late.n} | ${pct(late.touchRate)} | ${pct(late.randomDirectionTouchRate)} | ${late.edgeVsRandomPp == null ? '—' : late.edgeVsRandomPp.toFixed(2)} | ${pct(late.closeHitRate)} |`
  }

  const markdown = [
    `# Filter comparison · ±${threshold} · ${minutes}m horizon`,
    '',
    `**Coverage:** ${comparison.startUTC} → ${comparison.endExclusiveUTC} · ${comparison.bars.toLocaleString('en-US')} M1 bars · ~${spanDays.toFixed(1)} days.`,
    '',
    'Baseline = five-rule engine only. Other rows add optional post-qualification filters (ATR vs 0.03, skip large candle, M5 SMA20 trend). Defaults remain OFF in live trading unless toggled.',
    '',
    '## Full sample',
    '',
    '| Preset | Qualified | /day | Touch | Random-dir | Edge pp | Close dir | Missed |',
    '|---|---:|---:|---:|---:|---:|---:|---:|',
    ...results.map(row),
    '',
    `Baseline reference: ${baseline?.qualified ?? '—'} signals (~${baseline?.signalsPerDay?.toFixed(2) ?? '—'}/day).`,
    '',
    '## Latest 30% of history (descriptive — not a fresh holdout)',
    '',
    '| Preset | N | Touch | Random-dir | Edge pp | Close dir |',
    '|---|---:|---:|---:|---:|---:|',
    ...results.map(holdoutRow),
    '',
    '## How to read this',
    '',
    '- **Edge pp** = (signal touch rate − matched random-direction touch rate) × 100. Positive means the bot’s chosen side touched more often than a coin-flip at the same times.',
    '- High absolute touch rates near 95% usually mean both barriers are reachable; they do **not** prove directional skill.',
    '- Prefer presets that keep a positive edge on the latest segment **and** do not crush signal count below what you can forward-test.',
    '- No stake/payout/barrier quote model is applied — still not a tradable P&L claim.',
    '',
    `Data SHA256: \`${sha256}\`.`,
    '',
    '```powershell',
    `node scripts/compare-filters.mjs --minutes ${minutes} --threshold ${threshold}`,
    '```',
    '',
  ].join('\n')

  await writeFile(resolve(directory, 'FILTER_COMPARE.md'), markdown)
  console.log(JSON.stringify({
    startUTC: comparison.startUTC,
    endExclusiveUTC: comparison.endExclusiveUTC,
    bars: comparison.bars,
    spanDays,
    results: results.map((r) => ({
      name: r.name,
      qualified: r.qualified,
      signalsPerDay: r.signalsPerDay,
      touchRate: r.touchRate,
      randomDirectionTouchRate: r.randomDirectionTouchRate,
      edgeVsRandomPp: r.edgeVsRandomPp,
      closeHitRate: r.closeHitRate,
      elapsedMs: r.elapsedMs,
    })),
  }, null, 2))
  console.log(`Saved ${resolve(directory, 'FILTER_COMPARE.md')}`)
} finally {
  await server.close()
}
