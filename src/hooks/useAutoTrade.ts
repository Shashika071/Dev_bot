import { useCallback, useEffect, useRef, useState } from 'react'
import { listDerivAccounts, type DerivAccount } from '../lib/deriv/tradingAuth'
import { DerivTradingClient } from '../lib/deriv/tradingClient'
import {
  brokerNumber, isManualSettlement, loadTrackedContracts, reconcileContract, trackingKey,
  type ManualSettlement, type TrackedContract,
} from '../lib/deriv/contractTracking'
export type { ManualSettlement } from '../lib/deriv/contractTracking'
import {
  formatTouchBarrier,
  placeTouchTrades,
  proposeOneTouch,
  type TouchSideMode,
} from '../lib/deriv/touchTrade'
import {
  quoteEconomicsFromProposal,
  QUOTE_MAX_AGE_SEC,
  type QuoteDisplay,
} from '../lib/deriv/quoteEconomics'
import type { ConfirmationFlags, ConnectionStatus, FinalSignal, TrendDir } from '../lib/types'
import { C5_STRATEGY_ID } from '../lib/strategy/c5Strategy'
import {
  authorizeC5AutoTrade,
  c5DedupeKey,
  isC5EntryExpired,
  migrateLegacyAutoPrefs,
} from '../lib/strategy/c5AutoAuth'
import { syncBrowserAutoToVps } from '../lib/autoTrade/autoApi'

/** C5 migration storage — legacy v1 Auto ON is never restored. */
const STORAGE_KEY = 'v100_auto_trade_v2_c5'
const LEGACY_STORAGE_KEY = 'v100_auto_trade_v1'
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
  /** Candle open time in epoch seconds, matching the chart marker. */
  candleTime?: number
  buyPrice?: number | null
  payout?: number | null
  entrySpot?: number | null
  profit?: number | null
  settled?: boolean
  source?: 'auto' | 'manual'
  accountId?: string
  attemptId?: string
  absoluteBarrier?: number | null
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
  const header = 'utc,signal,barrier,ok,contract_id,buy_price,payout,entry_spot,profit,settled,source,message,signal_candle_utc,absolute_barrier,account_id,attempt_id'
  const lines = rows.map((r) => {
    const utc = new Date(r.at).toISOString()
    const msg = `"${r.message.replaceAll('"', '""')}"`
    const candleUtc = r.candleTime != null ? new Date(r.candleTime * 1000).toISOString() : ''
    return [
      utc, r.signal, r.barrier, r.ok ? '1' : '0', r.contractId ?? '',
      r.buyPrice ?? '', r.payout ?? '', r.entrySpot ?? '', r.profit ?? '',
      r.settled ? '1' : '0', r.source ?? '', msg, candleUtc, r.absoluteBarrier ?? '', r.accountId ?? '', r.attemptId ?? '',
    ].join(',')
  })
  return [header, ...lines].join('\n')
}

export type ManualFillInfo = {
  side: 'RISE' | 'FALL'
  contractId: number
  accountId: string
  currency: string
  absoluteBarrier: number | null
  barrier: string
  buyPrice: number | null
  payout: number | null
  entrySpot: number | null
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
  strategyId: 'C5'
  side: 'RISE' | 'FALL'
  symbol: string
  /** Signal candle open time (epoch sec) */
  candleTime: number
  /** Alias of candleTime for dedupe */
  time: number
  candleCloseEpoch: number
  expiresAt: number
  receivedTickEpoch?: number
  isQualified: boolean
  requiredPassed: number
  requiredTotal: number
  flags: ConfirmationFlags
  /** Display-only fraction requiredPassed/requiredTotal — never authorizes alone */
  confidence: number
  /** Market feed READY at candle_close; false → log skip, never buy */
  feedReady?: boolean
  indicators: {
    close: number
    smaRising: boolean
    smaFalling: boolean
    ao: number | null
    rsi: number | null
    stDir: TrendDir
    rawUt: 'BUY' | 'SELL' | 'NONE'
  }
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
  /** Unused for C5 authorization (kept for prefs UI compatibility). */
  minConfidencePct: 100,
  autoEnabled: false,
  wantConnected: false,
  cooldownUntil: 0,
  currency: 'USD',
}

function loadPrefs(): StoredPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      // Migrate credentials from v1 but NEVER restore Auto ON after C5 cutover.
      const legacyRaw = localStorage.getItem(LEGACY_STORAGE_KEY)
      if (legacyRaw) {
        const p = JSON.parse(legacyRaw) as Partial<StoredPrefs>
        const migrated = migrateLegacyAutoPrefs(p, {
          ...DEFAULT_PREFS,
          appId: typeof p.appId === 'string' ? p.appId : '',
          token: typeof p.token === 'string' ? p.token : '',
          accountId: typeof p.accountId === 'string' ? p.accountId : '',
          stake: typeof p.stake === 'number' && p.stake > 0 ? p.stake : 1,
          useFullStake: p.useFullStake !== false,
          barrierAbs: typeof p.barrierAbs === 'number' && p.barrierAbs > 0 ? p.barrierAbs : 0.05,
          duration: typeof p.duration === 'number' && p.duration > 0 ? p.duration : 7,
          durationUnit: p.durationUnit === 's' || p.durationUnit === 'h' || p.durationUnit === 'd' ? p.durationUnit : 'm',
          mode: 'signal' as const,
          barrierPair: 'single' as const,
          cooldownSec: typeof p.cooldownSec === 'number' && p.cooldownSec >= 0 ? p.cooldownSec : 420,
          minConfidencePct: 100,
          cooldownUntil: 0,
          currency: typeof p.currency === 'string' && p.currency ? p.currency : 'USD',
        })
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated)) } catch { /* ignore */ }
        return migrated
      }
      return { ...DEFAULT_PREFS }
    }
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
  const [liveQuote, setLiveQuote] = useState<QuoteDisplay>({
    available: false,
    reason: 'Quote unavailable',
  })
  const [quoteSide, setQuoteSideState] = useState<'RISE' | 'FALL' | null>(null)
  const quoteSideRef = useRef<'RISE' | 'FALL' | null>(null)
  const [contracts] = useState(() => {
    try { return { rows: loadTrackedContracts(), error: null as string | null } }
    catch { return { rows: new Map<string, TrackedContract>(), error: 'Unable to read saved contract tracking.' } }
  })
  const onSettlementRef = useRef<((row: ManualSettlement) => void) | null>(null)
  const sessionRef = useRef<{ client: DerivTradingClient; accountId: string; subscriptions: Set<number> } | null>(null)
  const [cooldownUntil, setCooldownUntil] = useState(prefs0.cooldownUntil)
  const [cooldownLeft, setCooldownLeft] = useState(0)
  const clientRef = useRef<DerivTradingClient | null>(null)
  const tradedSignals = useRef(new Set<string>())
  const cooldownUntilRef = useRef(prefs0.cooldownUntil)
  const tradeLockRef = useRef(false)
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

  const storeContract = useCallback((row: TrackedContract) => {
    const key = trackingKey(row.accountId, row.contractId)
    contracts.rows.set(key, row)
    try { localStorage.setItem(key, JSON.stringify(row)) }
    catch { setError('Could not save the contract for recovery after reload.') }
  }, [contracts])

  const forgetContract = useCallback((account: string, contractId: number) => {
    const key = trackingKey(account, contractId)
    contracts.rows.delete(key)
    try { localStorage.removeItem(key) }
    catch { setError('Could not clear the saved settlement receipt.') }
  }, [contracts])

  const acknowledgeSettlement = useCallback((account: string, contractId: number) => {
    const row = contracts.rows.get(trackingKey(account, contractId))
    if (row && isManualSettlement(row) && row.profit != null) forgetContract(account, contractId)
  }, [contracts, forgetContract])

  const setOnSettlement = useCallback((listener: ((row: ManualSettlement) => void) | null) => {
    onSettlementRef.current = listener
    if (!listener) return
    for (const record of contracts.rows.values()) {
      if (isManualSettlement(record)) listener(record)
    }
  }, [contracts])

  const subscribeContracts = useCallback(() => {
    const session = sessionRef.current
    if (!session || session.client.connectionStatus !== 'CONNECTED') return
    for (const row of contracts.rows.values()) {
      if (row.accountId !== session.accountId || (row.settled && row.profit != null)
        || session.subscriptions.has(row.contractId)) continue
      session.subscriptions.add(row.contractId)
      void session.client.request({
        proposal_open_contract: 1, contract_id: row.contractId, subscribe: 1,
      }).catch(() => { session.subscriptions.delete(row.contractId) })
    }
  }, [contracts])

  useEffect(() => {
    if (contracts.error) setError(contracts.error)
    const retry = setInterval(subscribeContracts, 15000)
    return () => clearInterval(retry)
  }, [contracts, subscribeContracts])

  const disconnect = useCallback(() => {
    pendingAuto.current = false
    setAutoEnabled(false)
    setWantConnected(false)
    clientRef.current?.close()
    clientRef.current = null
    sessionRef.current = null
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
    const session = { client, accountId: id, subscriptions: new Set<number>() }
    sessionRef.current = session
    clientRef.current = client
    client.setStatusHandler((nextStatus) => {
      if (sessionRef.current !== session) return
      setStatus(nextStatus)
      if (nextStatus === 'CONNECTED') subscribeContracts()
      else {
        session.subscriptions.clear()
        setLiveQuote({ available: false, reason: 'Quote unavailable' })
      }
    })
    client.subscribe((msg) => {
      if (sessionRef.current !== session) return
      if (msg.msg_type === 'balance') {
        const b = msg.balance as { balance?: number; currency?: string } | undefined
        if (typeof b?.balance === 'number') setBalance(b.balance)
        if (typeof b?.currency === 'string') setCurrency(b.currency)
      }
      if (msg.msg_type === 'proposal_open_contract') {
        const detail = msg.proposal_open_contract as Record<string, unknown> | undefined
        const contractId = brokerNumber(detail?.contract_id)
        if (!detail || contractId == null) return
        const previous = contracts.rows.get(trackingKey(id, contractId))
        if (!previous) return
        const updated = reconcileContract(previous, detail)
        if (updated === previous) return
        storeContract(updated)
        setLog((prev) => {
          const next = prev.map((row) =>
            row.contractId === contractId && row.accountId === id
              ? { ...row, entrySpot: updated.entrySpot, absoluteBarrier: updated.absoluteBarrier,
                  buyPrice: updated.buyPrice, payout: updated.payout, profit: updated.profit, settled: updated.settled }
              : row,
          )
          saveTradeHistory(next)
          return next
        })
        if (updated.settled) {
          session.subscriptions.delete(contractId)
          if (isManualSettlement(updated)) onSettlementRef.current?.(updated)
          else if (updated.profit != null) forgetContract(id, contractId)
        }
      }
    })
    try {
      await client.connect()
      if (sessionRef.current !== session) return false
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
      if (sessionRef.current !== session) return false
      sessionRef.current = null
      clientRef.current = null
      setStatus('DISCONNECTED')
      // Keep Auto trade ON preference after refresh/reconnect failure — only Disconnect clears it.
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      setBusy(false)
    }
  }, [appId, token, accountId, contracts, forgetContract, storeContract, subscribeContracts])

  // Restore saved credentials + reconnect after refresh/close.
  const initialConnection = useRef({ loadAccounts, connect })
  useEffect(() => {
    if (!prefs0.appId.trim() || !prefs0.token.trim()) return

    let cancelled = false
    const wantAuto = prefs0.autoEnabled
    const wantWs = prefs0.wantConnected
    pendingAuto.current = wantAuto
    if (wantAuto) setAutoEnabled(true)

    void (async () => {
      const rows = await initialConnection.current.loadAccounts({
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
      await initialConnection.current.connect({
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
      sessionRef.current = null
    }
  }, [prefs0])

  /**
   * Once the VPS worker successfully takes ownership, keep it until Auto OFF.
   * Never clear on a failed status poll — that caused browser+server double buys.
   */
  const VPS_OWNS_KEY = 'v100_vps_owns_auto_v1'
  const vpsOwnsAutoRef = useRef((() => {
    try { return localStorage.getItem(VPS_OWNS_KEY) === '1' } catch { return false }
  })())

  const setVpsOwnsAuto = useCallback((owns: boolean) => {
    vpsOwnsAutoRef.current = owns
    try { localStorage.setItem(VPS_OWNS_KEY, owns ? '1' : '0') } catch { /* ignore */ }
  }, [])

  const syncVpsFromBrowser = useCallback(async (on: boolean) => {
    const s = settingsRef.current
    // Block browser buys during ON handoff so a signal mid-sync cannot double with VPS.
    const alreadyOwned = vpsOwnsAutoRef.current
    if (on) setVpsOwnsAuto(true)
    try {
      const result = await syncBrowserAutoToVps(on, {
        appId,
        token,
        accountId,
        stake: s.stake,
        useFullStake: s.useFullStake,
        barrierAbs: s.barrierAbs,
        duration: s.duration,
        durationUnit: s.durationUnit,
        cooldownSec: s.cooldownSec,
        cooldownUntil: Math.max(cooldownUntilRef.current, Date.now()),
        currency: s.currency,
      })
      if (on && result) {
        setVpsOwnsAuto(true)
        pushLog({
          signal: 'NO_TRADE',
          barrier: '—',
          ok: true,
          message: 'Auto ON · VPS worker owns trades (browser may close; no browser double-buy)',
        })
      } else if (!on) {
        setVpsOwnsAuto(false)
        pushLog({
          signal: 'NO_TRADE',
          barrier: '—',
          ok: !!result,
          message: result
            ? 'Auto OFF · VPS worker stopped'
            : 'Auto OFF · VPS unreachable or unauthorized (confirm worker stopped)',
        })
      } else if (on && !result) {
        if (alreadyOwned) {
          // Keep ownership — never fall back to browser buys while VPS may still be live.
          pushLog({
            signal: 'NO_TRADE',
            barrier: '—',
            ok: false,
            message: 'Auto ON · VPS sync failed · keeping VPS ownership (browser will not buy)',
          })
        } else {
          // Never reached the worker — safe for browser-only Auto.
          setVpsOwnsAuto(false)
          pushLog({
            signal: 'NO_TRADE',
            barrier: '—',
            ok: true,
            message: 'Auto ON · browser only (VPS worker not reachable / auth missing)',
          })
        }
      }
    } catch (err) {
      if (on && !alreadyOwned) setVpsOwnsAuto(false)
      if (!on) setVpsOwnsAuto(false)
      pushLog({
        signal: 'NO_TRADE',
        barrier: '—',
        ok: false,
        message: `VPS sync failed · ${err instanceof Error ? err.message : String(err)}`,
      })
    }
  }, [appId, token, accountId, pushLog, setVpsOwnsAuto])

  const setAutoEnabledSafe = useCallback((on: boolean) => {
    pendingAuto.current = on
    settingsRef.current.autoEnabled = on
    setAutoEnabled(on)
    // Persist immediately so refresh never loses Auto trade ON.
    try {
      const cur = loadPrefs()
      savePrefs({ ...cur, autoEnabled: on, wantConnected: on ? true : cur.wantConnected })
    } catch { /* ignore */ }
    if (on) setWantConnected(true)
    // Same button also starts/stops the VPS worker — no separate VPS Auto toggle.
    void syncVpsFromBrowser(on)
  }, [syncVpsFromBrowser])

  /** Sync Wait-after-trade lock from VPS so manual buttons also block. */
  const applyRemoteCooldown = useCallback((untilMs: number) => {
    if (!(untilMs > Date.now())) return
    if (untilMs <= cooldownUntilRef.current) return
    cooldownUntilRef.current = untilMs
    setCooldownUntil(untilMs)
    persistCooldownUntil(untilMs)
  }, [])

  // Sync cooldown from VPS. Never clear ownership on a failed poll (avoids double-buy).
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      if (!autoEnabled) return
      if (!vpsOwnsAutoRef.current) return
      try {
        const { fetchVpsAutoStatus } = await import('../lib/autoTrade/autoApi')
        const st = await fetchVpsAutoStatus()
        if (cancelled || !st) return
        // If server says Auto was disabled externally, release ownership.
        if (st.autoEnabled === false) {
          setVpsOwnsAuto(false)
          return
        }
        const left = st.cooldownLeftSec ?? 0
        if (left > 0) applyRemoteCooldown(Date.now() + left * 1000)
      } catch {
        // Transient failure: keep vpsOwnsAutoRef true so browser does not also buy.
      }
    }
    void tick()
    const id = setInterval(() => { void tick() }, 3000)
    return () => { cancelled = true; clearInterval(id) }
  }, [autoEnabled, applyRemoteCooldown, setVpsOwnsAuto])

  const setQuoteSide = useCallback((side: 'RISE' | 'FALL' | null) => {
    quoteSideRef.current = side
    setQuoteSideState(side)
    if (!side) setLiveQuote({ available: false, reason: 'Quote unavailable' })
  }, [])

  // Throttled display quote for the current watch/active side (never buys).
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      const side = quoteSideRef.current
      const client = clientRef.current
      const s = settingsRef.current
      if (!side || !client || client.connectionStatus !== 'CONNECTED') {
        if (!cancelled) setLiveQuote({ available: false, reason: 'Quote unavailable' })
        return
      }
      try {
        const barrier = formatTouchBarrier(s.barrierAbs, side === 'RISE' ? 1 : -1)
        let tradeStake = s.stake
        if (s.useFullStake && typeof balance === 'number' && balance > 0) {
          tradeStake = Math.max(0.35, Math.floor(balance * 100) / 100)
        }
        const quoted = await proposeOneTouch(client, {
          symbol: s.symbol,
          currency: s.currency,
          stake: tradeStake,
          barrierAbs: s.barrierAbs,
          duration: s.duration,
          durationUnit: s.durationUnit,
          mode: 'signal',
        }, barrier)
        if (cancelled || quoteSideRef.current !== side) return
        setLiveQuote(quoteEconomicsFromProposal({
          ask: quoted.ask,
          payout: quoted.payout,
          barrier: quoted.barrier,
          responseEpoch: quoted.responseEpoch,
          nowSec: Date.now() / 1000,
        }))
      } catch {
        if (!cancelled) setLiveQuote({ available: false, reason: 'Quote unavailable' })
      }
    }
    void tick()
    const id = window.setInterval(() => { void tick() }, 2500)
    return () => { cancelled = true; clearInterval(id) }
  }, [quoteSide, status, barrierAbs, duration, durationUnit, stake, useFullStake, currency, balance, symbol])

  // Age out stale quotes on the 1s clock used by App.
  useEffect(() => {
    const id = window.setInterval(() => {
      setLiveQuote((prev) => {
        if (!prev.available) return prev
        if (Date.now() / 1000 - prev.responseEpoch > QUOTE_MAX_AGE_SEC) {
          return { available: false, reason: 'Quote unavailable' }
        }
        return prev
      })
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const executeTrade = useCallback(async (
    side: 'RISE' | 'FALL',
    opts: {
      source: 'auto' | 'manual'
      confPct?: number
      dedupeKey?: string
      expiresAt?: number
      strategyId?: 'C5'
      candleTime?: number
      attemptId?: string
    },
  ): Promise<ManualFillInfo[]> => {
    const s = settingsRef.current
    const logTrade = (row: Omit<TradeLogRow, 'id' | 'at'>) => pushLog({
      ...row,
      candleTime: opts.candleTime,
      source: row.source ?? opts.source,
      accountId: row.accountId ?? sessionRef.current?.accountId,
      attemptId: row.attemptId ?? opts.attemptId,
    })
    if (tradeLockRef.current) {
      logTrade({
        signal: side, barrier: '—', ok: false,
        message: `${opts.source === 'manual' ? 'Manual' : 'C5'} skipped · trade already in progress`,
      })
      return []
    }

    const client = clientRef.current
    if (!client || client.connectionStatus !== 'CONNECTED') {
      logTrade({
        signal: side,
        barrier: '—',
        ok: false,
        message: opts.source === 'manual'
          ? 'Manual skipped · connect trade WS first (Trade settings)'
          : 'Skipped · trading socket not connected',
      })
      return []
    }

    if (opts.source === 'auto' && opts.expiresAt != null && isC5EntryExpired(opts.expiresAt, Date.now() / 1000)) {
      logTrade({
        signal: side,
        barrier: '—',
        ok: false,
        message: 'C5 skipped · entry window expired before buy prep',
      })
      return []
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
        logTrade({
          signal: side,
          barrier: '—',
          ok: false,
          message: `${opts.source === 'manual' ? 'Manual' : 'Auto'} skipped · cooldown ${waitLeft}s left${
            opts.confPct != null ? ` · ${opts.confPct}% required` : ''
          }`,
        })
      }
      return []
    }

    if (opts.dedupeKey) tradedSignals.current.add(opts.dedupeKey)
    tradeLockRef.current = true

    // Start wait interval before buy so buttons stay locked for the full interval.
    if (s.cooldownSec > 0) {
      const until = Date.now() + s.cooldownSec * 1000
      cooldownUntilRef.current = until
      setCooldownUntil(until)
      persistCooldownUntil(until)
      logTrade({
        signal: side,
        barrier: '—',
        ok: true,
        message: `Cooldown started · next trade after ${s.cooldownSec}s`,
      })
    }

    setBusy(true)
    try {
      let tradeStake = s.stake
      let tradeCurrency = s.currency
      if (s.useFullStake) {
        const balMsg = await client.request({ balance: 1 })
        if (opts.source === 'auto' && opts.expiresAt != null && isC5EntryExpired(opts.expiresAt, Date.now() / 1000)) {
          logTrade({
            signal: side,
            barrier: '—',
            ok: false,
            message: 'C5 skipped · entry window expired during balance request',
          })
          if (s.cooldownSec > 0) {
            cooldownUntilRef.current = 0
            setCooldownUntil(0)
            persistCooldownUntil(0)
          }
          return []
        }
        if (client.connectionStatus !== 'CONNECTED') {
          throw new Error('C5 skipped · trading socket disconnected during prep')
        }
        const balObj = balMsg.balance as { balance?: number; currency?: string } | undefined
        const bal = typeof balObj?.balance === 'number' ? balObj.balance : null
        if (typeof balObj?.currency === 'string') {
          tradeCurrency = balObj.currency
          setCurrency(balObj.currency)
        }
        if (bal == null || !(bal > 0)) throw new Error('Full stake: no account balance')
        tradeStake = floorStake(bal)
        setBalance(bal)
        if (tradeStake < MIN_STAKE) {
          throw new Error(`Full stake: balance ${tradeStake} below minimum ${MIN_STAKE}`)
        }
        setStake(tradeStake)
        logTrade({
          signal: side,
          barrier: '—',
          ok: true,
          message: `Full stake · using ${tradeStake} ${s.currency} (balance ${bal.toFixed(2)})`,
        })
      }

      // Recheck expiry and connection before starting the proposal (fail-fast).
      if (opts.source === 'auto' && opts.expiresAt != null && isC5EntryExpired(opts.expiresAt, Date.now() / 1000)) {
        logTrade({
          signal: side,
          barrier: '—',
          ok: false,
          message: 'C5 skipped · entry window expired before buy request',
        })
        if (s.cooldownSec > 0) {
          cooldownUntilRef.current = 0
          setCooldownUntil(0)
          persistCooldownUntil(0)
        }
        return []
      }
      if (client.connectionStatus !== 'CONNECTED') {
        throw new Error('C5 skipped · trading socket not connected before buy')
      }

      const results = await placeTouchTrades(client, {
        symbol: s.symbol,
        currency: tradeCurrency,
        stake: tradeStake,
        barrierAbs: s.barrierAbs,
        duration: s.duration,
        durationUnit: s.durationUnit,
        // C5 auto and manual: one contract in signal direction only (never dual).
        mode: 'signal',
      }, side, {
        // Critical: recheck after proposal response — a slow quote must not buy past the window.
        beforeBuy: () => {
          if (opts.source === 'auto' && !settingsRef.current.autoEnabled) {
            throw new Error('C5 skipped · Auto trade OFF before buy')
          }
          if (opts.source === 'auto' && opts.expiresAt != null
            && isC5EntryExpired(opts.expiresAt, Date.now() / 1000)) {
            throw new Error('C5 skipped · entry window expired after proposal')
          }
          if (client.connectionStatus !== 'CONNECTED') {
            throw new Error('C5 skipped · trading socket not connected after proposal')
          }
        },
      })

      const confLabel = opts.source === 'auto' ? ' · C5 4/4' : ''
      const srcLabel = opts.source === 'manual' ? 'MANUAL · ' : 'C5 AUTO · '
      const purchaseAccount = sessionRef.current?.client === client
        ? sessionRef.current.accountId
        : null
      if (!purchaseAccount) throw new Error('Trading account changed before contract tracking')
      for (const r of results) {
        const relativeBarrier = Number(r.barrier)
        const absoluteBarrier = r.entrySpot != null && Number.isFinite(relativeBarrier)
          ? r.entrySpot + relativeBarrier
          : null
        const tracked: TrackedContract = {
          accountId: purchaseAccount,
          contractId: r.contractId,
          source: opts.source,
          attemptId: opts.source === 'manual' ? opts.attemptId ?? null : null,
          side,
          currency: tradeCurrency,
          barrier: r.barrier,
          absoluteBarrier,
          entrySpot: r.entrySpot,
          buyPrice: r.buyPrice,
          payout: r.payout,
          settled: false,
          status: 'open',
          profit: null,
        }
        // Persist before notifying the UI so a refresh cannot orphan the buy.
        storeContract(tracked)
        const activeSession = sessionRef.current
        if (activeSession?.client === client) {
          activeSession.subscriptions.add(r.contractId)
          void client.request({
            proposal_open_contract: 1,
            contract_id: r.contractId,
            subscribe: 1,
          }).catch(() => { activeSession.subscriptions.delete(r.contractId) })
        }
        logTrade({
          signal: side,
          barrier: r.barrier,
          ok: true,
          message: `${srcLabel}Bought ONETOUCH${confLabel} · stake≈${r.buyPrice ?? tradeStake}`,
          contractId: r.contractId,
          buyPrice: r.buyPrice,
          payout: r.payout,
          entrySpot: r.entrySpot,
          source: opts.source,
          accountId: purchaseAccount,
          attemptId: opts.attemptId,
          absoluteBarrier,
        })
      }

      if (!results.length && s.cooldownSec > 0) {
        // No fill — release cooldown so buttons unlock.
        cooldownUntilRef.current = 0
        setCooldownUntil(0)
        persistCooldownUntil(0)
      }
      return results.map((r) => ({
        side,
        contractId: r.contractId,
        accountId: purchaseAccount,
        currency: tradeCurrency,
        absoluteBarrier: r.entrySpot != null && Number.isFinite(Number(r.barrier))
          ? r.entrySpot + Number(r.barrier)
          : null,
        barrier: r.barrier,
        buyPrice: r.buyPrice,
        payout: r.payout,
        entrySpot: r.entrySpot,
      }))
    } catch (err) {
      if (s.cooldownSec > 0) {
        cooldownUntilRef.current = 0
        setCooldownUntil(0)
        persistCooldownUntil(0)
      }
      {
        const raw = err instanceof Error ? err.message : String(err)
        const prefixed = raw.startsWith('C5 skipped') || raw.startsWith('Manual')
          ? raw
          : `${opts.source === 'manual' ? 'Manual · ' : 'C5 · '}${raw}`
        logTrade({
          signal: side,
          barrier: '—',
          ok: false,
          message: prefixed,
        })
      }
      return []
    } finally {
      tradeLockRef.current = false
      setBusy(false)
    }
  }, [pushLog, storeContract])

  const onTradeSignal = useCallback(async (sig: AutoTradeSignal) => {
    const s = settingsRef.current
    const side = sig.side === 'RISE' || sig.side === 'FALL' ? sig.side : 'NO_TRADE'
    const key = c5DedupeKey(sig.candleTime, sig.side)
    const logSignal = (row: Omit<TradeLogRow, 'id' | 'at'>) => pushLog({
      ...row, candleTime: sig.candleTime,
    })
    logSignal({
      signal: side, barrier: '—', ok: true,
      message: `C5 signal received · ${sig.requiredPassed}/${sig.requiredTotal} required`,
    })

    // When VPS worker is handling Auto, skip browser buys (avoids double orders).
    if (s.autoEnabled && vpsOwnsAutoRef.current) {
      const barrier = side === 'RISE' ? `+${s.barrierAbs}` : side === 'FALL' ? `-${s.barrierAbs}` : '—'
      logSignal({
        signal: side,
        barrier,
        ok: true,
        message: `C5 · VPS worker handles buy · barrier ${barrier} (browser optional)`,
      })
      return
    }

    // Chart/Research can show the same C5 bar later; live Auto only acts on READY closes.
    if (sig.feedReady === false) {
      if (!tradedSignals.current.has(`feed:${key}`)) {
        tradedSignals.current.add(`feed:${key}`)
        logSignal({
          signal: side,
          barrier: '—',
          ok: false,
          message: 'C5 skipped · market feed not READY at candle close (chart may still show the arrow)',
        })
      }
      return
    }

    const auth = authorizeC5AutoTrade(sig, {
      autoEnabled: s.autoEnabled,
      nowSec: Date.now() / 1000,
      alreadyTraded: tradedSignals.current.has(key),
      // Saved threshold must never weaken the four mandatory C5 requirements.
      minConfidencePct: s.minConfidencePct,
    })
    if (!auth.ok) {
      // Always log live C5 misses — silent skips made Research/chart signals look like Auto failures.
      const reason =
        auth.reason === 'auto_disabled' ? 'Auto trade OFF (Research/chart markers are not real orders)'
          : auth.reason === 'expired' ? 'signal expired before auto handler'
            : auth.reason === 'duplicate' ? 'duplicate signal (already handled)'
              : auth.reason === 'wrong_strategy' ? 'wrong strategy id'
                : auth.reason === 'not_qualified' || auth.reason === 'required_count' || auth.reason === 'required_flags'
                  ? 'C5 4/4 requirements not met'
                  : auth.reason
      logSignal({
        signal: side,
        barrier: '—',
        ok: false,
        message: `C5 skipped · ${reason}`,
      })
      return
    }
    await executeTrade(sig.side, {
      source: 'auto',
      confPct: 100,
      dedupeKey: auth.dedupeKey,
      expiresAt: sig.expiresAt,
      strategyId: C5_STRATEGY_ID,
      candleTime: sig.candleTime,
    })
  }, [executeTrade, pushLog])

  const placeManualTrade = useCallback(async (
    side: 'RISE' | 'FALL',
    attemptId: string,
  ): Promise<ManualFillInfo[]> => {
    // Re-read stored cooldown (incl. VPS-synced) before manual click.
    const storedUntil = readStoredCooldownUntil()
    if (storedUntil > cooldownUntilRef.current) {
      cooldownUntilRef.current = storedUntil
      setCooldownUntil(storedUntil)
    }
    const waitLeft = Math.max(0, Math.ceil((cooldownUntilRef.current - Date.now()) / 1000))
    if (waitLeft > 0) {
      pushLog({
        signal: side,
        barrier: '—',
        ok: false,
        message: `Manual skipped · cooldown ${waitLeft}s left`,
        source: 'manual',
        attemptId,
        accountId: sessionRef.current?.accountId,
      })
      return []
    }
    return executeTrade(side, { source: 'manual', attemptId })
  }, [executeTrade, pushLog])

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
    applyRemoteCooldown,
    autoEnabled, setAutoEnabled: setAutoEnabledSafe,
    status, busy, error, balance, currency, log,
    clearTradeHistory,
    loadAccounts: () => void loadAccounts(),
    connect: () => void connect(),
    disconnect,
    onTradeSignal,
    placeManualTrade,
    liveQuote,
    setQuoteSide,
    setOnSettlement,
    acknowledgeSettlement,
  }
}
