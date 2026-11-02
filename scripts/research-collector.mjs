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
  RESEARCH_CODE_VERSION,
} = await vite.ssrLoadModule('/src/lib/research/experimentEngine.ts')
const {
  newExperimentId,
  latestExperimentId,
  loadState,
  appendSessionSnapshot, loadSession, appendJournal, readJournal,
  appendTick,
  appendEvent,
  setActiveExperiment,
  listExperiments,
  listExperimentSummaries,
} = await vite.ssrLoadModule('/src/lib/research/experimentStore.ts')
const { summarizeExperiment, exportObservationsCsv, exportReportMarkdown } =
  await vite.ssrLoadModule('/src/lib/research/summarizeExperiment.ts')
const { applyTick, candlesFromHistory, finalizeByClock } =
  await vite.ssrLoadModule('/src/lib/deriv/candles.ts')
const { proposeOneTouchOnly } =
  await vite.ssrLoadModule('/src/lib/research/proposeOnly.ts')
const { STRATEGY_REGISTRY } =
  await vite.ssrLoadModule('/src/lib/research/strategies/registry.ts')

const { applyResearchEvent, emptyResearchSession } = await vite.ssrLoadModule('/src/lib/research/researchSession.ts')
const { ResearchQuoteScheduler } = await vite.ssrLoadModule('/src/lib/research/quoteScheduler.ts')
const { startResearchHeartbeat } = await vite.ssrLoadModule('/src/lib/research/feedHeartbeat.ts')
let experimentId = idArg || latestExperimentId() || newExperimentId('discovery')
let session = loadSession(experimentId) || { ...emptyResearchSession(), state: loadState(experimentId) || createInitialState() }
// Restore all journal events after the atomic checkpoint, including quote responses.
if (session.state.codeVersion === RESEARCH_CODE_VERSION) {
  const events = readJournal(experimentId, true)
  for (const event of events.slice(session.sequence)) session = applyResearchEvent(session, event)
}
let state = session.state
let candles = session.candles
let lastPersist = 0
let feedHealthy = false
let lastReceipt = 0
let quotes = null
const feedDiagnostics = { since: Date.now() / 1000, disconnects: 0, connections: 0,
  lastDisconnectEpoch: null, lastDisconnectReason: null, maxTickGapSec: 0 }

function logFeedEvent(reason) {
  feedDiagnostics.lastDisconnectReason = reason
  appendEvent(experimentId, { type: 'feed_diagnostic', epoch: Date.now() / 1000, reason })
  console.warn(`[research] ${reason}`)
}

function record(event) {
  if (state.codeVersion !== RESEARCH_CODE_VERSION) return
  appendJournal(experimentId, event)
  session = applyResearchEvent(session, event)
  state = session.state
  candles = session.candles
}

function persist(force = false) {
  if (state.codeVersion !== RESEARCH_CODE_VERSION) return
  const now = Date.now()
  if (!force && now - lastPersist < 2000) return
  lastPersist = now
  appendSessionSnapshot(experimentId, session)
}

function loadExperiment(id) {
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new Error('Invalid experiment ID')
  if (!listExperiments().includes(id)) throw new Error(`Experiment not found: ${id}`)
  persist(true)
  quotes?.cancel()
  experimentId = id
  session = loadSession(experimentId) || { ...emptyResearchSession(), state: loadState(experimentId) || createInitialState() }
  if (session.state.codeVersion === RESEARCH_CODE_VERSION) {
    const events = readJournal(experimentId, true)
    for (const event of events.slice(session.sequence)) session = applyResearchEvent(session, event)
  }
  state = session.state
  candles = session.candles
  // Historical candles from an older run must not seed new live signals.
  record({ type: 'feed_unhealthy' })
  feedHealthy = false
  resetQuotes()
  persist(true)
  setActiveExperiment(experimentId)
  reconnectFeed('Research run changed; resynchronizing history')
}

function request(payload) {
  return new Promise((resolve, reject) => {
    if (ws?.readyState !== WebSocket.OPEN) return reject(new Error('Public feed disconnected'))
    const id = reqId++
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Proposal timeout')) }, 5000)
    pending.set(id, { resolve, reject, timer })
    ws.send(JSON.stringify({ ...payload, req_id: id }), (error) => {
      if (error) { clearTimeout(timer); pending.delete(id); reject(error) }
    })
  })
}

function resetQuotes() {
  quotes?.cancel()
  const runId = experimentId
  const contract = { ...state.contract }
  quotes = new ResearchQuoteScheduler(
    (side) => proposeOneTouchOnly({ request }, contract, SYMBOL, side),
    (ids, quote) => {
      if (runId !== experimentId) return
      record({ type: 'quote', ids, quote })
      persist(true)
    },
  )
}

function startRun(mode, ids = state.enabledStrategyIds) {
  persist(true)
  quotes?.cancel()
  const seed = candles
  const contract = state.contract
  experimentId = newExperimentId(mode)
  session = emptyResearchSession()
  state = session.state
  const started = startDiscovery(createInitialState(mode, ids, contract), Date.now())
  record({ type: 'start', state: started, candles: seed })
  resetQuotes()
  persist(true)
  setActiveExperiment(experimentId)
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
        experimentSummaries: listExperimentSummaries(),
        registryCount: STRATEGY_REGISTRY.length,
        feedDiagnostics,
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
          downtimeSec: state.downtimeSec,
          codeVersion: state.codeVersion,
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
      startRun('discovery')
      return json(res, 200, { ok: true, experimentId, stage: state.stage, endsAt: state.endsAt })
    }
    if (req.method === 'POST' && url.pathname === '/control/resume') {
      if (state.codeVersion !== RESEARCH_CODE_VERSION) return json(res, 409, { error: 'Pre-fix experiment is read-only. Export it and start a new discovery.' })
      record({ type: 'resume', now: Date.now() / 1000 })
      persist(true)
      return json(res, 200, { ok: true, stage: state.stage, endsAt: state.endsAt })
    }
    if (req.method === 'POST' && url.pathname === '/control/stop-entries') {
      if (state.codeVersion !== RESEARCH_CODE_VERSION) return json(res, 409, { error: 'Pre-fix experiment is read-only' })
      record({ type: 'stop_entries' })
      persist(true)
      return json(res, 200, { ok: true, stage: state.stage })
    }
    if (req.method === 'POST' && url.pathname === '/control/start-confirmation') {
      const body = await readBody(req)
      const ids = Array.isArray(body.strategyIds) ? body.strategyIds : state.enabledStrategyIds
      if (!ids.length || ids.some((id) => !STRATEGY_REGISTRY.some((s) => s.id === id)) || new Set(ids).size !== ids.length) return json(res, 400, { error: 'Invalid strategy IDs' })
      startRun('confirmation', ids)
      return json(res, 200, { ok: true, experimentId, mode: 'confirmation', enabledStrategyIds: ids })
    }
    if (req.method === 'POST' && url.pathname === '/control/reset') {
      // Clear all live records and begin a fresh discovery from zero.
      startRun('discovery')
      return json(res, 200, {
        ok: true,
        experimentId,
        stage: state.stage,
        observationCount: state.observations.length,
        endsAt: state.endsAt,
      })
    }
    if (req.method === 'POST' && url.pathname === '/control/load-experiment') {
      const body = await readBody(req)
      const id = typeof body.id === 'string' ? body.id : ''
      try {
        loadExperiment(id)
      } catch (e) {
        return json(res, 400, { error: e instanceof Error ? e.message : String(e) })
      }
      return json(res, 200, {
        ok: true,
        experimentId,
        stage: state.stage,
        observationCount: state.observations.length,
        endsAt: state.endsAt,
      })
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
let stopHeartbeat = () => {}
let reconnectTimer
let stopping = false

function reconnectFeed(reason) {
  if (stopping || !ws || ws.readyState === WebSocket.CLOSED) return
  logFeedEvent(reason)
  feedHealthy = false
  lastReceipt = 0
  ws.terminate()
}

function connect() {
  if (stopping) return
  const socket = new WebSocket(PUBLIC_WS, { handshakeTimeout: 10000 })
  ws = socket
  socket.on('open', () => {
    console.log('[research] public WS connected')
    feedDiagnostics.connections++
    feedHealthy = false
    lastReceipt = Date.now() // prevent stale watchdog from killing the socket before first tick
    // history then ticks
    const id = reqId++
    pending.set(id, 'history')
    socket.send(JSON.stringify({
      ticks_history: SYMBOL,
      style: 'candles',
      granularity: 60,
      count: 1000,
      end: 'latest',
      req_id: id,
    }))
    stopHeartbeat()
    stopHeartbeat = startResearchHeartbeat(request, reconnectFeed)
  })
  socket.on('message', (raw) => {
    if (socket !== ws || stopping) return
    let msg
    try { msg = JSON.parse(String(raw)) } catch { return }
    const reply = pending.get(msg.req_id)
    if (reply && typeof reply === 'object') {
      clearTimeout(reply.timer)
      pending.delete(msg.req_id)
      reply.resolve(msg)
      return
    }
    if (msg.error) {
      console.warn('[research] WS error', msg.error.message)
      if (reply === 'history' || reply === 'ticks') {
        pending.delete(msg.req_id)
        reconnectFeed(`${reply} request failed: ${msg.error.message}`)
      }
      return
    }
    if (msg.candles && pending.get(msg.req_id) === 'history') {
      pending.delete(msg.req_id)
      lastReceipt = Date.now()
      const rows = msg.candles.map((c) => ({
        epoch: Number(c.epoch),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }))
      const history = finalizeByClock(candlesFromHistory(rows), Math.floor(Date.now() / 1000))
      // Keep the live warmup available even while an old run is read-only.
      candles = history
      if (state.codeVersion === RESEARCH_CODE_VERSION) {
        record({ type: 'history', candles: history })
      }
      feedHealthy = candles.length >= 120
      persist(true)
      const subId = reqId++
      pending.set(subId, 'ticks')
      socket.send(JSON.stringify({ ticks: SYMBOL, subscribe: 1, req_id: subId }))
      return
    }
    if (msg.tick) {
      if (reply === 'ticks') pending.delete(msg.req_id)
      const tick = {
        epoch: Number(msg.tick.epoch),
        quote: Number(msg.tick.quote),
        symbol: msg.tick.symbol || SYMBOL,
      }
      const receiptEpoch = Date.now() / 1000
      if (!Number.isFinite(tick.epoch) || !Number.isFinite(tick.quote) || tick.quote <= 0 || tick.symbol !== SYMBOL) return
      if (state.lastTickEpoch != null && tick.epoch > state.lastTickEpoch) {
        const gap = tick.epoch - state.lastTickEpoch
        feedDiagnostics.maxTickGapSec = Math.max(feedDiagnostics.maxTickGapSec, gap)
        if (gap > 10) logFeedEvent(`Tick gap: ${gap}s`)
      }
      lastReceipt = Date.now()
      const healthy = Math.abs(receiptEpoch - tick.epoch) <= 10 && candles.length >= 120
      feedHealthy = healthy
      const beforeCount = state.observations.length
      if (state.codeVersion === RESEARCH_CODE_VERSION) {
        appendTick(experimentId, { ...tick, receiptMs: receiptEpoch * 1000 })
        record({ type: 'tick', tick, receiptEpoch, healthy })
        quotes?.enqueue(state.observations.slice(beforeCount), state.contract)
        persist()
      } else {
        candles = applyTick(candles, tick).candles.slice(-1000)
      }
    }
  })
  socket.on('close', (code, reason) => {
    if (socket !== ws) return
    stopHeartbeat()
    feedDiagnostics.disconnects++
    feedDiagnostics.lastDisconnectEpoch = Date.now() / 1000
    logFeedEvent(`Socket closed (${code}): ${String(reason) || 'no close reason supplied'}`)
    feedHealthy = false
    record({ type: 'feed_unhealthy' })
    for (const [id, job] of pending) {
      if (typeof job === 'object') { clearTimeout(job.timer); job.reject(new Error('Public feed disconnected')) }
      pending.delete(id)
    }
    persist(true)
    if (!stopping) reconnectTimer = setTimeout(connect, 2000)
  })
  socket.on('error', (error) => { logFeedEvent(`Socket error: ${error.message}`) })
}

record({ type: 'feed_unhealthy' })
resetQuotes()
persist(true)
setActiveExperiment(experimentId)
connect()

const clockTimer = setInterval(() => {
  // Only treat as stale after we have received at least one message (history or tick).
  if (lastReceipt > 0 && Date.now() - lastReceipt > 20000) {
    feedHealthy = false
    record({ type: 'feed_unhealthy' })
    reconnectFeed('No market data for 20s; reconnecting')
  }
  if (state.codeVersion === RESEARCH_CODE_VERSION) {
    record({ type: 'clock', now: Date.now() / 1000 })
    persist()
  }
}, 1000)

function shutdown() {
  if (stopping) return
  stopping = true
  stopHeartbeat()
  clearInterval(clockTimer)
  clearTimeout(reconnectTimer)
  quotes?.cancel()
  persist(true)
  console.log('[research] stopped; experiment left at stage', state.stage)
  process.exit(0)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
