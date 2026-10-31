import type { SignalHistoryRow } from '../lib/types'
import { exportCsv } from '../lib/history/signalStore'

export function SignalHistory({
  rows,
  onClear,
}: {
  rows: SignalHistoryRow[]
  onClear: () => void
}) {
  const download = () => {
    const blob = new Blob([exportCsv(rows)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'v100_signal_history.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex h-full min-h-0 flex-col border-t border-slate-800 bg-[#0c1118]">
      <div className="flex items-center justify-between px-3 py-2">
        <div className="text-sm font-semibold text-slate-200">Signal history</div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={download}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Export CSV
          </button>
          <button
            type="button"
            onClick={onClear}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700"
          >
            Clear completed
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-[#121820] text-slate-500">
            <tr>
              <th className="px-2 py-1">Time (UTC)</th>
              <th className="px-2 py-1">Signal</th>
              <th className="px-2 py-1">Close</th>
              <th className="px-2 py-1">Notify px</th>
              <th className="px-2 py-1">Paper 60s</th>
              <th className="px-2 py-1">Exit px</th>
              <th className="px-2 py-1">ST</th>
              <th className="px-2 py-1">SMA</th>
              <th className="px-2 py-1">AO</th>
              <th className="px-2 py-1">RSI</th>
              <th className="px-2 py-1">Feed</th>
              <th className="px-2 py-1">Failures</th>
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().slice(0, 200).map((r) => (
              <tr key={r.id} className="border-t border-slate-900/80 text-slate-300">
                <td className="px-2 py-1 whitespace-nowrap">
                  {new Date(r.timestamp * 1000).toISOString().replace('.000Z', 'Z')}
                </td>
                <td
                  className={`px-2 py-1 font-semibold ${
                    r.signal === 'RISE'
                      ? 'text-emerald-400'
                      : r.signal === 'FALL'
                        ? 'text-rose-400'
                        : 'text-amber-300'
                  }`}
                >
                  {r.signal}
                </td>
                <td className="px-2 py-1">{r.candleClose.toFixed(2)}</td>
                <td className="px-2 py-1">{r.notifyPrice?.toFixed(2) ?? '—'}</td>
                <td className="px-2 py-1" title={r.paper?.reason}>{r.paper?.status ?? '—'}</td>
                <td className="px-2 py-1">{r.paper?.exitPrice?.toFixed(2) ?? '—'}</td>
                <td className="px-2 py-1">{r.stDir === 1 ? 'UP' : r.stDir === -1 ? 'DN' : '—'}</td>
                <td className="px-2 py-1">{r.smaDir}</td>
                <td className="px-2 py-1">{r.ao?.toFixed(3) ?? '—'}</td>
                <td className="px-2 py-1">{r.rsi?.toFixed(1) ?? '—'}</td>
                <td className="px-2 py-1">{r.connection}</td>
                <td className="max-w-[240px] truncate px-2 py-1" title={r.failures.join('; ')}>
                  {r.failures.join('; ') || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
