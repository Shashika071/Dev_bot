import { useCallback, useEffect, useState } from 'react'
import {
  fetchVpsAutoHealth,
  fetchVpsAutoStatus,
  type VpsAutoStatus,
} from '../lib/autoTrade/autoApi'

/**
 * Status only — Auto trade ON/OFF in the form above also starts/stops the VPS worker.
 */
export function VpsAutoPanel() {
  const [status, setStatus] = useState<VpsAutoStatus | null>(null)
  const [reachable, setReachable] = useState(false)

  const refresh = useCallback(async () => {
    const healthy = await fetchVpsAutoHealth()
    setReachable(healthy)
    if (!healthy) {
      setStatus(null)
      return
    }
    setStatus(await fetchVpsAutoStatus())
  }, [])

  useEffect(() => {
    void refresh()
    const t = setInterval(() => { void refresh() }, 4000)
    return () => clearInterval(t)
  }, [refresh])

  if (!reachable) {
    return (
      <div className="mt-4 rounded border border-slate-800 bg-[#0b0f14] px-3 py-3 text-[11px] text-slate-500">
        <p className="font-semibold text-slate-300">Server Auto status</p>
        <p className="mt-1">
          VPS worker not reachable — <span className="text-slate-300">Auto trade ON</span> works in this
          browser tab only. Redeploy with the <span className="text-slate-300">autotrade</span> container
          so Auto continues after you close the browser.
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
        <p className="font-semibold text-sky-300">Server Auto status</p>
        <span className={`text-xs font-semibold ${on ? 'text-emerald-400' : 'text-slate-500'}`}>
          {on ? 'running' : 'stopped'}
        </span>
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
        Controlled by the same <span className="text-slate-300">Auto trade ON</span> checkbox above.
        When that is ON and the worker is up, you can close the browser — server keeps trading.
      </p>
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
