/**
 * Always-on C5 Auto trade worker for VPS.
 * Continues after the browser closes. Places real ONETOUCH orders when enabled.
 *
 *   npm run autotrade:worker
 *   AUTO_TRADE_API_HOST=0.0.0.0 npm run autotrade:worker
 *
 * Safety: Auto starts OFF unless settings.json has autoEnabled true AND credentials exist.
 * Prefer enabling from the Trade settings UI after deploy.
 */
import { createServer as createHttpServer } from 'node:http'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { createServer as createViteServer } from 'vite'
import WebSocket from 'ws'

// Browser trading client uses global WebSocket — map Node `ws` before SSR import.
globalThis.WebSocket = WebSocket

const PORT = Number(process.env.AUTO_TRADE_API_PORT || 8788)
const HOST = process.env.AUTO_TRADE_API_HOST || '127.0.0.1'
const SYMBOL = process.env.VITE_DERIV_SYMBOL || process.env.AUTO_TRADE_SYMBOL || 'R_100'
const PUBLIC_WS = process.env.VITE_DERIV_PUBLIC_WS || 'wss://api.derivws.com/trading/v1/options/ws/public'
const DATA_ROOT = process.env.AUTO_TRADE_DATA_ROOT || join(process.cwd(), 'auto-trade-data')
const SIGNAL_WINDOW_SECONDS = 5
const HISTORY = 500
const MIN_STAKE = 0.35

mkdirSync(DATA_ROOT, { recursive: true })

const vite = await createViteServer({
  root: process.cwd(),
  configFile: false,
  logLevel: 'error',
  server: { middlewareMode: true, hmr: false },
  appType: 'custom',
})

const {
  sanitizeServerAutoSettings,
  DEFAULT_SERVER_AUTO_SETTINGS,
  credentialsReady,
  publicServerAutoSettings,
} = await vite.ssrLoadModule('/src/lib/autoTrade/serverSettings.ts')
const { evaluateC5LiveBar, evaluateC5LatestClosed, C5_STRATEGY_ID } =
  await vite.ssrLoadModule('/src/lib/strategy/c5Strategy.ts')
const { authorizeC5AutoTrade, c5DedupeKey, isC5EntryExpired } =
  await vite.ssrLoadModule('/src/lib/strategy/c5AutoAuth.ts')
const { applyTick, candlesFromHistory, finalizeByClock } =
  await vite.ssrLoadModule('/src/lib/deriv/candles.ts')
const { placeTouchTrades } = await vite.ssrLoadModule('/src/lib/deriv/touchTrade.ts')
const { DerivTradingClient } = await vite.ssrLoadModule('/src/lib/deriv/tradingClient.ts')

const SETTINGS_PATH = join(DATA_ROOT, 'settings.json')
const LOG_PATH = join(DATA_ROOT, 'trades.jsonl')
const LOG_MAX = 500

function loadSettingsFromDisk() {
  let disk = {}
  if (existsSync(SETTINGS_PATH)) {
    try { disk = JSON.parse(readFileSync(SETTINGS_PATH, 'utf8')) } catch { disk = {} }
  }
  // Env can seed credentials on first boot; Auto still defaults OFF unless disk/env enable.
  const fromEnv = {
    appId: process.env.AUTO_TRADE_APP_ID || disk.appId,
    token: process.env.AUTO_TRADE_TOKEN || disk.token,
    accountId: process.env.AUTO_TRADE_ACCOUNT_ID || disk.accountId,
    stake: process.env.AUTO_TRADE_STAKE ? Number(process.env.AUTO_TRADE_STAKE) : disk.stake,
    useFullStake: process.env.AUTO_TRADE_USE_FULL_STAKE != null
      ? process.env.AUTO_TRADE_USE_FULL_STAKE !== 'false'
      : disk.useFullStake,
    barrierAbs: process.env.AUTO_TRADE_BARRIER ? Number(process.env.AUTO_TRADE_BARRIER) : disk.barrierAbs,
    duration: process.env.AUTO_TRADE_DURATION ? Number(process.env.AUTO_TRADE_DURATION) : disk.duration,
    durationUnit: process.env.AUTO_TRADE_DURATION_UNIT || disk.durationUnit,
    cooldownSec: process.env.AUTO_TRADE_COOLDOWN_SEC
      ? Number(process.env.AUTO_TRADE_COOLDOWN_SEC)
      : disk.cooldownSec,
    currency: process.env.AUTO_TRADE_CURRENCY || disk.currency,
    symbol: process.env.AUTO_TRADE_SYMBOL || disk.symbol || SYMBOL,
    autoEnabled: process.env.AUTO_TRADE_ENABLED === 'true' || disk.autoEnabled === true,
  }
  return sanitizeServerAutoSettings(fromEnv, { ...DEFAULT_SERVER_AUTO_SETTINGS, symbol: SYMBOL })
}

let settings = loadSettingsFromDisk()
let cooldownUntil = settings.cooldownUntil > Date.now() ? settings.cooldownUntil : 0

function saveSettings(next) {
  settings = sanitizeServerAutoSettings({
    ...next,
    cooldownUntil: typeof next.cooldownUntil === 'number' ? next.cooldownUntil : cooldownUntil,
  })
  if (settings.cooldownUntil > Date.now()) cooldownUntil = settings.cooldownUntil
  else if (!(settings.cooldownUntil > Date.now())) cooldownUntil = Math.max(0, cooldownUntil)
  writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2))
}

function setCooldownUntil(until) {
  cooldownUntil = until
  settings = { ...settings, cooldownUntil: until }
  try {
    writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2))
  } catch { /* ignore */ }
}

function appendLog(row) {
  const line = JSON.stringify({ ...row, at: row.at || Date.now() })
  try {
    writeFileSync(LOG_PATH, existsSync(LOG_PATH) ? `${readFileSync(LOG_PATH, 'utf8')}${line}\n` : `${line}\n`)
  } catch { /* ignore */ }
  log.unshift(row)
  if (log.length > LOG_MAX) log.length = LOG_MAX
}

function loadLog() {
  if (!existsSync(LOG_PATH)) return []
  try {
    return readFileSync(LOG_PATH, 'utf8')
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l))
      .reverse()
      .slice(0, LOG_MAX)
  } catch {
    return []
  }
}

saveSettings(settings) // normalize file on disk
let log = loadLog()
let candles = []
let feedHealthy = false
let tradeStatus = 'DISCONNECTED'
let lastError = null
let lastSignal = null
let tradeLock = false
let tradingClient = null
let reconnectTradeTimer = null
const notified = new Set()
let lastTickSeen = null
let publicWs = null
let historyId = null
let reqId = 1
const pending = new Map()

function floorStake(amount) {
  return Math.floor(amount * 100) / 100
}

function statusPayload() {
  return {
    ok: true,
    worker: 'autotrade',
    symbol: settings.symbol,
    feedHealthy,
    tradeStatus,
    autoEnabled: settings.autoEnabled,
    credentialsReady: credentialsReady(settings),
    cooldownLeftSec: Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000)),
    lastError,
    lastSignal,
    settings: publicServerAutoSettings(settings),
    log: log.slice(0, 50),
    candleCount: candles.length,
    note: 'VPS Auto runs with the browser closed. Keep browser Auto OFF to avoid double buys.',
  }
}

async function ensureTradingClient() {
  if (!credentialsReady(settings)) {
    tradingClient?.close()
    tradingClient = null
    tradeStatus = 'DISCONNECTED'
    return null
  }
  if (tradingClient && tradingClient.connectionStatus === 'CONNECTED') return tradingClient
  tradingClient?.close()
  tradingClient = new DerivTradingClient(settings.token, settings.appId, settings.accountId)
  tradingClient.setStatusHandler((s) => { tradeStatus = s })
  await tradingClient.connect()
  tradeStatus = tradingClient.connectionStatus
  return tradingClient
}

async function maybeConnectTrade() {
  if (!settings.autoEnabled || !credentialsReady(settings)) return
  try {
    await ensureTradingClient()
    lastError = null
  } catch (err) {
    lastError = err instanceof Error ? err.message : String(err)
    tradeStatus = 'DISCONNECTED'
    appendLog({
      id: `err-${Date.now()}`,
      at: Date.now(),
      signal: 'NO_TRADE',
      barrier: '—',
      ok: false,
      message: `VPS Auto · trade connect failed · ${lastError}`,
    })
  }
}

async function executeC5(side, expiresAt, dedupeKey) {
  if (tradeLock) return
  if (Date.now() < cooldownUntil) {
    appendLog({
      id: `cd-${Date.now()}`,
      at: Date.now(),
      signal: side,
      barrier: '—',
      ok: false,
      message: `VPS Auto skipped · cooldown ${Math.ceil((cooldownUntil - Date.now()) / 1000)}s left`,
    })
    return
  }
  tradeLock = true
  notified.add(dedupeKey)
  try {
    const client = await ensureTradingClient()
    if (!client || client.connectionStatus !== 'CONNECTED') {
      throw new Error('trading socket not connected')
    }
    if (isC5EntryExpired(expiresAt, Date.now() / 1000)) {
      appendLog({
        id: `exp-${Date.now()}`,
        at: Date.now(),
        signal: side,
        barrier: '—',
        ok: false,
        message: 'VPS Auto skipped · entry window expired before buy prep',
      })
      return
    }

    // Main Wait-after-trade cooldown — no further Auto buys until it ends.
    if (settings.cooldownSec > 0) {
      setCooldownUntil(Date.now() + settings.cooldownSec * 1000)
    }

    let tradeStake = settings.stake
    if (settings.useFullStake) {
      const balMsg = await client.request({ balance: 1 })
      if (isC5EntryExpired(expiresAt, Date.now() / 1000)) {
        appendLog({
          id: `exp-bal-${Date.now()}`,
          at: Date.now(),
          signal: side,
          barrier: '—',
          ok: false,
          message: 'VPS Auto skipped · entry window expired during balance request',
        })
        setCooldownUntil(0)
        return
      }
      const balObj = balMsg.balance
      const bal = typeof balObj?.balance === 'number' ? balObj.balance : null
      if (typeof balObj?.currency === 'string') settings.currency = balObj.currency
      if (bal == null || !(bal > 0)) throw new Error('Full stake: no account balance')
      tradeStake = floorStake(bal)
      if (tradeStake < MIN_STAKE) throw new Error(`Full stake below minimum ${MIN_STAKE}`)
      settings.stake = tradeStake
      saveSettings(settings)
    }

    const results = await placeTouchTrades(client, {
      symbol: settings.symbol,
      currency: settings.currency,
      stake: tradeStake,
      barrierAbs: settings.barrierAbs,
      duration: settings.duration,
      durationUnit: settings.durationUnit,
      mode: 'signal',
    }, side, {
      beforeBuy: () => {
        if (isC5EntryExpired(expiresAt, Date.now() / 1000)) {
          throw new Error('C5 skipped · entry window expired after proposal')
        }
        if (client.connectionStatus !== 'CONNECTED') {
          throw new Error('C5 skipped · trading socket not connected after proposal')
        }
      },
    })

    for (const r of results) {
      appendLog({
        id: `buy-${r.contractId || Date.now()}`,
        at: Date.now(),
        signal: side,
        barrier: r.barrier,
        ok: true,
        contractId: r.contractId,
        message: `VPS C5 AUTO · Bought ONETOUCH · barrier ${r.barrier} · stake≈${r.buyPrice ?? tradeStake}`,
      })
    }
    if (!results.length) setCooldownUntil(0)
    lastError = null
  } catch (err) {
    setCooldownUntil(0)
    const msg = err instanceof Error ? err.message : String(err)
    lastError = msg
    appendLog({
      id: `fail-${Date.now()}`,
      at: Date.now(),
      signal: side,
      barrier: '—',
      ok: false,
      message: msg.startsWith('C5 skipped') || msg.startsWith('VPS') ? msg : `VPS Auto · ${msg}`,
    })
  } finally {
    tradeLock = false
  }
}

function handleQualified(ev, expiresAt, source) {
  if (ev.final !== 'RISE' && ev.final !== 'FALL') return
  const id = `${C5_STRATEGY_ID}:${ev.time}:${ev.final}`
  if (notified.has(id)) return
  lastSignal = {
    side: ev.final,
    candleTime: ev.time,
    source,
    at: Date.now(),
    expiresAt,
  }
  const auth = authorizeC5AutoTrade({
    strategyId: C5_STRATEGY_ID,
    side: ev.final,
    isQualified: true,
    requiredPassed: ev.requiredPassed ?? 4,
    requiredTotal: ev.requiredTotal ?? 4,
    flags: ev.confirmations,
    candleTime: ev.time,
    expiresAt,
  }, {
    autoEnabled: settings.autoEnabled,
    nowSec: Date.now() / 1000,
    alreadyTraded: notified.has(c5DedupeKey(ev.time, ev.final)),
  })
  if (!auth.ok) {
    notified.add(id) // avoid log spam for same candle
    appendLog({
      id: `skip-${Date.now()}`,
      at: Date.now(),
      signal: ev.final,
      barrier: '—',
      ok: false,
      message: `VPS Auto skipped · ${auth.reason}${source === 'forming' ? ' · forming' : ' · close'}`,
    })
    return
  }
  void executeC5(ev.final, expiresAt, auth.dedupeKey)
}

function onPublicTick(tick) {
  const receiptEpoch = Date.now() / 1000
  if (!Number.isFinite(tick.epoch) || !Number.isFinite(tick.quote) || tick.quote <= 0) return
  if (Math.abs(receiptEpoch - tick.epoch) > 10) {
    feedHealthy = false
    return
  }
  feedHealthy = candles.length >= 80
  const { candles: next, closedCandle } = applyTick(candles, tick)
  candles = next.slice(-HISTORY)

  const nowSec = Date.now() / 1000
  if (closedCandle && tick.epoch - (closedCandle.time + 60) <= SIGNAL_WINDOW_SECONDS
    && nowSec - (closedCandle.time + 60) <= SIGNAL_WINDOW_SECONDS) {
    const ev = evaluateC5LatestClosed(candles)
    if (ev && ev.time === closedCandle.time && ev.isQualified) {
      handleQualified(ev, closedCandle.time + 60 + SIGNAL_WINDOW_SECONDS, 'close')
    }
  }

  if (tick.epoch !== lastTickSeen) {
    const isFirst = lastTickSeen == null
    lastTickSeen = tick.epoch
    const last = candles.at(-1)
    if (!isFirst && feedHealthy && last && !last.isClosed) {
      const ev = evaluateC5LiveBar(candles)
      if (ev && ev.time === last.time && ev.isQualified) {
        handleQualified(ev, nowSec + SIGNAL_WINDOW_SECONDS, 'forming')
      }
    }
  }
}

function reconnectFeed(reason) {
  console.warn(`[autotrade] reconnect feed: ${reason}`)
  feedHealthy = false
  lastTickSeen = null
  try { publicWs?.close() } catch { /* ignore */ }
  publicWs = null
  setTimeout(connectPublicFeed, 1500)
}

function connectPublicFeed() {
  const ws = new WebSocket(PUBLIC_WS)
  publicWs = ws
  ws.on('open', () => {
    console.log('[autotrade] public feed connected')
    historyId = reqId++
    const end = Math.floor(Date.now() / 1000)
    ws.send(JSON.stringify({
      ticks_history: SYMBOL,
      style: 'candles',
      granularity: 60,
      count: HISTORY,
      end,
      req_id: historyId,
    }))
  })
  ws.on('message', (raw) => {
    let msg
    try { msg = JSON.parse(String(raw)) } catch { return }
    if (msg.error) {
      lastError = msg.error.message || 'public feed error'
      reconnectFeed(lastError)
      return
    }
    if (Array.isArray(msg.candles) && msg.req_id === historyId) {
      const rows = msg.candles
      candles = finalizeByClock(candlesFromHistory(rows), Math.floor(Date.now() / 1000)).slice(-HISTORY)
      feedHealthy = candles.length >= 80
      const subId = reqId++
      pending.set(subId, 'ticks')
      ws.send(JSON.stringify({ ticks: SYMBOL, subscribe: 1, req_id: subId }))
      return
    }
    if (msg.tick) {
      onPublicTick({
        epoch: Number(msg.tick.epoch),
        quote: Number(msg.tick.quote),
        symbol: msg.tick.symbol || SYMBOL,
      })
    }
  })
  ws.on('close', () => reconnectFeed('public socket closed'))
  ws.on('error', () => { /* close handler reconnects */ })
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw) return resolve({})
      try { resolve(JSON.parse(raw)) } catch (e) { reject(e) }
    })
    req.on('error', reject)
  })
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
  })
  res.end(body)
}

const server = createHttpServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://${HOST}:${PORT}`)
  const path = url.pathname.replace(/\/$/, '') || '/'
  try {
    if (req.method === 'GET' && (path === '/health' || path === '/')) {
      return sendJson(res, 200, { ok: true, service: 'autotrade-worker' })
    }
    if (req.method === 'GET' && path === '/status') {
      return sendJson(res, 200, statusPayload())
    }
    if (req.method === 'POST' && path === '/control/settings') {
      const body = await readBody(req)
      const next = {
        ...settings,
        ...body,
        // Never force Auto ON just because settings were saved.
        autoEnabled: body.autoEnabled === true ? true : body.autoEnabled === false ? false : settings.autoEnabled,
      }
      if (typeof body.token === 'string' && body.token.includes('…')) {
        next.token = settings.token // keep existing when masked token posted back
      }
      saveSettings(next)
      if (!settings.autoEnabled) {
        tradingClient?.close()
        tradingClient = null
        tradeStatus = 'DISCONNECTED'
      } else {
        void maybeConnectTrade()
      }
      return sendJson(res, 200, statusPayload())
    }
    if (req.method === 'POST' && path === '/control/enable') {
      if (!credentialsReady(settings)) {
        return sendJson(res, 400, { ok: false, error: 'Save App ID, token, and account first' })
      }
      saveSettings({ ...settings, autoEnabled: true })
      await maybeConnectTrade()
      appendLog({
        id: `on-${Date.now()}`,
        at: Date.now(),
        signal: 'NO_TRADE',
        barrier: '—',
        ok: true,
        message: 'VPS Auto ENABLED · browser may be closed',
      })
      return sendJson(res, 200, statusPayload())
    }
    if (req.method === 'POST' && path === '/control/disable') {
      saveSettings({ ...settings, autoEnabled: false })
      tradingClient?.close()
      tradingClient = null
      tradeStatus = 'DISCONNECTED'
      appendLog({
        id: `off-${Date.now()}`,
        at: Date.now(),
        signal: 'NO_TRADE',
        barrier: '—',
        ok: true,
        message: 'VPS Auto DISABLED',
      })
      return sendJson(res, 200, statusPayload())
    }
    if (req.method === 'POST' && path === '/control/clear-log') {
      log = []
      try { writeFileSync(LOG_PATH, '') } catch { /* ignore */ }
      return sendJson(res, 200, statusPayload())
    }
    sendJson(res, 404, { ok: false, error: 'not found' })
  } catch (err) {
    sendJson(res, 500, { ok: false, error: err instanceof Error ? err.message : String(err) })
  }
})

server.listen(PORT, HOST, () => {
  console.log(`[autotrade] API http://${HOST}:${PORT}`)
  console.log(`[autotrade] data ${DATA_ROOT}`)
  console.log(`[autotrade] Auto ${settings.autoEnabled ? 'ON' : 'OFF'} · credentials ${credentialsReady(settings) ? 'ready' : 'missing'}`)
  connectPublicFeed()
  if (settings.autoEnabled) void maybeConnectTrade()
})

setInterval(() => {
  if (publicWs?.readyState === WebSocket.OPEN) {
    try { publicWs.send(JSON.stringify({ ping: 1, req_id: reqId++ })) } catch { /* ignore */ }
  }
}, 30000)
