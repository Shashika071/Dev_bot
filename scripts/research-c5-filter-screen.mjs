// Offline research only. Does not authenticate, request quotes, or place orders.
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { createHash } from 'node:crypto'
import { createServer } from 'vite'
import { computeFilterSeries, FILTER_DEFINITIONS, filterPass } from './lib/c5-filter-indicators.mjs'
import { auditCandles, chartContext, relativeContext, observeOpportunity, summarize, dayBlockIntervals } from './lib/c5-filter-screen.mjs'

const args = process.argv.slice(2)
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback
const directory = resolve(option('--output-dir', 'research-output/c5-filter-screen-2026-11-11'))
const manifestPath = resolve(option('--manifest', 'research-output/touch-003/download-manifest.json'))
const input = option('--input', null)
await mkdir(directory, { recursive: true })
const protocol = {
  symbol: 'R_100', timeframeSeconds: 60, barrier: .01, minutes: 9,
  entry: 'First following M1 candle open; no latency. Candle excursion proxy only.',
  filters: FILTER_DEFINITIONS,
  context: 'Current close/wick versus previous 20 completed-bar high/low channel, excluding current bar. CHOP buckets <38.2, >61.8, otherwise middle.',
  comparison: 'Each filter separately on identical C5 opportunities with all indicators available; no combinations or threshold tuning.',
  split: 'Earlier 70% versus later 30% of candles, descriptive, not fresh unseen holdout.',
  limitations: 'Closed-candle C5 only. Current forming-bar live signals are not reconstructible. No quotes, ticks, payouts, execution delay or cooldown model. Opportunities may overlap.',
}
await writeFile(resolve(directory, 'protocol.json'), JSON.stringify(protocol, null, 2))
const hash = createHash('sha256')
let candles = [], source
if (input) {
  const raw = await readFile(resolve(input), 'utf8'), data = JSON.parse(raw)
  hash.update(raw)
  if (data.symbol !== 'R_100' || data.granularity !== 60 || !Number.isFinite(data.asOfEpoch)) throw new Error('Invalid input metadata')
  candles = data.candles.map(c => ({ ...c, isClosed: c.time + 60 <= data.asOfEpoch }))
  source = { input: resolve(input), asOfEpoch: data.asOfEpoch }
} else {
  const manifestRaw = await readFile(manifestPath, 'utf8'), manifest = JSON.parse(manifestRaw)
  if (!manifest.complete || manifest.symbol !== 'R_100' || manifest.granularity !== 60) throw new Error('Invalid manifest')
  hash.update(manifestRaw)
  for (const page of [...manifest.pages].reverse()) {
    const raw = await readFile(resolve(dirname(manifestPath), page.file), 'utf8'), batch = JSON.parse(raw)
    if (batch.length !== page.bars || batch[0]?.time !== page.first || batch.at(-1)?.time !== page.last) throw new Error('Manifest page mismatch')
    hash.update(page.file); hash.update(raw)
    for (const c of batch) candles.push({ ...c, isClosed: c.time + 60 <= manifest.endExclusive })
  }
  if (candles.length !== manifest.bars) throw new Error('Manifest total mismatch')
  candles.sort((a, b) => a.time - b.time)
  source = { manifest: manifestPath, asOfEpoch: manifest.asOfEpoch }
}
source.sha256 = hash.digest('hex')
const audit = auditCandles(candles)
if (!candles.length) throw new Error('No candles')
const splitTime = candles[Math.floor(candles.length * .7)].time
console.log(JSON.stringify({ stage: 'audited', ...audit, start: candles[0].time, end: candles.at(-1).time }))
const series = computeFilterSeries(candles)
const byTime = new Map(candles.map((c, i) => [c.time, i]))
const server = await createServer({ root: process.cwd(), configFile: false, logLevel: 'error', server: { middlewareMode: true, hmr: false }, appType: 'custom' })
const rows = []
let qualified = 0, missingOutcomes = 0, unavailableIndicators = 0
try {
  const { evaluateC5All, C5_CALC_WINDOW } = await server.ssrLoadModule('/src/lib/strategy/c5Strategy.ts')
  // Chunking preserves the exact production trailing-260-bar calculation.
  for (let start = 0; start < candles.length; start += 10000) {
    const end = Math.min(candles.length, start + 10000)
    const window = candles.slice(Math.max(0, start - C5_CALC_WINDOW + 1), end)
    for (const ev of evaluateC5All(window)) {
      if (ev.time < candles[start].time || !['RISE', 'FALL'].includes(ev.final)) continue
      qualified++
      const i = byTime.get(ev.time)
      const observed = observeOpportunity(candles, i, ev.final)
      if (!observed) { missingOutcomes++; continue }
      if (![series.chop14[i], series.macdHist[i], series.er20[i]].every(v => v != null && Number.isFinite(v))) { unavailableIndicators++; continue }
      const context = chartContext(candles, i)
      if (!context) throw new Error('Missing context after common warmup')
      rows.push({
        index: i, ...observed, chop14: series.chop14[i], macdHist: series.macdHist[i], er20: series.er20[i],
        context, relativeContext: relativeContext(context, ev.final),
        chopBucket: series.chop14[i] < 38.2 ? 'TRENDING_RECENTLY' : series.chop14[i] > 61.8 ? 'CHOPPY_RECENTLY' : 'MIDDLE',
        erAlignment: context.change20 === 0 ? 'FLAT' : (context.change20 > 0) === (ev.final === 'RISE') ? 'ALIGNED' : 'OPPOSED',
        passes: FILTER_DEFINITIONS.filter(d => filterPass(d.id, ev.final, i, series)).map(d => d.id),
      })
    }
    console.log(JSON.stringify({ stage: 'C5 replay', processed: end, total: candles.length, qualified, included: rows.length }))
    // Allow Vite's pending work and process IO between batches.
    await new Promise(resolve => setImmediate(resolve))
  }
} finally { await server.close() }

const baseline = summarize(rows)
const months = [...new Set(rows.map(r => new Date(r.confirmedTime * 1000).toISOString().slice(0, 7)))]
const variants = FILTER_DEFINITIONS.map(d => {
  const kept = rows.filter(r => r.passes.includes(d.id)), rejected = rows.filter(r => !r.passes.includes(d.id))
  const stats = summarize(kept)
  return { ...d, ...stats, keptFraction: rows.length ? kept.length / rows.length : null,
    deltaVsC5: stats.touchRate == null || baseline.touchRate == null ? null : stats.touchRate - baseline.touchRate,
    rejected: summarize(rejected), rise: summarize(kept.filter(r => r.side === 'RISE')), fall: summarize(kept.filter(r => r.side === 'FALL')),
    chronological: [
      { period: 'Earlier 70%', ...summarize(kept.filter(r => r.outcomeEndTime <= splitTime)) },
      { period: 'Later 30%', ...summarize(kept.filter(r => r.signalTime >= splitTime)) },
    ],
    monthly: months.map(month => ({ month, ...summarize(kept.filter(r => new Date(r.confirmedTime * 1000).toISOString().startsWith(month))) })),
    exploratoryDayBootstrap95: dayBlockIntervals(rows, d.id),
  }
})
const grouped = key => [...new Set(rows.map(r => r[key]))].sort().map(group => {
  const selected = rows.filter(r => r[key] === group)
  return { group, ...summarize(selected), earlier: summarize(selected.filter(r => r.outcomeEndTime <= splitTime)), later: summarize(selected.filter(r => r.signalTime >= splitTime)) }
})
for (const r of rows) r.rawContext = r.context.label
const report = {
  protocol, source, audit, startUTC: new Date(candles[0].time * 1000).toISOString(),
  endExclusiveUTC: new Date((candles.at(-1).time + 60) * 1000).toISOString(),
  splitUTC: new Date(splitTime * 1000).toISOString(), qualified, missingOutcomes, unavailableIndicators,
  variants, contextGroups: grouped('relativeContext'), rawContextGroups: grouped('rawContext'), chopGroups: grouped('chopBucket'), erDirectionGroups: grouped('erAlignment'),
}
await writeFile(resolve(directory, 'report.json'), JSON.stringify(report, null, 2))
await writeFile(resolve(directory, 'opportunities.json'), JSON.stringify(rows))
// First occurrence of each chart context in the later period, selected without consulting outcomes.
const selected = new Map()
for (const r of rows) if (r.signalTime >= splitTime && !selected.has(r.rawContext)) selected.set(r.rawContext, r)
const illustrativeMiss = rows.find(r => r.signalTime >= splitTime && !r.hit)
const exampleRows = [...selected.values(), ...(illustrativeMiss ? [illustrativeMiss] : [])]
const examples = exampleRows.map(r => ({
  ...r, selection: r === illustrativeMiss
    ? 'First miss in the later 30%, deliberately outcome-selected to illustrate a failure. Not a representative performance sample.'
    : 'First occurrence of this context in the later 30%; outcome not used for selection.',
  candles: candles.slice(Math.max(0, r.index - 59), r.index + 10).map((c, offset) => {
    const i = Math.max(0, r.index - 59) + offset
    return { ...c, chop14: series.chop14[i], ema50: series.ema50[i] }
  }),
}))
await writeFile(resolve(directory, 'chart-examples.json'), JSON.stringify(examples))

const pct = n => n == null ? 'n/a' : `${(100 * n).toFixed(2)}%`
const pp = n => n == null ? 'n/a' : `${n >= 0 ? '+' : ''}${(100 * n).toFixed(2)} pp`
const ci = bounds => bounds ? bounds.map(pp).join(' to ') : 'n/a'
const chop = variants.find(v => v.id === 'CHOP14_LT_38_2')
const breakout = report.contextGroups.find(g => g.group === 'ALIGNED_BREAKOUT')
const table = variants.map(v => `| ${v.id} | ${v.n} | ${v.hits} | ${v.misses} | ${pct(v.touchRate)} | ${pct(v.randomSideRate)} | ${pp(v.deltaVsC5)} | ${v.rejected.hits} / ${v.rejected.misses} |`)
const groupTable = groups => [
  '| Context at C5 close | N | Touches | Misses | Touch rate | Same-time random side | Earlier rate | Later rate |',
  '|---|---:|---:|---:|---:|---:|---:|---:|',
  ...groups.map(g => `| ${g.group} | ${g.n} | ${g.hits} | ${g.misses} | ${pct(g.touchRate)} | ${pct(g.randomSideRate)} | ${pct(g.earlier.touchRate)} | ${pct(g.later.touchRate)} |`), '',
]
const markdown = [
  '# C5 filters and chart context — R_100 historical study', '',
  `Coverage: ${report.startUTC} through ${report.endExclusiveUTC} (end exclusive). ${audit.bars.toLocaleString()} completed one-minute candles; ${audit.gaps} gap(s), ${audit.missingMinutes} missing minute(s).`, '',
  '**These are candle-excursion counts, not verified contract wins or profits. No tested filter guarantees a win.**', '',
  '## Findings', '',
  `CHOP retains ${pct(chop.keptFraction)} of C5 opportunities, with ${chop.hits} touches and ${chop.misses} misses. Its exploratory interval for change versus C5 is ${ci(chop.exploratoryDayBootstrap95.deltaVsC5)}. In the later period it has ${chop.chronological[1].hits}/${chop.chronological[1].n} touches; the opposite direction touches in ${chop.chronological[1].oppositeHits}/${chop.chronological[1].n}. A perfect small subsample is not evidence of guaranteed prediction.`, '',
  `C5 reaches the selected nearby barrier ${pct(baseline.touchRate)} of the time, while a random side at the same times averages ${pct(baseline.randomSideRate)}. Both barriers are reached in ${baseline.bothHits.toLocaleString()} of ${baseline.n.toLocaleString()} windows (${pct(baseline.n ? baseline.bothHits / baseline.n : null)}). Close breakouts aligned with C5 yield ${pct(breakout?.touchRate)}. Candle-only results do not establish a live profitable filter.`, '',
  'For chart reading, distinguish a close outside the previous 20-bar range from a wick outside followed by a close back inside. CHOP describes recent trendiness; it does not predict direction. These descriptions can organize a demo journal, but a new untouched sample with actual quotes and settled P/L is still needed.', '',
  '## Frozen method', '',
  'Production closed-candle C5: fresh UT(2,10), Supertrend(7,2), SMA20 position/slope, RSI14 in (52,70) for RISE or (30,48) for FALL. AO is not required. Production trailing 260-bar calculations are reused. The current bot can also act on a forming candle; that path is not reproduced here.', '',
  'Entry reference is the next M1 open. A touch is a high at least entry+0.01 for RISE or a low at most entry-0.01 for FALL in exactly the next nine complete M1 candles. This is an idealized, zero-delay proxy. Features use only information available at the C5 candle close. Gaps reset indicators and exclude crossing outcome windows.', '',
  `Qualified C5 signals ${qualified}; missing complete outcomes ${missingOutcomes}; excluded for common indicator warmup ${unavailableIndicators}; compared opportunities ${rows.length}. Overlapping opportunities remain in the sample; this is not a one-position-at-a-time trading simulation.`, '',
  'Candidates were fixed before outcomes: CHOP14<38.2; MACD(12,26,9 EMA/EMA) histogram positive for RISE or negative for FALL; Kaufman ER20>=0.30. ER20/0.30 is exploratory, not a validated best setting. No combinations, search over settings, or optimization.', '',
  '## Filter comparison', '',
  '| Filter | Kept | Touches | Misses | Proxy touch rate | Same-time random side | Change vs C5 | Rejected touches / misses |',
  '|---|---:|---:|---:|---:|---:|---:|---:|', ...table, '',
  'Random side means the exact mean of up-touch and down-touch rates at these same accepted times. It is not assumed to be 50%. Both nearby barriers may be touched. A high hit rate by itself does not establish directional skill.', '',
  '## Chronological check and uncertainty', '',
  `Split: ${report.splitUTC}. The archive has been examined previously, so neither segment is a fresh holdout. Boundary-crossing outcomes are excluded from split summaries.`, '',
  '| Filter | Earlier N / rate | Later N / rate | 95% exploratory interval: change vs C5 | 95% interval: edge over random side |',
  '|---|---:|---:|---|---|',
  ...variants.map(v => `| ${v.id} | ${v.chronological[0].n} / ${pct(v.chronological[0].touchRate)} | ${v.chronological[1].n} / ${pct(v.chronological[1].touchRate)} | ${ci(v.exploratoryDayBootstrap95.deltaVsC5)} | ${ci(v.exploratoryDayBootstrap95.edgeOverRandomSide)} |`), '',
  'Intervals resample 2000 whole UTC-day blocks with a fixed seed, retaining intraday overlapping opportunities together. They still assume independent days, do not remove cross-midnight dependence, are not corrected for multiple comparisons, and do not measure live execution uncertainty. Context subsets are descriptive, not independently validated strategies.', '',
  '## Chart context', '',
  'The channel uses the previous 20 completed bars, excluding the C5 candle. Breakout means closing strictly beyond the channel. Rejection means a wick outside but close back inside. An upper rejection is labeled supportive of FALL; a lower rejection supportive of RISE. These are descriptive labels, not demonstrated advantages. This describes price geometry, not institutional activity or a predictable market regime.', '',
  ...groupTable(report.contextGroups),
  '### Recent trendiness', '', ...groupTable(report.chopGroups),
  '### Full channel labels', '', ...groupTable(report.rawContextGroups),
  '## Monthly stability', '',
  '| UTC month | C5 N / rate | CHOP N / rate | MACD N / rate | ER N / rate |',
  '|---|---:|---:|---:|---:|',
  ...months.map((m, i) => `| ${m} | ${variants.map(v => `${v.monthly[i].n} / ${pct(v.monthly[i].touchRate)}`).join(' | ')} |`), '',
  '## What must be checked before any live use', '',
  '- Fresh forward demo entries with exact confirmed signal time, purchase time, actual barrier, tick path, payout and settled P/L. Keep the selected rule fixed.',
  '- Historical highs/lows cannot reconstruct a manually delayed entry or forming-bar signal, or show whether a relevant tick occurred before contract activation. No claim is made about the specific November 6 loss; it is outside this archive.',
  '- Payouts are missing. The small barrier does not make a contract risk-free. Many small wins can be offset by a single stake loss; judge net P/L as well as hit rate.',
  '- Public historical tick retrieval was not executed because automatic approval review could not complete due a usage limit. Local saved candles were used.', '',
  '## Sources and reproduction', '',
  '- [TradingView CHOP](https://www.tradingview.com/support/solutions/43000501980-choppiness-index-chop/)',
  '- [TradingView MACD](https://www.tradingview.com/support/solutions/43000502344-moving-average-convergence-divergence-macd-indicator/)',
  '- [TradingView Kaufman ER formula](https://www.tradingview.com/support/solutions/43000773012-kaufman-s-adaptive-moving-average-kama/)',
  '- [TradingView Donchian Channels](https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/)',
  '- [Deriv synthetic indices: generated prices and limits of historical patterns](https://deriv.com/markets/derived-indices/synthetic-indices?tab=cfds)',
  '- [Deriv One Touch contract rule](https://legacy-docs.deriv.com/docs/touchno-touch)', '',
  '`node scripts/research-c5-filter-screen.mjs`', '',
  `Input SHA256 (manifest and ordered page contents): ${source.sha256}`, '',
  'The formulas were computed locally from saved Deriv candles. TradingView Strategy Tester was not used. Production trading code was not changed.', '',
]
await writeFile(resolve(directory, 'report.md'), markdown.join('\n'))
console.log(JSON.stringify({ stage: 'complete', directory, variants: variants.map(v => ({ id: v.id, n: v.n, hits: v.hits, misses: v.misses, rate: v.touchRate, randomSideRate: v.randomSideRate, deltaVsC5: v.deltaVsC5 })), contextGroups: report.contextGroups }, null, 2))
