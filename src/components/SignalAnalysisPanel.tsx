import type { SignalAnalysisReport, HorizonStats, BreakdownRow } from '../lib/research/signalAnalysis'
import { INSUFFICIENT_SAMPLE } from '../lib/research/signalAnalysis'
import { getObservationRetentionLimit } from '../lib/history/observationStore'

function pct(n: number | null) {
  return n == null ? '—' : `${(100 * n).toFixed(1)}%`
}

function num(n: number | null, digits = 4) {
  return n == null || !Number.isFinite(n) ? '—' : n.toFixed(digits)
}

function HorizonRow({ h }: { h: HorizonStats }) {
  const c = h.counts
  return (
    <tr className="border-t border-slate-900/80 text-slate-300">
      <td className="px-2 py-1 font-medium text-slate-200">{h.minutes}m</td>
      <td className="px-2 py-1">{c.total}</td>
      <td className="px-2 py-1">{c.completed}</td>
      <td className="px-2 py-1">{c.pending}</td>
      <td className="px-2 py-1">{c.unknown}</td>
      <td className="px-2 py-1">{c.unchanged}</td>
      <td className="px-2 py-1 text-emerald-400">{c.continuation}</td>
      <td className="px-2 py-1 text-rose-400">{c.reversal}</td>
      <td className="px-2 py-1" title="Among continuation + reversal only">
        {h.insufficient ? (
          <span className="text-amber-300">n&lt;{INSUFFICIENT_SAMPLE}</span>
        ) : (
          <>
            {pct(h.continuationAmongDirectional)} / {pct(h.reversalAmongDirectional)}
            <span className="text-slate-500"> (n={h.sampleSizeDirectional})</span>
          </>
        )}
      </td>
      <td className="px-2 py-1">{num(h.avgSignedMove)} / {num(h.avgSignedMoveInAtr, 3)} ATR</td>
      <td className="px-2 py-1 text-slate-400">
        {h.descriptiveWilson
          ? `${pct(h.descriptiveWilson[0])}–${pct(h.descriptiveWilson[1])}`
          : '—'}
      </td>
      <td className="px-2 py-1 text-slate-400" title="Matched 50/50 random direction">
        {h.randomSampleSizeDirectional < INSUFFICIENT_SAMPLE
          ? `n&lt;${INSUFFICIENT_SAMPLE}`
          : `${pct(h.randomContinuationAmongDirectional)} (n=${h.randomSampleSizeDirectional})`}
      </td>
    </tr>
  )
}

function BreakdownTable({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  if (!rows.length) return null
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-slate-300">{title}</summary>
      <div className="mt-1 overflow-auto">
        <table className="w-full text-left text-[11px]">
          <thead className="text-slate-500">
            <tr>
              <th className="px-2 py-1">Group</th>
              <th className="px-2 py-1">n</th>
              <th className="px-2 py-1">1m cont%</th>
              <th className="px-2 py-1">3m cont%</th>
              <th className="px-2 py-1">5m cont%</th>
              <th className="px-2 py-1">7m cont%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-slate-900/80 text-slate-300">
                <td className="px-2 py-1">{row.label}</td>
                <td className="px-2 py-1">{row.n}</td>
                {row.horizons.map((h) => (
                  <td key={h.minutes} className="px-2 py-1">
                    {h.insufficient ? `n&lt;${INSUFFICIENT_SAMPLE}` : pct(h.continuationAmongDirectional)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export function SignalAnalysisPanel({
  report,
  storageWarning,
  onExport,
  onClearCompleted,
}: {
  report: SignalAnalysisReport
  storageWarning: string | null
  onExport: () => void
  onClearCompleted: () => void
}) {
  const limit = getObservationRetentionLimit()
  return (
    <div className="shrink-0 border-t border-slate-800 bg-[#0c1118] px-3 py-2 text-xs text-slate-400">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <strong className="text-slate-200">Signal analysis</strong>
          <span>
            {report.totalObservations} observations · {report.pendingObservations} pending · cap {limit}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onExport}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Export analysis CSV
          </button>
          <button
            type="button"
            onClick={onClearCompleted}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Clear completed
          </button>
        </div>
      </div>

      {storageWarning && (
        <p role="alert" className="mt-1 text-amber-300">{storageWarning}</p>
      )}

      <p className="mt-1 text-amber-200/80">
        Public-tick research when live confidence first hits the trade threshold (default 80% = 4/5 conditions).
        Not a broker fill and not an automatic trading rule change.
      </p>

      {report.totalObservations === 0 ? (
        <p className="mt-2 text-slate-500">
          No observations yet. Leave the chart open on READY; each new ≥threshold setup is recorded once per candle and direction.
        </p>
      ) : (
        <>
          <div className="mt-2 overflow-auto">
            <table className="w-full min-w-[960px] text-left text-[11px]">
              <thead className="sticky top-0 bg-[#121820] text-slate-500">
                <tr>
                  <th className="px-2 py-1">Horizon</th>
                  <th className="px-2 py-1">Total</th>
                  <th className="px-2 py-1">Done</th>
                  <th className="px-2 py-1">Pending</th>
                  <th className="px-2 py-1">Unknown</th>
                  <th className="px-2 py-1">Flat</th>
                  <th className="px-2 py-1">Cont.</th>
                  <th className="px-2 py-1">Rev.</th>
                  <th className="px-2 py-1">Cont% / Rev%</th>
                  <th className="px-2 py-1">Avg move / ATR</th>
                  <th className="px-2 py-1">Desc. Wilson</th>
                  <th className="px-2 py-1">Random 50/50 cont%</th>
                </tr>
              </thead>
              <tbody>
                {report.byHorizon.map((h) => (
                  <HorizonRow key={h.minutes} h={h} />
                ))}
              </tbody>
            </table>
          </div>

          <BreakdownTable title="By direction (RISE vs FALL)" rows={report.byDirection} />
          <BreakdownTable title="By missing condition combination" rows={report.byConditionKey} />
          <BreakdownTable title="Four-of-five vs five-of-five" rows={report.byScoreBand} />
          <BreakdownTable title="By last completed candle size vs ATR" rows={report.byCandleSize} />

          <details className="mt-2">
            <summary className="cursor-pointer text-slate-300">How to read this</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {report.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
              <li>
                Horizons use the first valid tick at or after 1/3/5/7 minutes, at most 5s late.
                Gaps, disconnects, and reloads become unknown — they are not counted as losses.
              </li>
              <li>No “best setup” is recommended automatically. Small percentage gaps are not significance claims.</li>
            </ul>
          </details>
        </>
      )}
    </div>
  )
}
