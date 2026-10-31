import { useCallback, useEffect, useRef, useState } from 'react'
import { listDerivAccounts, type DerivAccount } from '../lib/deriv/tradingAuth'
import { DerivTradingClient } from '../lib/deriv/tradingClient'
import { placeTouchTrades, type TouchSideMode } from '../lib/deriv/touchTrade'
import type { ConnectionStatus, FinalSignal } from '../lib/types'

const STORAGE_KEY = 'v100_auto_trade_v1'
const HISTORY_KEY = 'v100_trade_history_v1'
const HISTORY_MAX = 500

export type TradeLogRow = {
  id: string
  at: number
  signal: FinalSignal
  barrier: string
  ok: boolean
  message: string
  contractId?: number | null
}

function loadTradeHistory(): TradeLogRow[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const rows = JSON.parse(raw) as unknown
    if (!Array.isArray(rows)) return []
    return rows.filter((r): r is TradeLogRow =>
      !!r && typeof r === 'object'
      && typeof (r as TradeLogRow).id === 'string'
      && typeof (r as TradeLogRow).at === 'number'
      && typeof (r as TradeLogRow).message === 'string',
    ).slice(0, HISTORY_MAX)
  } catch {
    return []
  }
}

function saveTradeHistory(rows: TradeLogRow[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(rows.slice(0, HISTORY_MAX)))
  } catch { /* ignore quota */ }
}

export function exportTradeHistoryCsv(rows: TradeLogRow[]): string {
  const header = 'utc,signal,barrier,ok,contract_id,message'
  const lines = rows.map((r) => {
    const utc = new Date(r.at).toISOString()
    const msg = `"${r.message.replaceAll('"', '""')}"`
    return [utc, r.signal, r.barrier, r.ok ? '1' : '0', r.contractId ?? '', msg].join(',')
  })
  return [header, ...lines].join('\n')
}

type StoredPrefs = {
  appId: string
  token: string
  accountId: string
  stake: number
  barrierAbs: number
  duration: number
  durationUnit: 's' | 'm' | 'h' | 'd'
  mode: TouchSideMode
  cooldownSec: number
  minConfidencePct: number
  /** Restore Auto trade ON after refresh */
  autoEnabled: boolean
  /** Reconnect trade WS after refresh when true */
  wantConnected: boolean
  cooldownUntil: number
  currency: string
}

export type AutoTradeSignal = {
  side: 'RISE' | 'FALL'
  confidence: number
  time: number
  isQualified: boolean
}

const DEFAULT_PREFS: StoredPrefs = {
  appId: '',
  token: '',
  accountId: '',
  stake: 1,
  barrierAbs: 0.05,
  duration: 7,
  durationUnit: 'm',
  mode: 'both',
  cooldownSec: 420,
  minConfidencePct: 80,
  autoEnabled: false,
  wantConnected: false,
  cooldownUntil: 0,
  currency: 'USD',
}

function loadPrefs(): StoredPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_PREFS }
    const p = JSON.parse(raw) as Partial<StoredPrefs>
    return {
      appId: typeof p.appId === 'string' ? p.appId : '',
      token: typeof p.token === 'string' ? p.token : '',
      accountId: typeof p.accountId === 'string' ? p.accountId : '',
      stake: typeof p.stake === 'number' && p.stake > 0 ? p.stake : 1,
      barrierAbs: typeof p.barrierAbs === 'number' && p.barrierAbs > 0 ? p.barrierAbs : 0.05,
      duration: typeof p.duration === 'number' && p.duration > 0 ? p.duration : 7,
      durationUnit: p.durationUnit === 's' || p.durationUnit === 'h' || p.durationUnit === 'd' ? p.durationUnit : 'm',
      mode: p.mode === 'signal' ? 'signal' : 'both',
      cooldownSec: typeof p.cooldownSec === 'number' && p.cooldownSec >= 0 ? p.cooldownSec : 420,
      minConfidencePct:
        typeof p.minConfidencePct === 'number' && p.minConfidencePct >= 0 && p.minConfidencePct <= 100
          ? p.minConfidencePct
          : 80,
      autoEnabled: p.autoEnabled === true,
      wantConnected: p.wantConnected === true,
      cooldownUntil: typeof p.cooldownUntil === 'number' && p.cooldownUntil > Date.now() ? p.cooldownUntil : 0,
      currency: typeof p.currency === 'string' && p.currency ? p.currency : 'USD',
    }
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

function savePrefs(payload: StoredPrefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch { /* ignore quota */ }
}

export function useAutoTrade(symbol: string) {
  const prefs0 = loadPrefs()
  const [appId, setAppId] = useState(prefs0.appId)
  const [token, setToken] = useState(prefs0.token)
  const [accountId, setAccountId] = useState(prefs0.accountId)
  const [accounts, setAccounts] = useState<DerivAccount[]>([])
  const [stake, setStake] = useState(prefs0.stake)
  const [barrierAbs, setBarrierAbs] = useState(prefs0.barrierAbs)
  const [duration, setDuration] = useState(prefs0.duration)
  const [durationUnit, setDurationUnit] = useState(prefs0.durationUnit)
  const [mode, setMode] = useState<TouchSideMode>(prefs0.mode)
  const [cooldownSec, setCooldownSec] = useState(prefs0.cooldownSec)
  const [minConfidencePct, setMinConfidencePct] = useState(prefs0.minConfidencePct)
  const [autoEnabled, setAutoEnabled] = useState(false)
  const [wantConnected, setWantConnected] = useState(prefs0.wantConnected)
  const [status, setStatus] = useState<ConnectionStatus>('DISCONNECTED')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [balance, setBalance] = useState<number | null>(null)
  const [currency, setCurrency] = useState(prefs0.currency)
  const [log, setLog] = useState<TradeLogRow[]>(() => loadTradeHistory())
  const [cooldownUntil, setCooldownUntil] = useState(prefs0.cooldownUntil)
  const [cooldownLeft, setCooldownLeft] = useState(0)
  const clientRef = useRef<DerivTradingClient | null>(null)
  const tradedSignals = useRef(new Set<string>())
  const cooldownUntilRef = useRef(prefs0.cooldownUntil)
  const restoreTried = useRef(false)
  const pendingAuto = useRef(prefs0.autoEnabled)
  const settingsRef = useRef({
    stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
  })

  useEffect(() => {
    settingsRef.current = {
      stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
    }
  }, [stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled])

  useEffect(() => {
    savePrefs({
      appId,
      token,
      accountId,
      stake,
      barrierAbs,
      duration,
      durationUnit,
      mode,
      cooldownSec,
      minConfidencePct,
      autoEnabled,
      wantConnected,
      cooldownUntil,
      currency,
    })
  }, [
    appId, token, accountId, stake, barrierAbs, duration, durationUnit, mode,
    cooldownSec, minConfidencePct, autoEnabled, wantConnected, cooldownUntil, currency,
  ])

  useEffect(() => {
    const tick = () => {
      const left = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
      setCooldownLeft(left)
      if (left === 0 && cooldownUntilRef.current !== 0) {
        cooldownUntilRef.current = 0
        setCooldownUntil(0)
      }
    }
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [])

  const pushLog = useCallback((row: Omit<TradeLogRow, 'id' | 'at'>) => {
    setLog((prev) => {
      const next = [{ id: `${Date.now()}:${Math.random()}`, at: Date.now(), ...row }, ...prev].slice(0, HISTORY_MAX)
      saveTradeHistory(next)
      return next
    })
  }, [])

  const clearTradeHistory = useCallback(() => {
    setLog([])
    saveTradeHistory([])
  }, [])

  const disconnect = useCallback(() => {
    pendingAuto.current = false
    setAutoEnabled(false)
    setWantConnected(false)
    clientRef.current?.close()
    clientRef.current = null
    setStatus('DISCONNECTED')
    setBalance(null)
  }, [])

  const loadAccounts = useCallback(async (override?: { token?: string; appId?: string; accountId?: string }) => {
    const t = (override?.token ?? token).trim()
    const a = (override?.appId ?? appId).trim()
    const preferId = override?.accountId ?? accountId
    if (!t || !a) {
      setError('Enter App ID and API token first')
      return [] as DerivAccount[]
    }
    setBusy(true)
    setError(null)
    try {
      const rows = await listDerivAccounts(t, a)
      setAccounts(rows)
      const preferred =
        rows.find((row) => row.account_id === preferId) ||
        rows.find((row) => (row.account_type || '').toLowerCase() === 'demo') ||
        rows[0]
      if (preferred) {
        setAccountId(preferred.account_id)
        if (preferred.currency) setCurrency(preferred.currency)
        if (typeof preferred.balance === 'number') setBalance(preferred.balance)
      }
      return rows
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setAccounts([])
      return [] as DerivAccount[]
    } finally {
      setBusy(false)
    }
  }, [token, appId, accountId])

  const connect = useCallback(async (override?: {
    token?: string
    appId?: string
    accountId?: string
    enableAuto?: boolean
  }) => {
    const t = (override?.token ?? token).trim()
    const a = (override?.appId ?? appId).trim()
    const id = (override?.accountId ?? accountId).trim()
    if (!t || !a || !id) {
      setError('Enter App ID, API token, and select an account')
      return false
    }
    setBusy(true)
    setError(null)
    setWantConnected(true)
    clientRef.current?.close()
    const client = new DerivTradingClient(t, a, id)
    client.setStatusHandler(setStatus)
    client.subscribe((msg) => {
      if (msg.msg_type === 'balance') {
        const b = msg.balance as { balance?: number; currency?: string } | undefined
        if (typeof b?.balance === 'number') setBalance(b.balance)
        if (typeof b?.currency === 'string') setCurrency(b.currency)
      }
    })
    try {
      await client.connect()
      clientRef.current = client
      const bal = await client.request({ balance: 1, subscribe: 1 })
      const b = bal.balance as { balance?: number; currency?: string } | undefined
      if (typeof b?.balance === 'number') setBalance(b.balance)
      if (typeof b?.currency === 'string') setCurrency(b.currency)
      const enable = override?.enableAuto ?? pendingAuto.current
      if (enable) {
        pendingAuto.current = true
        setAutoEnabled(true)
      }
      return true
    } catch (err) {
      client.close()
      clientRef.current = null
      setStatus('DISCONNECTED')
      setAutoEnabled(false)
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }, [appId, token, accountId])

  // Restore saved credentials + reconnect after refresh/close.
  useEffect(() => {
    if (restoreTried.current) return
    restoreTried.current = true
    const prefs = loadPrefs()
    if (!prefs.appId.trim() || !prefs.token.trim()) return

    let cancelled = false
    void (async () => {
      const rows = await loadAccounts({
        token: prefs.token,
        appId: prefs.appId,
        accountId: prefs.accountId,
      })
      if (cancelled) return
      const id =
        rows.find((r) => r.account_id === prefs.accountId)?.account_id
        || rows[0]?.account_id
        || prefs.accountId
      if (!prefs.wantConnected || !id) return
      pendingAuto.current = prefs.autoEnabled
      await connect({
        token: prefs.token,
        appId: prefs.appId,
        accountId: id,
        enableAuto: prefs.autoEnabled,
      })
    })()

    return () => {
      cancelled = true
      clientRef.current?.close()
      clientRef.current = null
    }
  }, [loadAccounts, connect])

  const setAutoEnabledSafe = useCallback((on: boolean) => {
    pendingAuto.current = on
    setAutoEnabled(on)
  }, [])

  const onTradeSignal = useCallback(async (sig: AutoTradeSignal) => {
    const s = settingsRef.current
    if (!s.autoEnabled) return
    if (sig.side !== 'RISE' && sig.side !== 'FALL') return
    const confPct = Math.round(100 * sig.confidence)
    if (confPct < s.minConfidencePct) return
    const key = `${sig.time}:${sig.side}`
    if (tradedSignals.current.has(key)) return
    const waitLeft = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
    if (waitLeft > 0) {
      const cdKey = `cd:${key}`
      if (!tradedSignals.current.has(cdKey)) {
        tradedSignals.current.add(cdKey)
        pushLog({
          signal: sig.side,
          barrier: '—',
          ok: false,
          message: `Skipped · cooldown ${waitLeft}s left · conf ${confPct}%`,
        })
      }
      return
    }
    const client = clientRef.current
    if (!client || client.connectionStatus !== 'CONNECTED') {
      const ncKey = `nc:${key}`
      if (!tradedSignals.current.has(ncKey)) {
        tradedSignals.current.add(ncKey)
        pushLog({
          signal: sig.side,
          barrier: '—',
          ok: false,
          message: `Skipped · conf ${confPct}% but trading socket not connected`,
        })
      }
      return
    }
    tradedSignals.current.add(key)
    setBusy(true)
    try {
      const results = await placeTouchTrades(client, {
        symbol: s.symbol,
        currency: s.currency,
        stake: s.stake,
        barrierAbs: s.barrierAbs,
        duration: s.duration,
        durationUnit: s.durationUnit,
        mode: s.mode,
      }, sig.side)
      for (const r of results) {
        pushLog({
          signal: sig.side,
          barrier: r.barrier,
          ok: true,
          message: `Bought ONETOUCH · conf ${confPct}% · stake≈${r.buyPrice ?? s.stake}`,
          contractId: r.contractId,
        })
      }
      if (results.length > 0 && s.cooldownSec > 0) {
        const until = Date.now() + s.cooldownSec * 1000
        cooldownUntilRef.current = until
        setCooldownUntil(until)
        pushLog({
          signal: sig.side,
          barrier: '—',
          ok: true,
          message: `Cooldown started · next trade after ${s.cooldownSec}s`,
        })
      }
    } catch (err) {
      pushLog({
        signal: sig.side,
        barrier: '—',
        ok: false,
        message: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setBusy(false)
    }
  }, [pushLog])

  return {
    appId, setAppId,
    token, setToken,
    accountId, setAccountId,
    accounts,
    stake, setStake,
    barrierAbs, setBarrierAbs,
    duration, setDuration,
    durationUnit, setDurationUnit,
    mode, setMode,
    cooldownSec, setCooldownSec,
    minConfidencePct, setMinConfidencePct,
    cooldownLeft, cooldownUntil,
    autoEnabled, setAutoEnabled: setAutoEnabledSafe,
    status, busy, error, balance, currency, log,
    clearTradeHistory,
    loadAccounts: () => void loadAccounts(),
    connect: () => void connect(),
    disconnect,
    onTradeSignal,
  }
}
