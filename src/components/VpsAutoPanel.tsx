import { useCallback, useEffect, useState } from 'react'
import {
  fetchVpsAutoHealth,
  fetchVpsAutoStatus,
  type VpsAutoStatus,
} from '../lib/autoTrade/autoApi'

/** Compact server status — cooldown is shown on Manual bar / header like before. */
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
      <p className="mt-3 text-[11px] text-slate-500">
        Server worker offline — Auto works in this browser tab only until you redeploy{' '}
        <span className="text-slate-300">autotrade</span>.
      </p>
    )
  }

  const on = status?.autoEnabled === true
  const lastBuy = status?.log?.find((r) => r.ok && r.message.includes('Bought'))

  return (
    <p className="mt-3 text-[11px] leading-snug text-slate-500">
      Server Auto:{' '}
      <span className={on ? 'font-semibold text-emerald-400' : 'text-slate-400'}>
        {on ? 'running' : 'stopped'}
      </span>
      {' · '}
      feed {status?.feedHealthy ? 'OK' : 'warming'}
      {' · '}
      trade {status?.tradeStatus ?? '—'}
      {lastBuy ? (
        <>
          {' · '}
          last buy {lastBuy.barrier !== '—' ? lastBuy.barrier : ''} {lastBuy.signal}
        </>
      ) : null}
      {status?.lastError ? (
        <span className="text-amber-300"> · {status.lastError}</span>
      ) : null}
    </p>
  )
}
