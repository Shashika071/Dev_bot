import type { summarize } from '../lib/research/statistics'

type Stats = ReturnType<typeof summarize>

function percent(n: number | null) { return n == null ? '—' : `${(100 * n).toFixed(1)}%` }

export function ResearchPanel({ research, netWinReturn, onReturnChange }: {
  research: { bars: number; historical: Stats; paper: Stats; pending: number; invalid: number }
  netWinReturn: number
  onReturnChange: (value: number) => void
}) {
  const p = research.paper
  return (
    <div className="shrink-0 border-t border-slate-800 bg-[#0c1118] px-3 py-2 text-xs text-slate-400">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <strong className="text-slate-200">Paper 60s observations</strong>
        <span>Wins {p.wins}/{p.n} ({percent(p.winRate)})</span>
        <span>95% interval {p.interval95 ? p.interval95.map(percent).join('–') : '—'}</span>
        <span>Pending {research.pending} · Unknown {research.invalid}</span>
        <span>Longest loss streak {p.maxLosingStreak}</span>
        <label>Assumed net win return (%){' '}
          <input aria-label="Assumed net win return percent" type="number" min="1" max="1000" step="1"
            className="w-16 rounded bg-slate-800 px-1 text-slate-100"
            value={Math.round(netWinReturn * 100)} onChange={(e) => {
              const value = Number(e.target.value)
              if (Number.isFinite(value) && value >= 1 && value <= 1000) onReturnChange(value / 100)
            }} />
        </label>
        <span>Break-even {percent(p.breakEven)} · Simulated net {p.netUnits.toFixed(2)} units</span>
        <span>Max drawdown {p.maxDrawdown.toFixed(2)} units</span>
      </div>
      <p className="mt-1 text-amber-200/80">Public-tick simulation, not executed contracts. Entry = alert tick; exit = first tick at/after 60s (up to 5s late). Ties lose; feed gaps become unknown. Return is an assumption, not a broker quote.</p>
      <details className="mt-1">
        <summary className="cursor-pointer">Historical candle proxy: {research.historical.wins}/{research.historical.n} ({percent(research.historical.winRate)}) · {research.bars} candles</summary>
        <p className="mt-1">Signal close to next completed close only; entry timing differs from live observations. Neither metric proves profitability. Intervals assume independent observations; correlated signals can make them too narrow. Clearing history resets these session statistics.</p>
      </details>
    </div>
  )
}
