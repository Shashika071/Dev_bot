import type { SignalAnalysisReport, HorizonStats, BreakdownRow } from '../lib/research/signalAnalysis'
import { INSUFFICIENT_SAMPLE } from '../lib/research/signalAnalysis'
import { getObservationRetentionLimit } from '../lib/history/observationStore'

function pct(n: number | null) {
  return n == null ? '—' : `${(100 * n).toFixed(0)}%`
}

function plainScore(h: HorizonStats): string {
  const ok = h.counts.continuation
  const bad = h.counts.reversal
  const n = ok + bad
  if (n === 0) return 'Not ready yet'
  return `${ok} correct out of ${n} (${pct(ok / n)})`
}

function SimpleRow({ h }: { h: HorizonStats }) {
  const ok = h.counts.continuation
  const bad = h.counts.reversal
  const flat = h.counts.unchanged
  const wait = h.counts.pending
  const n = ok + bad

  return (
    <tr className="border-t border-slate-800 text-slate-200">
      <td className="px-3 py-2 font-medium">{h.minutes} min later</td>
      <td className="px-3 py-2 tabular-nums text-emerald-400">{ok}</td>
      <td className="px-3 py-2 tabular-nums text-rose-400">{bad}</td>
      <td className="px-3 py-2 tabular-nums text-slate-400">{flat}</td>
      <td className="px-3 py-2 tabular-nums text-amber-300/90">{wait}</td>
      <td className="px-3 py-2">
        {n === 0 ? (
          <span className="text-slate-500">
            {wait > 0 ? 'Still waiting for time to pass…' : 'No finished checks yet'}
          </span>
        ) : (
          <span>
            <span className="font-semibold text-slate-100">{plainScore(h)}</span>
            {n < INSUFFICIENT_SAMPLE && (
              <span className="ml-2 text-amber-300">too few to trust yet</span>
            )}
          </span>
        )}
      </td>
    </tr>
  )
}

function ExtraTable({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  if (!rows.length) return null
  return (
    <div className="mt-2">
      <div className="text-[11px] font-medium text-slate-400">{title}</div>
      <table className="mt-1 w-full text-left text-[11px]">
        <thead className="text-slate-500">
          <tr>
            <th className="px-2 py-1">Group</th>
            <th className="px-2 py-1">Count</th>
            <th className="px-2 py-1">1m correct%</th>
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
                  {h.sampleSizeDirectional <= 0 ? '—' : pct(h.continuationAmongDirectional)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const h1 = report.byHorizon[0]

  return (
    <div className="shrink-0 border-t border-slate-800 bg-[#0c1118] px-3 py-3 text-xs text-slate-400">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <strong className="text-base text-slate-100">Did the signal direction come true?</strong>
          <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-slate-400">
            When the bot shows a strong RISE or FALL (about 80%), we remember that moment.
            Then we check the public price later — like a homework check, not a real trade result.
          </p>
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
        <p role="alert" className="mt-2 text-amber-300">{storageWarning}</p>
      )}

      <div className="mt-3 rounded border border-slate-700/80 bg-[#121820] px-3 py-2 text-[12px] leading-relaxed text-slate-300">
        <div className="font-medium text-slate-100">Example</div>
        <p className="mt-1">
          Bot says <span className="text-emerald-400 font-semibold">RISE</span> at price 100.
        </p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          <li>
            1 minute later price is <span className="text-emerald-400">101</span> → count as{' '}
            <span className="text-emerald-400 font-semibold">correct</span>
          </li>
          <li>
            1 minute later price is <span className="text-rose-400">99</span> → count as{' '}
            <span className="text-rose-400 font-semibold">wrong</span>
          </li>
          <li>
            Price still <span className="text-slate-200">100</span> → count as{' '}
            <span className="text-slate-200">same / no move</span>
          </li>
        </ul>
        <p className="mt-2 text-slate-500">
          Same idea for FALL (price going down = correct). This is not money won or lost.
        </p>
      </div>

      {report.totalObservations === 0 ? (
        <p className="mt-3 text-slate-500">
          No checks yet. Keep the chart open on READY. When a strong setup appears, it will show up here after a few minutes.
        </p>
      ) : (
        <>
          <p className="mt-3 text-[12px] text-slate-400">
            Recorded setups: <span className="text-slate-200">{report.totalObservations}</span>
            {' · '}
            Still waiting: <span className="text-amber-300">{report.pendingObservations}</span>
            {' · '}
            Max saved: {limit}
            {h1 && h1.sampleSizeDirectional > 0 && (
              <>
                {' · '}
                Right now (1 min): <span className="text-slate-100 font-medium">{plainScore(h1)}</span>
              </>
            )}
          </p>

          <div className="mt-2 overflow-auto rounded border border-slate-800">
            <table className="w-full min-w-[640px] text-left text-[12px]">
              <thead className="bg-[#121820] text-slate-500">
                <tr>
                  <th className="px-3 py-2">Check time</th>
                  <th className="px-3 py-2 text-emerald-400/80">Correct</th>
                  <th className="px-3 py-2 text-rose-400/80">Wrong</th>
                  <th className="px-3 py-2">Same price</th>
                  <th className="px-3 py-2">Waiting</th>
                  <th className="px-3 py-2">Meaning</th>
                </tr>
              </thead>
              <tbody>
                {report.byHorizon.map((h) => (
                  <SimpleRow key={h.minutes} h={h} />
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-2 text-[11px] text-slate-500">
            Tip: ignore the % until you have about {INSUFFICIENT_SAMPLE}+ finished correct/wrong checks.
            “Waiting” means not enough time has passed yet — leave the page open.
          </p>

          <details className="mt-3">
            <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
              Extra numbers (optional — you can ignore this)
            </summary>
            <ExtraTable title="RISE vs FALL" rows={report.byDirection} />
            <ExtraTable title="Which condition was missing" rows={report.byConditionKey} />
            <ExtraTable title="4/5 vs 5/5" rows={report.byScoreBand} />
            <ExtraTable title="Candle size groups" rows={report.byCandleSize} />
          </details>
        </>
      )}
    </div>
  )
}
