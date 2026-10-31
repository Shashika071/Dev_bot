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
  /** Seconds to wait after a trade before the next auto entry */
  cooldownSec: number
  /** Only trade when side confidence % is at least this (4/5 = 80) */
  minConfidencePct: number
}

export type AutoTradeSignal = {
  side: 'RISE' | 'FALL'
  /** 0–1 from how many of the 5 conditions passed on the closed candle */
  confidence: number
  time: number
  isQualified: boolean
}

function loadPrefs(): StoredPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) throw new Error('empty')
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
    }
  } catch {
    return {
      appId: '', token: '', accountId: '',
      stake: 1, barrierAbs: 0.05, duration: 7, durationUnit: 'm', mode: 'both',
      cooldownSec: 420, minConfidencePct: 80,
    }
  }
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
  const [status, setStatus] = useState<ConnectionStatus>('DISCONNECTED')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [balance, setBalance] = useState<number | null>(null)
  const [currency, setCurrency] = useState('USD')
  const [log, setLog] = useState<TradeLogRow[]>(() => loadTradeHistory())
  const [cooldownUntil, setCooldownUntil] = useState(0)
  const [cooldownLeft, setCooldownLeft] = useState(0)
  const clientRef = useRef<DerivTradingClient | null>(null)
  const tradedSignals = useRef(new Set<string>())
  const cooldownUntilRef = useRef(0)
  const settingsRef = useRef({
    stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
  })

  useEffect(() => {
    settingsRef.current = {
      stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
    }
  }, [stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled])

  useEffect(() => {
    const payload: StoredPrefs = {
      appId, token, accountId, stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct,
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch { /* ignore quota */ }
  }, [appId, token, accountId, stake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct])

  useEffect(() => {
    const tick = () => {
      const left = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
      setCooldownLeft(left)
    }
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [])

  useEffect(() => () => {
    clientRef.current?.close()
    clientRef.current = null
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
    setAutoEnabled(false)
    clientRef.current?.close()
    clientRef.current = null
    setStatus('DISCONNECTED')
    setBalance(null)
  }, [])

  const loadAccounts = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const rows = await listDerivAccounts(token.trim(), appId.trim())
      setAccounts(rows)
      const preferred =
        rows.find((a) => a.account_id === accountId) ||
        rows.find((a) => (a.account_type || '').toLowerCase() === 'demo') ||
        rows[0]
      if (preferred) {
        setAccountId(preferred.account_id)
        if (preferred.currency) setCurrency(preferred.currency)
        if (typeof preferred.balance === 'number') setBalance(preferred.balance)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setAccounts([])
    } finally {
      setBusy(false)
    }
  }, [token, appId, accountId])

  const connect = useCallback(async () => {
    if (!appId.trim() || !token.trim() || !accountId.trim()) {
      setError('Enter App ID, API token, and select an account')
      return
    }
    setBusy(true)
    setError(null)
    clientRef.current?.close()
    const client = new DerivTradingClient(token.trim(), appId.trim(), accountId.trim())
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
    } catch (err) {
      client.close()
      clientRef.current = null
      setStatus('DISCONNECTED')
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [appId, token, accountId])

  const onTradeSignal = useCallback(async (sig: AutoTradeSignal) => {
    const s = settingsRef.current
    if (!s.autoEnabled) return
    if (sig.side !== 'RISE' && sig.side !== 'FALL') return
    const confPct = Math.round(100 * sig.confidence)
    const key = `${sig.time}:${sig.side}`
    if (tradedSignals.current.has(key)) return
    if (confPct < s.minConfidencePct) {
      pushLog({
        signal: sig.side,
        barrier: '—',
        ok: false,
        message: `Skipped · confidence ${confPct}% < min ${s.minConfidencePct}%`,
      })
      return
    }
    const waitLeft = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
    if (waitLeft > 0) {
      pushLog({
        signal: sig.side,
        barrier: '—',
        ok: false,
        message: `Skipped · cooldown ${waitLeft}s left`,
      })
      return
    }
    const client = clientRef.current
    if (!client || client.connectionStatus !== 'CONNECTED') {
      pushLog({ signal: sig.side, barrier: '—', ok: false, message: 'Auto trade on but trading socket not connected' })
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
    autoEnabled, setAutoEnabled,
    status, busy, error, balance, currency, log,
    clearTradeHistory,
    loadAccounts, connect, disconnect, onTradeSignal,
  }
}
