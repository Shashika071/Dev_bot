import { useCallback, useEffect, useState } from 'react'
import {
  fetchVpsAutoHealth,
  fetchVpsAutoStatus,
  postVpsAutoControl,
  type VpsAutoStatus,
} from '../lib/autoTrade/autoApi'
import type { useAutoTrade } from '../hooks/useAutoTrade'

type AutoTrade = ReturnType<typeof useAutoTrade>

/**
 * Always-on VPS C5 Auto — keeps trading after the browser closes.
 * Turn browser Auto OFF when this is ON to avoid double buys.
 */
export function VpsAutoPanel({ trade }: { trade: AutoTrade }) {
  const [status, setStatus] = useState<VpsAutoStatus | null>(null)
  const [reachable, setReachable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const healthy = await fetchVpsAutoHealth()
    setReachable(healthy)
    if (!healthy) {
      setStatus(null)
      return
    }
    const s = await fetchVpsAutoStatus()
    setStatus(s)
  }, [])

  useEffect(() => {
    void refresh()
    const t = setInterval(() => { void refresh() }, 4000)
    return () => clearInterval(t)
  }, [refresh])

  const pushBrowserCreds = async () => {
    setBusy(true)
    setError(null)
    try {
      const next = await postVpsAutoControl('/control/settings', {
        appId: trade.appId,
        token: trade.token,
        accountId: trade.accountId,
        stake: trade.stake,
        useFullStake: trade.useFullStake,
        barrierAbs: trade.barrierAbs,
        duration: trade.duration,
        durationUnit: trade.durationUnit,
        cooldownSec: trade.cooldownSec,
        currency: trade.currency,
        autoEnabled: false,
      })
      setStatus(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const setEnabled = async (on: boolean) => {
    setBusy(true)
    setError(null)
    try {
      // Ensure latest browser credentials are on the worker before enable.
      if (on) {
        await postVpsAutoControl('/control/settings', {
          appId: trade.appId,
          token: trade.token,
          accountId: trade.accountId,
          stake: trade.stake,
          useFullStake: trade.useFullStake,
          barrierAbs: trade.barrierAbs,
          duration: trade.duration,
          durationUnit: trade.durationUnit,
          cooldownSec: trade.cooldownSec,
          currency: trade.currency,
        })
      }
      const next = await postVpsAutoControl(on ? '/control/enable' : '/control/disable')
      setStatus(next)
      if (on && trade.autoEnabled) {
        trade.setAutoEnabled(false)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
      void refresh()
    }
  }

  if (!reachable) {
    return (
      <div className="mt-4 rounded border border-slate-800 bg-[#0b0f14] px-3 py-3 text-[11px] text-slate-500">
        <p className="font-semibold text-slate-300">VPS Auto (always-on)</p>
        <p className="mt-1">
          Worker not reachable. Redeploy with the <span className="text-slate-300">autotrade</span> container,
          then enable here so trades continue after you close the browser.
        </p>
      </div>
    )
  }

  const on = status?.autoEnabled === true
  const feedOk = status?.feedHealthy === true
  const tradeOk = status?.tradeStatus === 'CONNECTED'

  return (
    <div className="mt-4 rounded border border-sky-900/60 bg-[#0b0f14] px-3 py-3 text-[11px] text-slate-400">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-semibold text-sky-300">VPS Auto (always-on · close browser OK)</p>
        <span className={feedOk ? 'text-emerald-400' : 'text-amber-400'}>
          ● feed {feedOk ? 'OK' : 'warming'}
        </span>
        <span className={tradeOk ? 'text-emerald-400' : 'text-slate-500'}>
          ● trade {status?.tradeStatus ?? '—'}
        </span>
        {(status?.cooldownLeftSec ?? 0) > 0 && (
          <span className="text-amber-300">wait {status?.cooldownLeftSec}s</span>
        )}
      </div>
      <p className="mt-1 leading-snug">
        Same C5 4/4 rules as browser Auto. Runs on the server. Turn this ON and leave browser Auto OFF
        so you do not double-buy.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" className="btn" disabled={busy} onClick={() => void pushBrowserCreds()}>
          Push credentials from form
        </button>
        <button
          type="button"
          className={on ? 'btn-muted' : 'btn'}
          disabled={busy || (!on && !status?.credentialsReady && !(trade.appId && trade.token && trade.accountId))}
          onClick={() => void setEnabled(!on)}
        >
          {on ? 'Disable VPS Auto' : 'Enable VPS Auto'}
        </button>
        <span className={`text-xs font-semibold ${on ? 'text-emerald-400' : 'text-slate-500'}`}>
          {on ? 'VPS Auto ON' : 'VPS Auto OFF'}
        </span>
      </div>
      {error && <p className="mt-2 text-rose-300">{error}</p>}
      {status?.lastError && <p className="mt-1 text-amber-300">Last error: {status.lastError}</p>}
      {status?.lastSignal && (
        <p className="mt-1 text-slate-500">
          Last signal {status.lastSignal.side} · {status.lastSignal.source} ·{' '}
          {new Date(status.lastSignal.at).toISOString().slice(11, 19)} UTC
        </p>
      )}
      {!!status?.log?.length && (
        <ul className="mt-2 max-h-40 space-y-0.5 overflow-auto border-t border-slate-800 pt-2 font-mono text-[10px]">
          {status.log.slice(0, 12).map((row) => (
            <li key={row.id} className={row.ok ? 'text-emerald-400/90' : 'text-slate-500'}>
              {new Date(row.at).toISOString().slice(11, 19)} · {row.signal} · {row.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
