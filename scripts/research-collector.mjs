/**
 * Research collector for the 22-strategy paper experiment.
 * Public market data only. Never authenticates a trading account or sends buy.
 *
 * Usage:
 *   npm run research:collector
 *   RESEARCH_API_HOST=0.0.0.0 npm run research:collector   # server / Docker
 */
import { createServer as createHttpServer } from 'node:http'
import { createServer as createViteServer } from 'vite'
import WebSocket from 'ws'
const PORT = Number(process.env.RESEARCH_API_PORT || 8787)
const HOST = process.env.RESEARCH_API_HOST || '127.0.0.1'
const SYMBOL = process.env.VITE_DERIV_SYMBOL || 'R_100'
const PUBLIC_WS = process.env.VITE_DERIV_PUBLIC_WS || 'wss://api.derivws.com/trading/v1/options/ws/public'
const args = process.argv.slice(2)
const idArg = args.includes('--id') ? args[args.indexOf('--id') + 1] : null

const vite = await createViteServer({
  root: process.cwd(),
  configFile: false,
  logLevel: 'error',
  server: { middlewareMode: true, hmr: false },
  appType: 'custom',
})

const {
  createInitialState,
  startDiscovery,
  resumeCollecting,
  onTick,
  RESEARCH_CODE_VERSION,
} = await vite.ssrLoadModule('/src/lib/research/experimentEngine.ts')
const {
  newExperimentId,
  latestExperimentId,
  loadState,
  appendStateSnapshot,
  appendTick,
  appendEvent,
  listExperiments,
} = await vite.ssrLoadModule('/src/lib/research/experimentStore.ts')
const { summarizeExperiment, exportObservationsCsv, exportReportMarkdown } =
  await vite.ssrLoadModule('/src/lib/research/summarizeExperiment.ts')
const { applyTick, candlesFromHistory, finalizeByClock } =
  await vite.ssrLoadModule('/src/lib/deriv/candles.ts')
const { proposeOneTouchOnly } =
  await vite.ssrLoadModule('/src/lib/research/proposeOnly.ts')
const { attachProposalQuote } =
  await vite.ssrLoadModule('/src/lib/research/touchPaper.ts')
const { STRATEGY_REGISTRY } =
  await vite.ssrLoadModule('/src/lib/research/strategies/registry.ts')

let experimentId = idArg || latestExperimentId() || newExperimentId('discovery')
let state = loadState(experimentId) || createInitialState()
appendStateSnapshot(experimentId, state)

let candles = []
let lastPersist = 0
let feedHealthy = false
let lastReceipt = 0
let downtimeAccum = 0
let unhealthySince = null
let proposalBusy = false

function persist(force = false) {
  const now = Date.now()
  if (!force && now - lastPersist < 2000) return
  lastPersist = now
  appendStateSnapshot(experimentId, state)
}

function json(res, code, body) {
  const raw = JSON.stringify(body)
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  })
  res.end(raw)
}

async function readBody(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  if (!chunks.length) return {}
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) } catch { return {} }
}

const api = createHttpServer(async (req, res) => {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    })
    res.end()
    return
  }
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`)
  try {
    if (url.pathname === '/health') {
      return json(res, 200, {
        ok: true,
        live: true,
        experimentId,
        stage: state.stage,
        feedHealthy,
        codeVersion: RESEARCH_CODE_VERSION,
        generatedAt: Math.floor(Date.now() / 1000),
      })
    }
    if (url.pathname === '/status') {
      const summary = summarizeExperiment(state)
      return json(res, 200, {
        live: true,
        experimentId,
        experiments: listExperiments(),
        registryCount: STRATEGY_REGISTRY.length,
        registry: STRATEGY_REGISTRY.map((s) => ({
          id: s.id, name: s.name, family: s.family, rules: s.rules, version: s.version,
          usesFormingCandle: s.usesFormingCandle, pairedWith: s.pairedWith ?? null,
        })),
        state: {
          mode: state.mode,
          stage: state.stage,
          startedAt: state.startedAt,
          endsAt: state.endsAt,
          finishedAt: state.finishedAt,
          contract: state.contract,
          enabledStrategyIds: state.enabledStrategyIds,
          lastTickEpoch: state.lastTickEpoch,
          feedHealthy,
          downtimeSec: state.downtimeSec + downtimeAccum,
          observationCount: state.observations.length,
          rejectedCount: state.rejected.length,
        },
        summary,
      })
    }
    if (url.pathname === '/strategy' && url.searchParams.get('id')) {
      const id = url.searchParams.get('id')
      const rows = state.observations.filter((o) => o.strategyId === id).slice(-200)
      const rejected = state.rejected.filter((r) => r.strategyId === id).slice(-100)
      return json(res, 200, { strategyId: id, observations: rows, rejected })
    }
    if (url.pathname === '/export/csv') {
      res.writeHead(200, {
        'Content-Type': 'text/csv',
        'Access-Control-Allow-Origin': '*',
        'Content-Disposition': `attachment; filename="${experimentId}-observations.csv"`,
      })
      return res.end(exportObservationsCsv(state))
    }
    if (url.pathname === '/export/report') {
      const md = exportReportMarkdown(summarizeExperiment(state))
      res.writeHead(200, {
        'Content-Type': 'text/markdown',
        'Access-Control-Allow-Origin': '*',
      })
      return res.end(md)
    }
    if (url.pathname === '/export/summary') {
      return json(res, 200, summarizeExperiment(state))
    }
    if (req.method === 'POST' && url.pathname === '/control/start-discovery') {
      experimentId = newExperimentId('discovery')
      state = startDiscovery(createInitialState(), Date.now())
      appendStateSnapshot(experimentId, state)
      appendEvent(experimentId, { type: 'start_discovery', at: Date.now() })
      return json(res, 200, { ok: true, experimentId, stage: state.stage, endsAt: state.endsAt })
    }
    if (req.method === 'POST' && url.pathname === '/control/resume') {
      state = resumeCollecting(state)
      persist(true)
      return json(res, 200, { ok: true, stage: state.stage, endsAt: state.endsAt })
    }
    if (req.method === 'POST' && url.pathname === '/control/stop-entries') {
      state = { ...state, stage: 'finishing' }
      persist(true)
      return json(res, 200, { ok: true, stage: state.stage })
    }
    if (req.method === 'POST' && url.pathname === '/control/start-confirmation') {
      const body = await readBody(req)
      const ids = Array.isArray(body.strategyIds) ? body.strategyIds : state.enabledStrategyIds
      experimentId = newExperimentId('confirmation')
      state = startDiscovery(createInitialState('confirmation', ids), Date.now())
      state = { ...state, mode: 'confirmation' }
      appendStateSnapshot(experimentId, state)
      return json(res, 200, { ok: true, experimentId, mode: 'confirmation', enabledStrategyIds: ids })
    }
    json(res, 404, { error: 'not found' })
  } catch (e) {
    json(res, 500, { error: e instanceof Error ? e.message : String(e) })
  }
})

api.listen(PORT, HOST, () => {
  console.log(`[research] API http://${HOST}:${PORT} experiment=${experimentId} stage=${state.stage}`)
  console.log('[research] Public ticks only — no account auth, no buy orders')
})

// --- Public market feed ---
let ws
let reqId = 1
const pending = new Map()

function connect() {
  ws = new WebSocket(PUBLIC_WS)
  ws.on('open', () => {
    console.log('[research] public WS connected')
    feedHealthy = false
    // history then ticks
    const id = reqId++
    pending.set(id, 'history')
    ws.send(JSON.stringify({
      ticks_history: SYMBOL,
      style: 'candles',
      granularity: 60,
      count: 1000,
      end: 'latest',
      req_id: id,
    }))
  })
  ws.on('message', async (raw) => {
    let msg
    try { msg = JSON.parse(String(raw)) } catch { return }
    lastReceipt = Date.now()
    if (msg.error) {
      console.warn('[research] WS error', msg.error.message)
      return
    }
    if (msg.candles && pending.get(msg.req_id) === 'history') {
      pending.delete(msg.req_id)
      const rows = msg.candles.map((c) => ({
        epoch: Number(c.epoch),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }))
      candles = finalizeByClock(candlesFromHistory(rows), Math.floor(Date.now() / 1000))
      feedHealthy = candles.length >= 120
      appendEvent(experimentId, { type: 'history_loaded', bars: candles.length, at: Date.now() })
      const subId = reqId++
      ws.send(JSON.stringify({ ticks: SYMBOL, subscribe: 1, req_id: subId }))
      return
    }
    if (msg.tick) {
      const tick = {
        epoch: Number(msg.tick.epoch),
        quote: Number(msg.tick.quote),
        symbol: msg.tick.symbol || SYMBOL,
      }
      appendTick(experimentId, { ...tick, receiptMs: Date.now() })
      const applied = applyTick(candles, tick)
      candles = applied.candles
      const age = Math.abs(Date.now() / 1000 - tick.epoch)
      const healthy = age <= 10 && candles.length >= 120
      if (!healthy) {
        if (unhealthySince == null) unhealthySince = Date.now()
        feedHealthy = false
      } else {
        if (unhealthySince != null) {
          downtimeAccum += (Date.now() - unhealthySince) / 1000
          unhealthySince = null
        }
        feedHealthy = true
      }
      const beforeCount = state.observations.length
      state = onTick(state, tick, candles, Date.now() / 1000, feedHealthy)
      state = { ...state, downtimeSec: downtimeAccum }

      // Optional public proposal for newly accepted observations (best-effort; never buy)
      if (!proposalBusy && state.observations.length > beforeCount) {
        const newest = state.observations[state.observations.length - 1]
        if (newest && !newest.touch?.quoteAvailable) {
          proposalBusy = true
          try {
            const transport = {
              request: (payload) => new Promise((resolve, reject) => {
                const id = reqId++
                const timer = setTimeout(() => {
                  pending.delete(id)
                  reject(new Error('proposal timeout'))
                }, 8000)
                pending.set(id, { resolve, reject, timer })
                ws.send(JSON.stringify({ ...payload, req_id: id }))
              }),
            }
            // Resolve pending proposal responses in message handler via pending map objects
            const quote = await proposeOneTouchOnly(transport, state.contract, SYMBOL, newest.side)
            if (quote && Number.isFinite(quote.ask) && !quote.rawError) {
              const idx = state.observations.findIndex((o) => o.id === newest.id)
              if (idx >= 0) {
                const updated = attachProposalQuote(state.observations[idx], quote, state.contract)
                const obs = [...state.observations]
                obs[idx] = updated
                state = { ...state, observations: obs }
              }
            }
          } catch {
            // leave quote unavailable
          } finally {
            proposalBusy = false
          }
        }
      }
      persist()
    }
    const p = pending.get(msg.req_id)
    if (p && typeof p === 'object' && p.resolve) {
      clearTimeout(p.timer)
      pending.delete(msg.req_id)
      p.resolve(msg)
    }
  })
  ws.on('close', () => {
    feedHealthy = false
    if (unhealthySince == null) unhealthySince = Date.now()
    appendEvent(experimentId, { type: 'ws_close', at: Date.now() })
    setTimeout(connect, 2000)
  })
  ws.on('error', () => { /* reconnect via close */ })
}

connect()

setInterval(() => {
  if (Date.now() - lastReceipt > 15000) {
    feedHealthy = false
    try { ws?.terminate() } catch { /* */ }
  }
}, 5000)

process.on('SIGINT', () => {
  persist(true)
  console.log('[research] stopped; experiment left at stage', state.stage)
  process.exit(0)
})
