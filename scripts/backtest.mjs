import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import WebSocket from 'ws'
import { createServer } from 'vite'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
function option(name, fallback) {
  const index = args.indexOf(name)
  if (index < 0) return fallback
  if (!args[index + 1] || args[index + 1].startsWith('--')) throw new Error(`Missing value for ${name}`)
  return args[index + 1]
}
const days = Number(option('--days', '30'))
const netWinReturn = Number(option('--net-win-return', '0.8'))
if (!Number.isFinite(days) || days < 0.2 || days > 365) throw new Error('--days must be between 0.2 and 365')
const input = option('--input', null)
const outDir = resolve(root, 'research-output')
const output = resolve(option('--output', resolve(outDir, 'backtest-report.json')))

function request(payload) {
  return new Promise((resolveRequest, reject) => {
    const ws = new WebSocket('wss://api.derivws.com/trading/v1/options/ws/public')
    let completed = false
    const finish = (error, value) => {
      if (completed) return
      completed = true
      clearTimeout(timer)
      ws.close()
      if (error) reject(error)
      else resolveRequest(value)
    }
    const timer = setTimeout(() => finish(new Error('Deriv data request timed out')), 15000)
    ws.on('open', () => ws.send(JSON.stringify({ ...payload, req_id: 1 })))
    ws.on('message', (raw) => {
      try {
        const msg = JSON.parse(String(raw))
        if (msg.req_id !== 1) return
        finish(msg.error ? new Error(msg.error.message || 'Deriv request failed') : null, msg)
      } catch (error) { finish(error) }
    })
    ws.on('error', (error) => finish(error))
    ws.on('close', () => { if (!completed) finish(new Error('Connection closed before response')) })
  })
}

let candles
if (input) {
  const dataset = JSON.parse(await readFile(resolve(input), 'utf8'))
  if (dataset.symbol !== 'R_100' || dataset.granularity !== 60 || !Number.isFinite(dataset.asOfEpoch)) {
    throw new Error('Dataset requires symbol R_100, granularity 60, asOfEpoch and candles')
  }
  candles = dataset.candles.map((c) => ({ ...c, isClosed: c.time + 60 <= dataset.asOfEpoch }))
} else {
  const response = await request({ time: 1 })
  const asOfEpoch = Number(response.time)
  if (!Number.isFinite(asOfEpoch)) throw new Error('No valid server time')
  const lastBoundary = Math.floor(asOfEpoch / 60) * 60
  const cutoff = lastBoundary - Math.ceil(days * 1440) * 60 - 60 * 60
  let end = lastBoundary - 1
  const byTime = new Map()
  while (end >= cutoff) {
    const msg = await request({ ticks_history: 'R_100', style: 'candles', granularity: 60, count: 5000, end: String(end) })
    if (!Array.isArray(msg.candles) || !msg.candles.length) throw new Error('History ended before requested period')
    const page = msg.candles.map((c) => ({ time: Number(c.epoch), open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close), isClosed: true }))
    for (const c of page) if (c.time >= cutoff && c.time + 60 <= lastBoundary) byTime.set(c.time, c)
    const earliest = Math.min(...page.map((c) => c.time))
    if (!Number.isFinite(earliest) || earliest > end) throw new Error('History pagination did not advance')
    end = earliest - 1
    console.log(`Downloaded ${byTime.size} completed candles`)
  }
  candles = [...byTime.values()].sort((a, b) => a.time - b.time)
  await mkdir(outDir, { recursive: true })
  await writeFile(resolve(outDir, 'candles.json'), JSON.stringify({ symbol: 'R_100', granularity: 60, asOfEpoch, candles }))
}

// Load the actual TypeScript strategy through the project's installed Vite runtime.
const server = await createServer({ root, configFile: false, logLevel: 'error', server: { middlewareMode: true, hmr: false }, appType: 'custom' })
try {
  const { runBacktest } = await server.ssrLoadModule('/src/lib/research/backtest.ts')
  const report = runBacktest(candles, netWinReturn)
  await mkdir(dirname(output), { recursive: true })
  await writeFile(output, JSON.stringify(report, null, 2))
  console.log(report.measurement)
  console.log(`${report.startUTC} to ${report.endUTC}; ${report.bars} bars; ${report.gaps} gaps`)
  console.table(report.rows.filter((row) => row.window.includes('70%') || row.window.includes('30%')).map((r) => ({
    window: r.window, variant: r.variant, n: r.n,
    winPercent: r.winRate == null ? null : +(r.winRate * 100).toFixed(2),
    netUnits: +r.netUnits.toFixed(2), maxLossStreak: r.maxLosingStreak,
  })))
  console.log(report.warning)
  console.log(`Saved ${output}`)
} finally { await server.close() }
