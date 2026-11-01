import type { SignalAnalysisReport, HorizonStats, BreakdownRow } from '../lib/research/signalAnalysis'
import { INSUFFICIENT_SAMPLE } from '../lib/research/signalAnalysis'
import { getObservationRetentionLimit } from '../lib/history/observationStore'

function pct(n: number | null) {
  return n == null ? '—' : `${(100 * n).toFixed(0)}%`
}

function num(n: number | null, digits = 2) {
  return n == null || !Number.isFinite(n) ? '—' : n.toFixed(digits)
}

function SampleNote({ n }: { n: number }) {
  if (n <= 0) return <span className="text-slate-500">no results yet</span>
  if (n < INSUFFICIENT_SAMPLE) {
    return (
      <span className="text-amber-300" title={`Need about ${INSUFFICIENT_SAMPLE}+ finished moves for a stable read`}>
        only {n} samples — keep collecting
      </span>
    )
  }
  return <span className="text-slate-500">{n} samples</span>
}

function HorizonCard({ h }: { h: HorizonStats }) {
  const c = h.counts
  const waiting = c.pending
  const finished = c.continuation + c.reversal + c.unchanged
  return (
    <div className="min-w-[160px] flex-1 rounded border border-slate-800 bg-[#121820] px-3 py-2">
      <div className="text-sm font-semibold text-slate-100">After {h.minutes} minute{h.minutes === 1 ? '' : 's'}</div>
      <p className="mt-1 text-[11px] text-slate-500">
        {c.total} setups · {waiting} still waiting · {c.unknown} unknown
      </p>

      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <span className="text-emerald-400">Went with signal</span>
        <span className="tabular-nums text-emerald-300">{c.continuation}</span>
        <span className="text-rose-400">Went against</span>
        <span className="tabular-nums text-rose-300">{c.reversal}</span>
        <span className="text-slate-400">No move</span>
        <span className="tabular-nums text-slate-300">{c.unchanged}</span>
      </div>

      <div className="mt-2 border-t border-slate-800 pt-2">
        <div className="text-[11px] text-slate-400">With-signal rate</div>
        <div className="text-lg font-semibold tabular-nums text-slate-100">
          {pct(h.continuationAmongDirectional)}
        </div>
        <div className="text-[11px]">
          <SampleNote n={h.sampleSizeDirectional} />
        </div>
        {h.sampleSizeDirectional > 0 && (
          <p className="mt-1 text-[11px] text-slate-500">
            Against-signal {pct(h.reversalAmongDirectional)} · avg move {num(h.avgSignedMove)} ({num(h.avgSignedMoveInAtr, 2)} ATR)
          </p>
        )}
        {finished === 0 && waiting > 0 && (
          <p className="mt-1 text-[11px] text-amber-200/80">
            Waiting up to {h.minutes}m after each setup — leave the page open.
          </p>
        )}
      </div>

      {h.randomSampleSizeDirectional > 0 && (
        <p className="mt-2 text-[10px] text-slate-500" title="Same times, coin-flip direction">
          Coin-flip reference: {pct(h.randomContinuationAmongDirectional)} with-signal
        </p>
      )}
    </div>
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
              <th className="px-2 py-1">Setups</th>
              <th className="px-2 py-1">1m</th>
              <th className="px-2 py-1">3m</th>
              <th className="px-2 py-1">5m</th>
              <th className="px-2 py-1">7m</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-slate-900/80 text-slate-300">
                <td className="px-2 py-1">{row.label}</td>
                <td className="px-2 py-1">{row.n}</td>
                {row.horizons.map((h) => (
                  <td key={h.minutes} className="px-2 py-1 tabular-nums">
                    {h.sampleSizeDirectional <= 0
                      ? '—'
                      : (
                        <span title={h.insufficient ? `Only ${h.sampleSizeDirectional} samples` : undefined}>
                          {pct(h.continuationAmongDirectional)}
                          {h.insufficient ? '*' : ''}
                        </span>
                      )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="px-2 pt-1 text-[10px] text-slate-500">* = fewer than {INSUFFICIENT_SAMPLE} finished moves — treat as early look only.</p>
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
            {report.totalObservations} setups recorded · {report.pendingObservations} still timing · saved up to {limit}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onExport}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Export CSV
          </button>
          <button
            type="button"
            onClick={onClearCompleted}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Clear finished
          </button>
        </div>
      </div>

      {storageWarning && (
        <p role="alert" className="mt-1 text-amber-300">{storageWarning}</p>
      )}

      <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-slate-400">
        Simple idea: when the bot reaches trade-level confidence (usually 80% = 4 of 5 conditions),
        we watch the public price 1, 3, 5 and 7 minutes later.
        <span className="text-emerald-400/90"> “Went with signal”</span> means price moved the same way as RISE/FALL.
        <span className="text-rose-400/90"> “Went against”</span> means the opposite.
        This is research only — not a trade result and not a win-rate promise.
      </p>

      {report.totalObservations === 0 ? (
        <p className="mt-2 text-slate-500">
          Nothing recorded yet. Keep the chart open on READY. Each new high-confidence setup is saved once per minute candle.
        </p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap gap-2">
            {report.byHorizon.map((h) => (
              <HorizonCard key={h.minutes} h={h} />
            ))}
          </div>

          <BreakdownTable title="RISE vs FALL" rows={report.byDirection} />
          <BreakdownTable title="Which condition was missing" rows={report.byConditionKey} />
          <BreakdownTable title="4/5 vs 5/5 setups" rows={report.byScoreBand} />
          <BreakdownTable title="Candle size vs ATR (research groups only)" rows={report.byCandleSize} />

          <details className="mt-2">
            <summary className="cursor-pointer text-slate-300">More detail</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px]">
              <li>80% means 4 of 5 conditions passed — not “80% chance to win”.</li>
              <li>With-signal % uses only “with” + “against” (flat / waiting / unknown are left out).</li>
              <li>Unknown = feed gap, disconnect, or page reload — not counted as a loss.</li>
              <li>Coin-flip reference uses the same times with a random direction; near 50% is expected.</li>
              <li>Do not pick a “best” group from a tiny sample. Wait for more finished moves.</li>
            </ul>
          </details>
        </>
      )}
    </div>
  )
}
