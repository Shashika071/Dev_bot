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

const SERVER_CACHE_KEY = 'v100_research_server_status_v1'

function pct(n: number | null | undefined) {
  return n == null ? '—' : `${(100 * n).toFixed(1)}%`
}

function ago(epoch: number | null | undefined) {
  if (epoch == null) return '—'
  return new Date(epoch * 1000).toISOString().replace('.000Z', 'Z')
}

type RemotePayload = Awaited<ReturnType<typeof fetchResearchStatus>>

function readServerCache(): RemotePayload | null {
  try {
    const raw = sessionStorage.getItem(SERVER_CACHE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as RemotePayload
  } catch {
    return null
  }
}

function writeServerCache(status: RemotePayload) {
  try {
    sessionStorage.setItem(SERVER_CACHE_KEY, JSON.stringify(status))
  } catch {
    /* ignore quota */
  }
}

export function ResearchTab({ market }: { market: MarketState | null }) {
  const cached = readServerCache()
  const [mode, setMode] = useState<'loading' | 'server' | 'browser'>(cached ? 'server' : 'loading')
  const [remote, setRemote] = useState<RemotePayload | null>(cached)
  const browser = useBrowserResearch(market, mode === 'browser')
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
        const prevCount = (readServerCache()?.state?.observationCount
          ?? cached?.state?.observationCount
          ?? 0) as number
        const nextCount = status?.state?.observationCount ?? 0
        if (prevCount > 0 && nextCount === 0 && status?.experimentId !== readServerCache()?.experimentId) {
          note(`New research run loaded (0 observations). Previous snapshot had ${prevCount}. Export/check older runs before Start/Reset.`)
        }
        setRemoteLive(!!health.live)
        setGeneratedAt(health.generatedAt)
        setRemote(status)
        writeServerCache(status)
        setMode('server')
      } catch {
        if (cancelled) return
        setRemoteLive(false)
        // Keep cached server snapshot after refresh; only fall back to browser when we never had server data.
        setMode((prev) => {
          if (prev === 'server' || readServerCache() != null) return 'server'
          return 'browser'
        })
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
      writeServerCache(status)
      setRemoteLive(true)
      setMode('server')
      note(okMsg ?? `OK — stage: ${status?.state?.stage ?? 'updated'}`)
    } catch (e) {
      note(e instanceof Error ? e.message : 'Control request failed')
    } finally {
      setBusy(false)
    }
  }

  const usingServer = (mode === 'server' || mode === 'loading') && remote != null
  const probing = mode === 'loading' && remote == null
  const st = usingServer ? remote?.state : browser.state
  const strategies = usingServer
    ? (remote?.summary?.strategies ?? [])
    : browser.summary.strategies
  const registryCount = usingServer
    ? (remote?.registryCount ?? 0)
    : browser.registry.length
  const summary = usingServer ? remote?.summary : browser.summary
  const legacy = (summary?.codeVersion ?? st?.codeVersion) !== RESEARCH_CODE_VERSION
  const controlsDisabled = busy || probing || (usingServer && !remoteLive)
  const contract = (st as { contract?: { contractType: string; barrierAbs: number; duration: number; durationUnit: string; paperStake: number; currency: string } } | undefined)?.contract
  const experimentId = usingServer ? (remote as { experimentId?: string } | null)?.experimentId : null
  const observationCount = usingServer
    ? (remote?.state?.observationCount ?? 0)
    : browser.state.observations.length

  function confirmWipe(action: string): boolean {
    if (observationCount <= 0) return true
    return window.confirm(
      `${action} will clear ${observationCount} observations and start from zero. Export first if you need this run.`,
    )
  }

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
          {probing ? (
            <div className="text-slate-400">● Connecting to research collector…</div>
          ) : usingServer ? (
            <>
              <div className={remoteLive ? 'text-emerald-400' : 'text-rose-400'}>
                {remoteLive ? '● Server collector' : '● Server disconnected — showing last saved snapshot'}
              </div>
              <div className="text-slate-500">Report {generatedAt ? ago(generatedAt) : '—'}</div>
              {experimentId && <div className="text-slate-500">Run {experimentId}</div>}
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

      {probing && (
        <p className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2 text-slate-300">
          Loading research status — keeping the last known observation count so refresh does not flash 0.
        </p>
      )}

      {!probing && !usingServer && (
        <p className="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-2 text-amber-200">
          Server collector not reachable yet — using browser mode for now.
          After you redeploy with the <code className="text-amber-100">research</code> container,
          this becomes always-on and closing the tab is OK.
        </p>
      )}

      {usingServer && !remoteLive && observationCount > 0 && (
        <p className="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-2 text-amber-200">
          Showing cached server snapshot ({observationCount} observations). Live collector is unreachable right now —
          this is not a wipe; counts return when the research service responds again.
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
            <span className="font-semibold text-slate-100">{observationCount}</span>
            {usingServer && !remoteLive ? ' (cached)' : ''}
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
                onClick={() => {
                  if (!confirmWipe('Start 72h discovery')) return
                  void runRemote('/control/start-discovery', undefined, 'Discovery started (server)')
                }}
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
                onClick={() => {
                  if (!confirmWipe('Start confirmation')) return
                  void runRemote(
                    '/control/start-confirmation',
                    { strategyIds: remote?.state?.enabledStrategyIds },
                    'Confirmation started',
                  )
                }}
                className={`${btn} bg-slate-600 text-slate-100 hover:bg-slate-500`}
              >
                Start confirmation (same IDs)
              </button>
              <button
                type="button"
                disabled={controlsDisabled}
                onClick={() => {
                  if (!confirmWipe('Reset')) return
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
                  if (!confirmWipe('Start 72h discovery')) return
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
                  if (!confirmWipe('Start confirmation')) return
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
                  if (!confirmWipe('Reset')) return
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
