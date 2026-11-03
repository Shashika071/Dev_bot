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
import { FOCUSED_CONFIRMATION_LABELS, isFocusedConfirmation } from '../lib/research/focusedConfirmation'

const SERVER_CACHE_KEY = 'v100_research_server_status_v1'

function pct(n: number | null | undefined) {
  return n == null ? '—' : `${(100 * n).toFixed(1)}%`
}

function touchRate(hits: number, misses: number) {
  return hits + misses > 0 ? `${pct(hits / (hits + misses))}${hits + misses < 20 ? '*' : ''}` : '—'
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
  const quality = (strategies as StrategySummary[]).reduce((totals, s) => {
    totals.hits += s.touchHits ?? 0
    totals.misses += s.touchMisses ?? 0
    totals.unknown += s.touchUnknown ?? 0
    totals.pending += s.touchPending ?? 0
    totals.quoteHits += s.quotedHits ?? 0
    totals.quoteMisses += s.quotedMisses ?? 0
    totals.quoteUnknown += s.quotedUnknown ?? 0
    totals.quotePending += s.quotedPending ?? 0
    totals.unquoted += s.candidatesAccepted - (s.quotesAvailable ?? 0)
    return totals
  }, { hits: 0, misses: 0, unknown: 0, pending: 0, quoteHits: 0, quoteMisses: 0, quoteUnknown: 0, quotePending: 0, unquoted: 0 })
  const feedDiagnostics = usingServer ? remote?.feedDiagnostics : null
  const legacy = (summary?.codeVersion ?? st?.codeVersion) !== RESEARCH_CODE_VERSION
  const controlsDisabled = busy || probing || (usingServer && !remoteLive)
  const contract = (st as { contract?: { contractType: string; barrierAbs: number; duration: number; durationUnit: string; paperStake: number; currency: string } } | undefined)?.contract
  const experimentId = usingServer ? (remote as { experimentId?: string } | null)?.experimentId : null
  const observationCount = usingServer
    ? (remote?.state?.observationCount ?? 0)
    : browser.state.observations.length
  const otherRuns = usingServer
    ? (((remote as { experimentSummaries?: Array<{ id: string; observationCount: number; stage: string | null; startedAt: number | null }> } | null)
        ?.experimentSummaries ?? [])
      .filter((r) => r.id !== experimentId && r.observationCount > 0)
      .slice(0, 8))
    : []
  const enabledIds = (st as { enabledStrategyIds?: string[] } | undefined)?.enabledStrategyIds as
    | import('../lib/research/strategies/types').StrategyId[]
    | undefined
  const focusedRun = !!(summary as { focusedConfirmation?: boolean } | undefined)?.focusedConfirmation
    || isFocusedConfirmation(enabledIds)

  function confirmWipe(action: string): boolean {
    if (observationCount <= 0) return true
    return window.confirm(
      `${action} will archive this run (${observationCount} observations) and start a new run from zero.`,
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 text-xs text-slate-300">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Research</h1>
          <p className="mt-1 max-w-2xl text-[12px] text-slate-400">
            Paper-only research. Does not trade and does not use your token.
            Use discovery for all 22 strategies, or focused confirmation for B1 / C5 / C1.
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
          <div>
            Strategies:{' '}
            <span className="text-slate-100">
              {focusedRun
                ? `focused ${enabledIds?.length ?? 3} (B1/C5/C1)`
                : `${enabledIds?.length ?? registryCount}/${registryCount}`}
            </span>
          </div>
          <div>Start: {ago((st as { startedAt?: number | null } | undefined)?.startedAt)}</div>
          <div>Ends: {ago((st as { endsAt?: number | null } | undefined)?.endsAt)}</div>
          <div>Finished: {ago((st as { finishedAt?: number | null } | undefined)?.finishedAt)}</div>
          <div>
            Feed:{' '}
            {(st as { feedHealthy?: boolean } | undefined)?.feedHealthy ? 'healthy' : 'unhealthy/offline'}
          </div>
          <div>Last tick: {ago((st as { lastTickEpoch?: number | null } | undefined)?.lastTickEpoch)}</div>
          {feedDiagnostics && <div>Feed disconnects since collector restart: {feedDiagnostics.disconnects}</div>}
          <div>
            Observations:{' '}
            <span className="font-semibold text-slate-100">{observationCount}</span>
            {usingServer && !remoteLive ? ' (cached)' : ''}
          </div>
        </div>

        {feedDiagnostics?.lastDisconnectReason && (
          <p className="mt-2 text-amber-200">Last feed diagnostic: {feedDiagnostics.lastDisconnectReason}</p>
        )}

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
                  if (!confirmWipe('Start focused confirmation: B1 / C5 / C1')) return
                  void runRemote(
                    '/control/start-focused-confirmation',
                    undefined,
                    'Focused confirmation started (B1 / C5 / C1)',
                  )
                }}
                className={`${btn} bg-sky-700 text-white hover:bg-sky-600`}
              >
                Start focused confirmation: B1 / C5 / C1
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
                  void runRemote('/control/reset', undefined, 'Previous run archived; new discovery started from 0')
                }}
                className={`${btn} bg-rose-800 text-rose-100 hover:bg-rose-700`}
              >
                Archive and restart
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
                  if (!confirmWipe('Start focused confirmation: B1 / C5 / C1')) return
                  try {
                    const next = browser.startFocusedConfirmation()
                    note(`Focused confirmation started (B1 / C5 / C1) — stage: ${next.stage}`)
                  } catch {
                    note('Could not archive the existing run. Export it before starting another run.')
                  }
                }}
                className={`${btn} bg-sky-700 text-white hover:bg-sky-600`}
              >
                Start focused confirmation: B1 / C5 / C1
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
                Archive and restart
              </button>
            </>
          )}
        </div>

        {focusedRun && (
          <div className="mt-3 rounded border border-sky-800/60 bg-sky-950/30 p-3 text-[11px] text-sky-100">
            <div className="font-medium text-sky-200">Focused paper comparison (not live trading)</div>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-sky-100/90">
              <li><span className="font-mono">B1</span>: {FOCUSED_CONFIRMATION_LABELS.B1}</li>
              <li><span className="font-mono">C5</span>: {FOCUSED_CONFIRMATION_LABELS.C5}</li>
              <li><span className="font-mono">C1</span>: {FOCUSED_CONFIRMATION_LABELS.C1}</li>
            </ul>
            <p className="mt-2 text-sky-200/80">
              Compare Touch performance first. A small sample or a 100% observed win rate is not a proven best strategy.
            </p>
          </div>
        )}

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

        {usingServer && otherRuns.length > 0 && (
          <div className="mt-3 rounded border border-slate-700 bg-slate-950/50 p-2">
            <div className="mb-1 font-medium text-slate-200">Previous saved runs (not wiped)</div>
            <p className="mb-2 text-[11px] text-slate-500">
              Start/Reset creates a new run. Your older observations are still on the server — load one below.
            </p>
            <div className="flex flex-col gap-1">
              {otherRuns.map((run) => (
                <div key={run.id} className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                  <span className="text-slate-300">
                    {run.id}
                    {' · '}
                    <span className="text-emerald-300">{run.observationCount} obs</span>
                    {run.stage ? ` · ${run.stage}` : ''}
                  </span>
                  <button
                    type="button"
                    disabled={controlsDisabled}
                    className={`${btn} bg-slate-700 text-slate-100 hover:bg-slate-600`}
                    onClick={() =>
                      void runRemote(
                        '/control/load-experiment',
                        { id: run.id },
                        `Loaded ${run.id} (${run.observationCount} observations)`,
                      )
                    }
                  >
                    Load this run
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-medium text-slate-200">Touch results — target reached within {contract?.duration ?? 7} minutes</div>
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
          HIT = win immediately when the target is touched. A later reversal or disconnection does not cancel that win.
          MISS = no touch by expiry with full tick coverage. UNKNOWN = no confirmed hit and incomplete coverage.
          PENDING = the target has not been touched and the window is still being observed.
        </p>
        <p className="mt-2 text-[11px] text-slate-400">
          H/M/U/P = hit / miss / unknown / pending. Signal Touch starts at the signal tick; quoted Touch starts after quote receipt.
          Win% = hits / (hits + misses); unknown and pending results are excluded. P&amp;L uses settled quoted results only.
          Quote cov% = quoted / accepted. Median TTT = median seconds to first touch among signal-reference hits.
          * Fewer than 20 settled results; all comparisons remain exploratory — not a proven ranking.
        </p>
        {observationCount > 0 && (
          <div role={quality.unknown > 0 || quality.quoteUnknown > 0 ? 'alert' : 'status'}
            className="mt-3 rounded border border-amber-800/60 bg-amber-950/30 p-3 text-amber-100">
            <p>Signal Touch: {quality.hits} hits, {quality.misses} misses, {quality.unknown} unknown, {quality.pending} pending.</p>
            {!legacy && <p>Quoted Touch: {quality.quoteHits} hits, {quality.quoteMisses} misses, {quality.quoteUnknown} unknown, {quality.quotePending} pending.</p>}
            <p>Signals without usable quotes: {quality.unquoted}.</p>
            {(quality.unknown > 0 || quality.quoteUnknown > 0) && <p className="mt-1">
              Missing Touch outcomes can affect the overall win rate and profit. Confirmed hits remain wins.
            </p>}
            <p className="mt-1">The P&amp;L range below includes matured quoted unknowns: all losses to all wins.
              It excludes pending and unquoted signals and is not a confidence interval.</p>
          </div>
        )}
        <div className="mt-2 overflow-auto">
          <table className="w-full min-w-[1600px] text-left text-[11px]">
            <thead className="text-slate-500">
              <tr>
                <th className="px-2 py-1">ID</th>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">n</th>
                <th className="px-2 py-1">Signal Touch win%</th>
                <th className="px-2 py-1">Signal H/M/U/P</th>
                <th className="px-2 py-1">Quote cov%</th>
                <th className="px-2 py-1">Sig/day</th>
                <th className="px-2 py-1">Median TTT</th>
                <th className="px-2 py-1">Quotes/n</th>
                <th className="px-2 py-1">Quoted Touch win%</th>
                <th className="px-2 py-1">Quoted H/M/U/P</th>
                <th className="px-2 py-1">Settled n / cost</th>
                <th className="px-2 py-1">Settled P&amp;L</th>
                <th className="px-2 py-1">Settled return</th>
                <th className="px-2 py-1">Matured quoted P&amp;L range</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s: StrategySummary) => {
                const h7 = s.horizons.find((h) => h.minutes === 7)
                const label = s.id === 'B1' || s.id === 'C5' || s.id === 'C1'
                  ? FOCUSED_CONFIRMATION_LABELS[s.id]
                  : null
                return (
                  <Fragment key={s.id}>
                    <tr
                      className="cursor-pointer border-t border-slate-900/80 hover:bg-slate-900/50"
                      onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                    >
                      <td className="px-2 py-1 font-mono text-slate-200">{s.id}</td>
                      <td className="px-2 py-1">
                        {s.name}
                        {label && <div className="text-[10px] text-slate-500">{label}</div>}
                      </td>
                      <td className="px-2 py-1 tabular-nums">{s.candidatesAccepted}</td>
                      <td className="px-2 py-1 tabular-nums">{touchRate(s.touchHits, s.touchMisses)}</td>
                      <td className="px-2 py-1 tabular-nums">{s.touchHits}/{s.touchMisses}/{s.touchUnknown}/{s.touchPending ?? 0}</td>
                      <td className="px-2 py-1 tabular-nums">{pct(s.quoteCoverageRate)}</td>
                      <td className="px-2 py-1 tabular-nums">{s.signalsPerDay == null ? '—' : s.signalsPerDay.toFixed(2)}</td>
                      <td className="px-2 py-1 tabular-nums">{s.medianTimeToTouchSec == null ? '—' : `${Math.round(s.medianTimeToTouchSec)}s`}</td>
                      <td className="px-2 py-1">{s.quotesAvailable}/{s.candidatesAccepted}</td>
                      <td className="px-2 py-1 tabular-nums">{legacy ? '—' : touchRate(s.quotedHits, s.quotedMisses)}</td>
                      <td className="px-2 py-1">{legacy ? "—" : `${s.quotedHits}/${s.quotedMisses}/${s.quotedUnknown}/${s.quotedPending}`}</td>
                      <td className="px-2 py-1">{legacy ? "—" : `${s.quotedSettled} / ${s.paperCost?.toFixed(2) ?? "—"}`}</td>
                      <td className="px-2 py-1">{legacy || s.pnlUnavailable ? 'UNAVAILABLE' : (s.paperPnl?.toFixed(4) ?? '—')}</td>
                      <td className="px-2 py-1">{legacy ? '—' : pct(s.paperReturn)}</td>
                      <td className="px-2 py-1">{legacy || s.paperPnlLower == null ? '—' : `${s.paperPnlLower.toFixed(2)} to ${s.paperPnlUpper?.toFixed(2)}`}</td>
                    </tr>
                    {expanded === s.id && (
                      <tr className="border-t border-slate-900/50 bg-[#121820]">
                        <td colSpan={15} className="px-3 py-2 text-[11px] text-slate-400 whitespace-pre-wrap">{s.rules}{'\n\n'}
                          Quoted results include only simulations entered after quote receipt. Unquoted observations do not contribute to P&amp;L.
                          Signal-reference Touch and quoted-paper Touch are counted separately; endpoint unknowns never mix into Touch H/M/U/P.
                          <details className="mt-2">
                            <summary className="cursor-pointer">Optional endpoint diagnostics (not Touch wins)</summary>
                            <p className="mt-1">Direction at minute 7: {h7?.continuation ?? 0} continuation / {h7?.reversal ?? 0} reversal / {h7?.unchanged ?? 0} unchanged / {h7?.unknown ?? 0} unknown / {h7?.pending ?? 0} pending.</p>
                            <p>A confirmed Touch hit stays a win regardless of this diagnostic. The endpoint 50/50 reference is not a Touch win-rate benchmark.</p>
                          </details>
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
