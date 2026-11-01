import { Fragment, useState } from 'react'
import type { MarketState } from '../lib/deriv/marketFeed'
import { useBrowserResearch } from '../hooks/useBrowserResearch'

function pct(n: number | null | undefined) {
  return n == null ? '—' : `${(100 * n).toFixed(1)}%`
}

function ago(epoch: number | null | undefined) {
  if (epoch == null) return '—'
  return new Date(epoch * 1000).toISOString().replace('.000Z', 'Z')
}

export function ResearchTab({ market }: { market: MarketState | null }) {
  const research = useBrowserResearch(market)
  const [expanded, setExpanded] = useState<string | null>(null)
  const st = research.state
  const summary = research.summary
  const strategies = summary.strategies

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 text-xs text-slate-300">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Research</h1>
          <p className="mt-1 max-w-2xl text-[12px] text-slate-400">
            Paper-only 22-strategy experiment on the same public R_100 feed as Chart.
            Works after deploy in this browser — no local collector required.
            Does not trade and does not use your token.
          </p>
        </div>
        <div className="text-right text-[11px]">
          <div className={research.live ? 'text-emerald-400' : 'text-amber-300'}>
            {research.live ? '● Live public feed' : '● Waiting for READY feed'}
          </div>
          <div className="text-slate-500">In-browser research · saved in this browser</div>
        </div>
      </div>

      {!research.live && (
        <p role="alert" className="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-2 text-amber-200">
          Market feed not READY yet. Stay on this site until the header shows READY, then start discovery.
          Keep this browser tab open for the 72h run (or at least while collecting).
        </p>
      )}

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="font-medium text-slate-200">Experiment status</div>
        <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <div>Stage: <span className="text-slate-100">{st.stage}</span></div>
          <div>Mode: <span className="text-slate-100">{st.mode}</span></div>
          <div>Registry: <span className="text-slate-100">{research.registry.length}/22</span></div>
          <div>Start: {ago(st.startedAt)}</div>
          <div>Ends: {ago(st.endsAt)}</div>
          <div>Finished: {ago(st.finishedAt)}</div>
          <div>Feed: {st.feedHealthy ? 'healthy' : 'unhealthy/offline'}</div>
          <div>Last tick: {ago(st.lastTickEpoch)}</div>
          <div>Observations: {st.observations.length}</div>
          <div>Rejected: {st.rejected.length}</div>
          <div>Pending: {summary.strategies.reduce((n, s) => n + s.pending, 0)}</div>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Frozen paper contract: {st.contract.contractType} barrier±{st.contract.barrierAbs}{' '}
          {st.contract.duration}{st.contract.durationUnit} stake={st.contract.paperStake} {st.contract.currency}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => research.start()}
            className="rounded bg-emerald-800 px-2 py-1 text-emerald-100"
          >
            Start 72h discovery
          </button>
          <button
            type="button"
            onClick={() => research.resume()}
            className="rounded bg-slate-700 px-2 py-1"
          >
            Resume
          </button>
          <button
            type="button"
            onClick={() => research.stopEntries()}
            className="rounded bg-slate-700 px-2 py-1"
          >
            Stop new entries
          </button>
          <button
            type="button"
            onClick={() => research.startConfirmation()}
            className="rounded bg-slate-700 px-2 py-1"
          >
            Start confirmation (same IDs)
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          For a full 72h on the deployed site: leave this browser open (or reopen and click Resume).
          Data is stored in this browser only. Auto trade can stay OFF.
        </p>
      </section>

      <section className="rounded border border-slate-800 bg-[#0c1118] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-medium text-slate-200">Comparison (descriptive)</div>
          <div className="flex gap-2">
            <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => research.downloadCsv()}>CSV</button>
            <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => research.downloadReport()}>Report MD</button>
            <button type="button" className="rounded bg-slate-800 px-2 py-1 hover:bg-slate-700" onClick={() => research.downloadSummary()}>Summary JSON</button>
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          7m is the primary direction horizon. Compare completed strategies to B1; B0 timing differs (forming).
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
              {strategies.map((s) => {
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
        <p className="mt-2 text-[10px] text-slate-500">* small sample — exploratory only. Never auto-enables in live trading.</p>
      </section>
    </div>
  )
}
