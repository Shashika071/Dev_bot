import { exportTradeHistoryCsv, type TradeLogRow } from '../hooks/useAutoTrade'

export function TradeHistory({
  rows,
  onClear,
}: {
  rows: TradeLogRow[]
  onClear: () => void
}) {
  const buys = rows.filter((r) => r.ok && r.contractId != null).length
  const fails = rows.filter((r) => !r.ok).length

  const download = () => {
    const blob = new Blob([exportTradeHistoryCsv(rows)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'v100_trade_history.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="mt-5 flex min-h-0 flex-1 flex-col rounded-lg border border-slate-800 bg-[#0b0f14]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
        <div>
          <div className="text-sm font-semibold text-slate-200">Trade history</div>
          <div className="text-[11px] text-slate-500">
            {rows.length} events · {buys} buys · {fails} skipped/failed · saved in this browser
          </div>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={download}
            disabled={!rows.length}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-40"
          >
            Export CSV
          </button>
          <button
            type="button"
            onClick={onClear}
            disabled={!rows.length}
            className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-40"
          >
            Clear history
          </button>
        </div>
      </div>

      <div className="min-h-[280px] flex-1 overflow-auto">
        {!rows.length ? (
          <p className="px-3 py-8 text-center text-xs text-slate-500">
            No trades yet. Connect, turn Auto trade ON, and fills/skips will appear here.
          </p>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-[#121820] text-slate-500">
              <tr>
                <th className="px-2 py-1.5">Time (UTC)</th>
                <th className="px-2 py-1.5">Signal candle (UTC)</th>
                <th className="px-2 py-1.5">Side</th>
                <th className="px-2 py-1.5">Barrier</th>
                <th className="px-2 py-1.5">Status</th>
                <th className="px-2 py-1.5">Contract</th>
                <th className="px-2 py-1.5">Message</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-slate-900/80 text-slate-300">
                  <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-slate-400">
                    {new Date(r.at).toISOString().replace('.000Z', 'Z')}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-slate-400">
                    {r.candleTime != null ? new Date(r.candleTime * 1000).toISOString().replace('.000Z', 'Z') : '—'}
                  </td>
                  <td
                    className={`px-2 py-1.5 font-semibold ${
                      r.signal === 'RISE'
                        ? 'text-emerald-400'
                        : r.signal === 'FALL'
                          ? 'text-rose-400'
                          : 'text-slate-400'
                    }`}
                  >
                    {r.signal}
                  </td>
                  <td className="px-2 py-1.5 tabular-nums">{r.barrier}</td>
                  <td className={`px-2 py-1.5 font-semibold ${r.ok ? 'text-emerald-400' : 'text-rose-300'}`}>
                    {r.ok ? (r.contractId != null ? 'BOUGHT' : 'OK') : 'SKIP/FAIL'}
                  </td>
                  <td className="px-2 py-1.5 tabular-nums text-slate-400">
                    {r.contractId != null ? `#${r.contractId}` : '—'}
                  </td>
                  <td className="max-w-[420px] truncate px-2 py-1.5 text-slate-400" title={r.message}>
                    {r.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
