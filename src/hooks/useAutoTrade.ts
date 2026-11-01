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
  /** When true, each trade uses the full account balance (floored to cents). */
  useFullStake: boolean
  barrierAbs: number
  duration: number
  durationUnit: 's' | 'm' | 'h' | 'd'
  mode: TouchSideMode
  /** dual = + and − every signal; single = RISE→+ only, FALL→− only */
  barrierPair: 'single' | 'dual'
  cooldownSec: number
  minConfidencePct: number
  /** Restore Auto trade ON after refresh */
  autoEnabled: boolean
  /** Reconnect trade WS after refresh when true */
  wantConnected: boolean
  cooldownUntil: number
  currency: string
}

const MIN_STAKE = 0.35

function floorStake(amount: number): number {
  return Math.floor(amount * 100) / 100
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
  useFullStake: true,
  barrierAbs: 0.05,
  duration: 7,
  durationUnit: 'm',
  mode: 'signal',
  barrierPair: 'single',
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
      // Default ON: use full balance every trade unless user explicitly turned it off.
      useFullStake: p.useFullStake !== false,
      barrierAbs: typeof p.barrierAbs === 'number' && p.barrierAbs > 0 ? p.barrierAbs : 0.05,
      duration: typeof p.duration === 'number' && p.duration > 0 ? p.duration : 7,
      durationUnit: p.durationUnit === 's' || p.durationUnit === 'h' || p.durationUnit === 'd' ? p.durationUnit : 'm',
      // Migrate: old default "both" dual-barrier → signal side only (+ for RISE, − for FALL).
      // Dual barriers only if user explicitly saved barrierPair: 'dual'.
      barrierPair: p.barrierPair === 'dual' ? 'dual' : 'single',
      mode: p.barrierPair === 'dual' ? 'both' : 'signal',
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

function readStoredCooldownUntil(): number {
  try {
    const until = loadPrefs().cooldownUntil
    return typeof until === 'number' && until > Date.now() ? until : 0
  } catch {
    return 0
  }
}

function persistCooldownUntil(until: number) {
  try {
    const cur = loadPrefs()
    savePrefs({ ...cur, cooldownUntil: until })
  } catch { /* ignore */ }
}

export function useAutoTrade(symbol: string) {
  const prefs0 = useRef(loadPrefs()).current
  const [appId, setAppId] = useState(prefs0.appId)
  const [token, setToken] = useState(prefs0.token)
  const [accountId, setAccountId] = useState(prefs0.accountId)
  const [accounts, setAccounts] = useState<DerivAccount[]>([])
  const [stake, setStake] = useState(prefs0.stake)
  const [useFullStake, setUseFullStake] = useState(prefs0.useFullStake)
  const [barrierAbs, setBarrierAbs] = useState(prefs0.barrierAbs)
  const [duration, setDuration] = useState(prefs0.duration)
  const [durationUnit, setDurationUnit] = useState(prefs0.durationUnit)
  const [mode, setMode] = useState<TouchSideMode>(prefs0.mode)
  const [cooldownSec, setCooldownSec] = useState(prefs0.cooldownSec)
  const [minConfidencePct, setMinConfidencePct] = useState(prefs0.minConfidencePct)
  // Keep Auto trade ON across refresh — never default to false if it was saved.
  const [autoEnabled, setAutoEnabled] = useState(prefs0.autoEnabled)
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
  const tradeLockRef = useRef(false)
  const restoreTried = useRef(false)
  const pendingAuto = useRef(prefs0.autoEnabled)
  const readyToPersist = useRef(false)
  const settingsRef = useRef({
    stake, useFullStake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
  })

  useEffect(() => {
    settingsRef.current = {
      stake, useFullStake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled,
    }
  }, [stake, useFullStake, barrierAbs, duration, durationUnit, mode, cooldownSec, minConfidencePct, currency, symbol, autoEnabled])

  useEffect(() => {
    // Skip the first paint so we never overwrite saved Auto trade ON with a false default.
    if (!readyToPersist.current) {
      readyToPersist.current = true
      return
    }
    savePrefs({
      appId,
      token,
      accountId,
      stake,
      useFullStake,
      barrierAbs,
      duration,
      durationUnit,
      mode,
      barrierPair: mode === 'both' ? 'dual' : 'single',
      cooldownSec,
      minConfidencePct,
      autoEnabled,
      wantConnected,
      cooldownUntil,
      currency,
    })
  }, [
    appId, token, accountId, stake, useFullStake, barrierAbs, duration, durationUnit, mode,
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
      // Keep Auto trade ON preference after refresh/reconnect failure — only Disconnect clears it.
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
    if (!prefs0.appId.trim() || !prefs0.token.trim()) return

    let cancelled = false
    const wantAuto = prefs0.autoEnabled
    const wantWs = prefs0.wantConnected
    pendingAuto.current = wantAuto
    if (wantAuto) setAutoEnabled(true)

    void (async () => {
      const rows = await loadAccounts({
        token: prefs0.token,
        appId: prefs0.appId,
        accountId: prefs0.accountId,
      })
      if (cancelled) return
      const id =
        rows.find((r) => r.account_id === prefs0.accountId)?.account_id
        || rows[0]?.account_id
        || prefs0.accountId
      if (!wantWs || !id) return
      await connect({
        token: prefs0.token,
        appId: prefs0.appId,
        accountId: id,
        enableAuto: wantAuto,
      })
    })()

    return () => {
      cancelled = true
      clientRef.current?.close()
      clientRef.current = null
    }
  }, [loadAccounts, connect, prefs0])

  const setAutoEnabledSafe = useCallback((on: boolean) => {
    pendingAuto.current = on
    setAutoEnabled(on)
    // Persist immediately so refresh never loses Auto trade ON.
    try {
      const cur = loadPrefs()
      savePrefs({ ...cur, autoEnabled: on, wantConnected: on ? true : cur.wantConnected })
    } catch { /* ignore */ }
    if (on) setWantConnected(true)
  }, [])

  const executeTrade = useCallback(async (
    side: 'RISE' | 'FALL',
    opts: { source: 'auto' | 'manual'; confPct?: number; dedupeKey?: string },
  ) => {
    const s = settingsRef.current
    if (tradeLockRef.current) {
      if (opts.source === 'manual') {
        pushLog({ signal: side, barrier: '—', ok: false, message: 'Manual skipped · trade already in progress' })
      }
      return
    }

    const client = clientRef.current
    if (!client || client.connectionStatus !== 'CONNECTED') {
      pushLog({
        signal: side,
        barrier: '—',
        ok: false,
        message: opts.source === 'manual'
          ? 'Manual skipped · connect trade WS first (Trade settings)'
          : 'Skipped · trading socket not connected',
      })
      return
    }

    // Cooldown blocks both auto and manual until the wait interval ends.
    const storedUntil = readStoredCooldownUntil()
    if (storedUntil > cooldownUntilRef.current) {
      cooldownUntilRef.current = storedUntil
      setCooldownUntil(storedUntil)
    }
    const waitLeft = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
    if (waitLeft > 0) {
      const cdKey = `cd:${opts.source}:${opts.dedupeKey ?? side}`
      if (!tradedSignals.current.has(cdKey)) {
        tradedSignals.current.add(cdKey)
        pushLog({
          signal: side,
          barrier: '—',
          ok: false,
          message: `${opts.source === 'manual' ? 'Manual' : 'Auto'} skipped · cooldown ${waitLeft}s left${
            opts.confPct != null ? ` · conf ${opts.confPct}%` : ''
          }`,
        })
      }
      return
    }

    if (opts.dedupeKey) tradedSignals.current.add(opts.dedupeKey)
    tradeLockRef.current = true

    // Start wait interval before buy so buttons stay locked for the full interval.
    if (s.cooldownSec > 0) {
      const until = Date.now() + s.cooldownSec * 1000
      cooldownUntilRef.current = until
      setCooldownUntil(until)
      persistCooldownUntil(until)
      pushLog({
        signal: side,
        barrier: '—',
        ok: true,
        message: `Cooldown started · next trade after ${s.cooldownSec}s`,
      })
    }

    setBusy(true)
    try {
      let tradeStake = s.stake
      if (s.useFullStake) {
        const balMsg = await client.request({ balance: 1 })
        const balObj = balMsg.balance as { balance?: number; currency?: string } | undefined
        const bal = typeof balObj?.balance === 'number' ? balObj.balance : null
        if (typeof balObj?.currency === 'string') setCurrency(balObj.currency)
        if (bal == null || !(bal > 0)) throw new Error('Full stake: no account balance')
        tradeStake = floorStake(bal)
        setBalance(bal)
        if (tradeStake < MIN_STAKE) {
          throw new Error(`Full stake: balance ${tradeStake} below minimum ${MIN_STAKE}`)
        }
        setStake(tradeStake)
        pushLog({
          signal: side,
          barrier: '—',
          ok: true,
          message: `Full stake · using ${tradeStake} ${s.currency} (balance ${bal.toFixed(2)})`,
        })
      }

      const results = await placeTouchTrades(client, {
        symbol: s.symbol,
        currency: s.currency,
        stake: tradeStake,
        barrierAbs: s.barrierAbs,
        duration: s.duration,
        durationUnit: s.durationUnit,
        // Manual buttons always map RISE→+ / FALL→− (one contract).
        mode: opts.source === 'manual' ? 'signal' : s.mode,
      }, side)

      const confLabel = opts.confPct != null ? ` · conf ${opts.confPct}%` : ''
      const srcLabel = opts.source === 'manual' ? 'MANUAL · ' : ''
      for (const r of results) {
        pushLog({
          signal: side,
          barrier: r.barrier,
          ok: true,
          message: `${srcLabel}Bought ONETOUCH${confLabel} · stake≈${r.buyPrice ?? tradeStake}`,
          contractId: r.contractId,
        })
      }

      if (!results.length && s.cooldownSec > 0) {
        // No fill — release cooldown so buttons unlock.
        cooldownUntilRef.current = 0
        setCooldownUntil(0)
        persistCooldownUntil(0)
      }
    } catch (err) {
      if (s.cooldownSec > 0) {
        cooldownUntilRef.current = 0
        setCooldownUntil(0)
        persistCooldownUntil(0)
      }
      pushLog({
        signal: side,
        barrier: '—',
        ok: false,
        message: `${opts.source === 'manual' ? 'Manual · ' : ''}${err instanceof Error ? err.message : String(err)}`,
      })
    } finally {
      tradeLockRef.current = false
      setBusy(false)
    }
  }, [pushLog])

  const onTradeSignal = useCallback(async (sig: AutoTradeSignal) => {
    const s = settingsRef.current
    if (!s.autoEnabled) return
    if (sig.side !== 'RISE' && sig.side !== 'FALL') return
    const confPct = Math.round(100 * sig.confidence)
    if (confPct < s.minConfidencePct) return
    const key = `${sig.time}:${sig.side}`
    if (tradedSignals.current.has(key)) return
    await executeTrade(sig.side, { source: 'auto', confPct, dedupeKey: key })
  }, [executeTrade])

  const placeManualTrade = useCallback(async (side: 'RISE' | 'FALL') => {
    await executeTrade(side, { source: 'manual' })
  }, [executeTrade])

  return {
    appId, setAppId,
    token, setToken,
    accountId, setAccountId,
    accounts,
    stake, setStake,
    useFullStake, setUseFullStake,
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
    placeManualTrade,
  }
}
