import { Fragment, useEffect, useState } from 'react'
import type { MarketState } from '../lib/deriv/marketFeed'
import { useBrowserResearch } from '../hooks/useBrowserResearch'
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

type RemotePayload = Awaited<ReturnType<typeof fetchResearchStatus>>

export function ResearchTab({ market }: { market: MarketState | null }) {
  const browser = useBrowserResearch(market)
  const [mode, setMode] = useState<'server' | 'browser'>('browser')
  const [remote, setRemote] = useState<RemotePayload | null>(null)
  const [remoteLive, setRemoteLive] = useState(false)
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
        const status = await fetchResearchStatus(ctrl.signal)
        if (cancelled) return
        setRemoteLive(!!health.live)
        setGeneratedAt(health.generatedAt)
        setRemote(status)
        setMode('server')
      } catch {
        if (cancelled) return
        setRemoteLive(false)
        setMode('browser')
      }
    }
    void poll()
    const t = window.setInterval(() => { void poll() }, 4000)
    return () => {
      cancelled = true
      ctrl.abort()
      window.clearInterval(t)
    }
  }, [])

  async function runRemote(path: string, body?: unknown) {
    setBusy(true)
    try {
      await postResearchControl(path, body)
      const status = await fetchResearchStatus()
      setRemote(status)
      setRemoteLive(true)
      setMode('server')
    } finally {
      setBusy(false)
    }
  }

  const usingServer = mode === 'server' && remoteLive
  const st = usingServer ? remote?.state : browser.state
  const strategies = usingServer
    ? (remote?.summary?.strategies ?? [])
    : browser.summary.strategies
  const registryCount = usingServer
    ? (remote?.registryCount ?? 0)
    : browser.registry.length

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 text-xs text-slate-300">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Research</h1>
          <p className="mt-1 max-w-2xl text-[12px] text-slate-400">
            Paper-only 22-strategy experiment. Does not trade and does not use your token.
          </p>
        </div>
        <div className="text-right text-[11px]">
          {usingServer ? (
            <>
              <div className="text-emerald-400">● Server collector (keeps running if you close tab)</div>
              <div className="text-slate-500">Report {generatedAt ? ago(generatedAt) : '—'}</div>
            </>
          ) : (
            <>
              <div className={browser.live ? 'text-amber-300' : 'text-rose-400'}>
                ● Browser mode {browser.live ? '(stops if you close tab)' : '(waiting for READY)'}
              </div>
              <div className="text-slate-500">Deploy research service for always-on collection</div>
            </>
          )}
        </div>
      </div>

      {!usingServer && (
        <p className="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-2 text-amber-200">
          Server collector not reachable yet — using browser mode for now.
          After you redeploy with the <code className="text-amber-100">research</code> container,
          this becomes always-on and closing the tab is OK.
        </p>
      )}

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="font-medium text-slate-200">Experiment status</div>
        <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <div>Stage: <span className="text-slate-100">{st?.stage ?? '—'}</span></div>
          <div>Mode: <span className="text-slate-100">{(st as { mode?: string } | undefined)?.mode ?? '—'}</span></div>
          <div>Registry: <span className="text-slate-100">{registryCount}/22</span></div>
          <div>Start: {ago((st as { startedAt?: number | null } | undefined)?.startedAt)}</div>
          <div>Ends: {ago((st as { endsAt?: number | null } | undefined)?.endsAt)}</div>
          <div>Finished: {ago((st as { finishedAt?: number | null } | undefined)?.finishedAt)}</div>
          <div>
            Feed:{' '}
            {(st as { feedHealthy?: boolean } | undefined)?.feedHealthy ? 'healthy' : 'unhealthy/offline'}
          </div>
          <div>Last tick: {ago((st as { lastTickEpoch?: number | null } | undefined)?.lastTickEpoch)}</div>
          <div>
            Observations:{' '}
            {usingServer
              ? (remote?.state?.observationCount ?? 0)
              : browser.state.observations.length}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {usingServer ? (
            <>
              <button type="button" disabled={busy} onClick={() => void runRemote('/control/start-discovery')} className="rounded bg-emerald-800 px-2 py-1 text-emerald-100 disabled:opacity-40">Start 72h discovery</button>
              <button type="button" disabled={busy} onClick={() => void runRemote('/control/resume')} className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40">Resume</button>
              <button type="button" disabled={busy} onClick={() => void runRemote('/control/stop-entries')} className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40">Stop new entries</button>
              <button type="button" disabled={busy} onClick={() => void runRemote('/control/start-confirmation', { strategyIds: remote?.state?.enabledStrategyIds })} className="rounded bg-slate-700 px-2 py-1 disabled:opacity-40">Start confirmation</button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => browser.start()} className="rounded bg-emerald-800 px-2 py-1 text-emerald-100">Start 72h discovery</button>
              <button type="button" onClick={() => browser.resume()} className="rounded bg-slate-700 px-2 py-1">Resume</button>
              <button type="button" onClick={() => browser.stopEntries()} className="rounded bg-slate-700 px-2 py-1">Stop new entries</button>
              <button type="button" onClick={() => browser.startConfirmation()} className="rounded bg-slate-700 px-2 py-1">Start confirmation</button>
            </>
          )}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          {usingServer
            ? 'Server collector is running on the VPS. You can close this tab; research continues.'
            : 'Browser fallback: keep this tab open, or redeploy with the research container for always-on.'}
        </p>
      </section>

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-medium text-slate-200">Comparison (descriptive)</div>
          <div className="flex gap-2">
            {usingServer ? (
              <>
                <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('csv')}>CSV</a>
                <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('report')}>Report MD</a>
                <a className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" href={researchExportUrl('summary')}>Summary JSON</a>
              </>
            ) : (
              <>
                <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => browser.downloadCsv()}>CSV</button>
                <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => browser.downloadReport()}>Report MD</button>
                <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => browser.downloadSummary()}>Summary JSON</button>
              </>
            )}
          </div>
        </div>
        <div className="mt-2 overflow-auto">
          <table className="w-full min-w-[900px] text-left text-[11px]">
            <thead className="text-slate-500">
              <tr>
                <th className="px-2 py-1">ID</th>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">n</th>
                <th className="px-2 py-1">7m cont%</th>
                <th className="px-2 py-1">vs random</th>
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
                horizons: Array<{ minutes: number; contRate: number | null; randomContRate: number | null; insufficient: boolean }>
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
                      <td className="px-2 py-1 tabular-nums">{h7?.insufficient ? `${pct(h7.contRate)}*` : pct(h7?.contRate)}</td>
                      <td className="px-2 py-1 tabular-nums">{pct(h7?.randomContRate)}</td>
                      <td className="px-2 py-1 tabular-nums">{s.touchHits}/{s.touchMisses}/{s.touchUnknown}</td>
                      <td className="px-2 py-1">{s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')}</td>
                    </tr>
                    {expanded === s.id && (
                      <tr className="border-t border-slate-900/50 bg-[#121820]">
                        <td colSpan={7} className="px-3 py-2 text-[11px] text-slate-400 whitespace-pre-wrap">{s.rules}</td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
