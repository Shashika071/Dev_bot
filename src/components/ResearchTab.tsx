import { Fragment, useEffect, useState } from 'react'
import { RESEARCH_CODE_VERSION } from '../lib/research/experimentEngine'
import type { StrategySummary } from '../lib/research/summarizeExperiment'
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
  const [mode, setMode] = useState<'server' | 'browser'>('browser')
  const browser = useBrowserResearch(market, mode === 'browser')
  const [remote, setRemote] = useState<RemotePayload | null>(null)
  const [remoteLive, setRemoteLive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [generatedAt, setGeneratedAt] = useState<number | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  function note(msg: string) {
    setFlash(msg)
    window.setTimeout(() => setFlash(null), 4000)
  }

  const btn =
    'cursor-pointer rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40'

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
        // Preserve the server snapshot on disconnect; never swap to another experiment.
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

  async function runRemote(path: string, body?: unknown, okMsg?: string) {
    setBusy(true)
    try {
      await postResearchControl(path, body)
      const status = await fetchResearchStatus()
      setRemote(status)
      setRemoteLive(true)
      setMode('server')
      note(okMsg ?? `OK — stage: ${status?.state?.stage ?? 'updated'}`)
    } catch (e) {
      note(e instanceof Error ? e.message : 'Control request failed')
    } finally {
      setBusy(false)
    }
  }

  function confirmReset(): boolean {
    return window.confirm(
      'Reset research? This clears all observations and starts a new 72h discovery from zero. Export first if you need the current run.',
    )
  }

  const usingServer = mode === 'server' && remote != null
  const st = usingServer ? remote?.state : browser.state
  const strategies = usingServer
    ? (remote?.summary?.strategies ?? [])
    : browser.summary.strategies
  const registryCount = usingServer
    ? (remote?.registryCount ?? 0)
    : browser.registry.length
  const summary = usingServer ? remote?.summary : browser.summary
  const legacy = (summary?.codeVersion ?? st?.codeVersion) !== RESEARCH_CODE_VERSION
  const controlsDisabled = busy || (usingServer && !remoteLive)
  const contract = (st as { contract?: { contractType: string; barrierAbs: number; duration: number; durationUnit: string; paperStake: number; currency: string } } | undefined)?.contract

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
              <div className={remoteLive ? "text-emerald-400" : "text-rose-400"}>{remoteLive ? "● Server collector" : "● Server disconnected — last saved snapshot"}</div>
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

      {legacy && (
        <p className="rounded border border-amber-700 bg-amber-950/40 p-3 text-amber-200">
          Pre-fix experiment: preserved read-only. Paired entries, quotes and expiry were affected by known bugs.
          Export this run and start a new discovery with the corrected engine. Legacy P&amp;L is not comparable.
        </p>
      )}
      {browser.storageError && !usingServer && (
        <p role="alert" className="text-rose-300">Browser storage is full or unavailable. Export the in-memory results now; new observations are not being saved.</p>
      )}

      <section className="relative z-10 rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="font-medium text-slate-200">Experiment status</div>
        <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            Stage:{' '}
            <span className="font-semibold text-emerald-300">{st?.stage ?? '—'}</span>
          </div>
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

        {contract && (
          <p className="mt-2 text-[11px] text-slate-500">
            Frozen paper contract: {contract.contractType} barrier±{contract.barrierAbs}{' '}
            {contract.duration}{contract.durationUnit} stake={contract.paperStake} {contract.currency}
          </p>
        )}

        <div className="relative z-10 mt-3 flex flex-wrap gap-2">
          {usingServer ? (
            <>
              <button
                type="button"
                disabled={controlsDisabled}
                onClick={() => void runRemote('/control/start-discovery', undefined, 'Discovery started (server)')}
                className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
              >
                {busy ? 'Working…' : 'Start 72h discovery'}
              </button>
              <button
                type="button"
                disabled={controlsDisabled || legacy}
                onClick={() => void runRemote('/control/resume', undefined, 'Resumed collecting')}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Resume
              </button>
              <button
                type="button"
                disabled={controlsDisabled || legacy}
                onClick={() => void runRemote('/control/stop-entries', undefined, 'Stopped new entries')}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Stop new entries
              </button>
              <button
                type="button"
                disabled={controlsDisabled}
                onClick={() =>
                  void runRemote(
                    '/control/start-confirmation',
                    { strategyIds: remote?.state?.enabledStrategyIds },
                    'Confirmation started',
                  )
                }
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Start confirmation (same IDs)
              </button>
              <button
                type="button"
                disabled={controlsDisabled}
                onClick={() => {
                  if (!confirmReset()) return
                  void runRemote('/control/reset', undefined, 'Reset — all records cleared, discovery restarted from 0')
                }}
                className={`${btn} bg-rose-800 text-rose-100 hover:bg-rose-700`}
              >
                Reset (clear all)
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => {
                  try { const next = browser.start(); note(`Discovery started — stage: ${next.stage}`) }
                  catch { note('Could not archive the existing run. Export it before starting another run.') }
                }}
                className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
              >
                Start 72h discovery
              </button>
              <button
                type="button"
                disabled={legacy}
                onClick={() => {
                  const next = browser.resume()
                  note(`Resumed — stage: ${next.stage}`)
                }}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Resume
              </button>
              <button
                type="button"
                disabled={legacy}
                onClick={() => {
                  const next = browser.stopEntries()
                  note(`Stopped new entries — stage: ${next.stage}`)
                }}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Stop new entries
              </button>
              <button
                type="button"
                onClick={() => {
                  try { const next = browser.startConfirmation(); note(`Confirmation started — stage: ${next.stage}`) }
                  catch { note('Could not archive the existing run. Export it before starting another run.') }
                }}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Start confirmation (same IDs)
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!confirmReset()) return
                  try {
                    const next = browser.resetAll()
                    note(`Reset — observations 0, stage: ${next.stage}`)
                  } catch {
                    note('Could not archive the existing run. Export it before reset.')
                  }
                }}
                className={`${btn} bg-rose-800 text-rose-100 hover:bg-rose-700`}
              >
                Reset (clear all)
              </button>
            </>
          )}
        </div>

        {flash && (
          <p className="mt-2 rounded border border-emerald-700/60 bg-emerald-950/50 px-2 py-1.5 text-[11px] text-emerald-200">
            {flash}
          </p>
        )}

        <p className="mt-2 text-[11px] text-slate-500">
          {usingServer
            ? (remoteLive ? (legacy ? 'Old run is read-only. Start a new v2 experiment to collect corrected results.' : 'Server collector is running. You can close this tab; research continues.') : 'Connection lost. This is a snapshot; live collection status is unknown.')
            : 'For a full 72h on the deployed site: leave this browser open (or reopen and click Resume). Data is stored in this browser only. Auto trade can stay OFF.'}
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
        <p className="mt-2 text-[11px] text-slate-400">
          H/M/U/P = hit / miss / unknown / pending. Reference touches and post-quote touches have different entries.
          P&amp;L and cost use the same settled quoted observations only. Direction percentages exclude unchanged and unavailable endpoints.
          * Fewer than 20 directional outcomes; larger samples are still exploratory, not proof of an advantage.
        </p>
        <div className="mt-2 overflow-auto">
          <table className="w-full min-w-[1400px] text-left text-[11px]">
            <thead className="text-slate-500">
              <tr>
                <th className="px-2 py-1">ID</th>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">n</th>
                <th className="px-2 py-1">7m cont%</th>
                <th className="px-2 py-1">50/50 reference</th>
                <th className="px-2 py-1">Reference H/M/U/P</th>
                <th className="px-2 py-1">Quote P&L</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s: StrategySummary) => {
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
                      <td className="px-2 py-1 tabular-nums">{s.touchHits}/{s.touchMisses}/{s.touchUnknown}/{s.touchPending ?? 0}</td>
                      <td className="px-2 py-1">{s.quotesAvailable}/{s.candidatesAccepted}</td>
                      <td className="px-2 py-1">{legacy ? "—" : `${s.quotedHits}/${s.quotedMisses}/${s.quotedUnknown}/${s.quotedPending}`}</td>
                      <td className="px-2 py-1">{legacy ? "—" : `${s.quotedSettled} / ${s.paperCost?.toFixed(2) ?? "—"}`}</td>
                      <td className="px-2 py-1">{legacy || s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')}</td>
                      <td className="px-2 py-1">{legacy ? '—' : pct(s.paperReturn)}</td>
                    </tr>
                    {expanded === s.id && (
                      <tr className="border-t border-slate-900/50 bg-[#121820]">
                        <td colSpan={11} className="px-3 py-2 text-[11px] text-slate-400 whitespace-pre-wrap">{s.rules}{'\n\n'}
                          7m: {h7?.continuation ?? 0} continuation / {h7?.reversal ?? 0} reversal / {h7?.unchanged ?? 0} unchanged / {h7?.unknown ?? 0} unknown / {h7?.pending ?? 0} pending.
                          {'\n'}Quoted results include only simulations entered after quote receipt. Unquoted observations do not contribute to P&amp;L.
                        </td>
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
