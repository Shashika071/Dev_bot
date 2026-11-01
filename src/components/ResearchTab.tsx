import { Fragment, useEffect, useState } from 'react'
import {
  fetchResearchHealth,
  fetchResearchStatus,
  postResearchControl,
  researchExportUrl,
} from '../lib/research/researchApi'

function pct(n: number | null | undefined) {
  return n == null ? '—' : `${(100 * n).toFixed(1)}%`
}

function ago(epoch: number | null | undefined) {
  if (epoch == null) return '—'
  return new Date(epoch * 1000).toISOString().replace('.000Z', 'Z')
}

export function ResearchTab() {
  const [live, setLive] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchResearchStatus>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [generatedAt, setGeneratedAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    const ctrl = new AbortController()
    async function poll() {
      try {
        const health = await fetchResearchHealth(ctrl.signal)
        if (cancelled) return
        setLive(!!health.live)
        setGeneratedAt(health.generatedAt)
        const status = await fetchResearchStatus(ctrl.signal)
        if (cancelled) return
        setData(status)
        setError(null)
      } catch {
        if (cancelled) return
        setLive(false)
        setError('Collector offline. Start: npm run research:collector')
      }
    }
    void poll()
    const t = window.setInterval(() => { void poll() }, 3000)
    return () => {
      cancelled = true
      ctrl.abort()
      window.clearInterval(t)
    }
  }, [])

  async function run(path: string, body?: unknown) {
    setBusy(true)
    try {
      await postResearchControl(path, body)
      const status = await fetchResearchStatus()
      setData(status)
      setLive(true)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Control failed')
    } finally {
      setBusy(false)
    }
  }

  const st = data?.state
  const summary = data?.summary
  const strategies = summary?.strategies ?? []

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 text-xs text-slate-300">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Research</h1>
          <p className="mt-1 max-w-2xl text-[12px] text-slate-400">
            Paper-only 22-strategy experiment on public R_100 data. Does not trade, does not use your token,
            and does not change Chart/Trade settings.
          </p>
        </div>
        <div className="text-right text-[11px]">
          <div className={live ? 'text-emerald-400' : 'text-amber-300'}>
            {live ? '● Collector live' : '● Collector offline'}
          </div>
          <div className="text-slate-500">
            Report time {generatedAt ? ago(generatedAt) : '—'}
            {!live && data ? ' · showing last loaded snapshot if any' : ''}
          </div>
        </div>
      </div>

      {error && <p role="alert" className="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-2 text-amber-200">{error}</p>}

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="font-medium text-slate-200">Experiment status</div>
        <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <div>Stage: <span className="text-slate-100">{st?.stage ?? '—'}</span></div>
          <div>Mode: <span className="text-slate-100">{st?.mode ?? '—'}</span></div>
          <div>Experiment: <span className="font-mono text-slate-100">{data?.experimentId ?? '—'}</span></div>
          <div>Start: {ago(st?.startedAt)}</div>
          <div>Ends: {ago(st?.endsAt)}</div>
          <div>Finished: {ago(st?.finishedAt)}</div>
          <div>Feed: {st?.feedHealthy ? 'healthy' : 'unhealthy/offline'}</div>
          <div>Last tick: {ago(st?.lastTickEpoch)}</div>
          <div>Downtime: {st?.downtimeSec != null ? `${Math.round(st.downtimeSec)}s` : '—'}</div>
          <div>Observations: {st?.observationCount ?? 0}</div>
          <div>Rejected: {st?.rejectedCount ?? 0}</div>
          <div>Registry: {data?.registryCount ?? 0}/22</div>
        </div>
        {st?.contract && (
          <p className="mt-2 text-[11px] text-slate-500">
            Frozen paper contract: {st.contract.contractType} barrier±{st.contract.barrierAbs}{' '}
            {st.contract.duration}{st.contract.durationUnit} stake={st.contract.paperStake} {st.contract.currency}
            {' · '}max quote age {st.contract.maxQuoteAgeSec}s
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !live}
            onClick={() => void run('/control/start-discovery')}
            className="rounded bg-emerald-800 px-2 py-1 text-emerald-100 disabled:opacity-40"
          >
            Start 72h discovery
          </button>
          <button
            type="button"
            disabled={busy || !live}
            onClick={() => void run('/control/resume')}
            className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Resume
          </button>
          <button
            type="button"
            disabled={busy || !live}
            onClick={() => void run('/control/stop-entries')}
            className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Stop new entries
          </button>
          <button
            type="button"
            disabled={busy || !live}
            onClick={() => void run('/control/start-confirmation', {
              strategyIds: st?.enabledStrategyIds,
            })}
            className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40"
          >
            Start confirmation (same IDs)
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Keep the collector running in a terminal for the full 72 hours. Restarting the browser does not stop it.
          Restarting the collector resumes from disk without resetting the deadline.
        </p>
      </section>

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-medium text-slate-200">Comparison (descriptive)</div>
          <div className="flex gap-2">
            <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('csv')}>CSV</a>
            <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('report')}>Report MD</a>
            <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('summary')}>Summary JSON</a>
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          7m is the primary direction horizon. Compare completed strategies to B1; B0 timing differs (forming).
          Quote P&L is UNAVAILABLE unless public proposals succeeded — never a broker fill.
        </p>
        <div className="mt-2 overflow-auto">
          <table className="w-full min-w-[900px] text-left text-[11px]">
            <thead className="text-slate-500">
              <tr>
                <th className="px-2 py-1">ID</th>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">n</th>
                <th className="px-2 py-1">7m cont%</th>
                <th className="px-2 py-1">vs random</th>
                <th className="px-2 py-1">avg move / ATR</th>
                <th className="px-2 py-1">Touch H/M/U</th>
                <th className="px-2 py-1">Quote P&L</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s: {
                id: string
                name: string
                family: string
                rules: string
                candidatesAccepted: number
                horizons: Array<{
                  minutes: number
                  contRate: number | null
                  randomContRate: number | null
                  avgSignedMove: number | null
                  avgSignedMoveInAtr: number | null
                  insufficient: boolean
                }>
                touchHits: number
                touchMisses: number
                touchUnknown: number
                pnlUnavailable: boolean
                paperPnl: number | null
              }) => {
                const h7 = s.horizons.find((h) => h.minutes === 7)
                return (
                  <Fragment key={s.id}>
                    <tr
                      className="cursor-pointer border-t border-slate-900/80 hover:bg-slate-900/50"
                      onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                    >
                      <td className="px-2 py-1 font-mono text-slate-200">{s.id}</td>
                      <td className="px-2 py-1">{s.name}</td>
                      <td className="px-2 py-1 tabular-nums">{s.candidatesAccepted}</td>
                      <td className="px-2 py-1 tabular-nums">
                        {h7?.insufficient ? `${pct(h7.contRate)}*` : pct(h7?.contRate)}
                      </td>
                      <td className="px-2 py-1 tabular-nums">{pct(h7?.randomContRate)}</td>
                      <td className="px-2 py-1 tabular-nums">
                        {h7?.avgSignedMove == null ? '—' : h7.avgSignedMove.toFixed(4)}
                        {' / '}
                        {h7?.avgSignedMoveInAtr == null ? '—' : h7.avgSignedMoveInAtr.toFixed(3)}
                      </td>
                      <td className="px-2 py-1 tabular-nums">
                        {s.touchHits}/{s.touchMisses}/{s.touchUnknown}
                      </td>
                      <td className="px-2 py-1">
                        {s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')}
                      </td>
                    </tr>
                    {expanded === s.id && (
                      <tr className="border-t border-slate-900/50 bg-[#121820]">
                        <td colSpan={8} className="px-3 py-2 text-[11px] text-slate-400">
                          <div className="text-slate-300">Family: {s.family}</div>
                          <div className="mt-1 whitespace-pre-wrap">{s.rules}</div>
                          <div className="mt-2 flex flex-wrap gap-3">
                            {s.horizons.map((h) => (
                              <span key={h.minutes}>
                                {h.minutes}m cont {pct(h.contRate)}
                                {h.insufficient ? ' (small n)' : ''}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[10px] text-slate-500">* small sample — treat as early look only. Rankings are exploratory, not statistical proof.</p>
      </section>

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3 text-[11px] text-slate-500">
        <div className="font-medium text-slate-300">How to run 72h</div>
        <ol className="mt-1 list-decimal space-y-1 pl-4">
          <li>In a terminal: <code className="text-slate-300">npm run research:collector</code></li>
          <li>Open this Research tab and click <strong className="text-slate-300">Start 72h discovery</strong>.</li>
          <li>Leave the collector process running (laptop awake / server). Browser can close.</li>
          <li>Optional: <code className="text-slate-300">npm run research:report</code> writes files under research-output/.</li>
          <li>Replay: <code className="text-slate-300">npm run research:replay</code></li>
        </ol>
      </section>
    </div>
  )
}
