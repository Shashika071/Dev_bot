import { TradingChart } from './components/TradingChart'
import { SignalPanel } from './components/SignalPanel'
import { useMarketAndSignals } from './hooks/useMarketAndSignals'
import { ResearchPanel } from './components/ResearchPanel'
import { SignalHistory } from './components/SignalHistory'

export default function App() {
  const {
    market, evaluations, latestEval, liveSignal, sound, setSound, history, clearHistory,
    research, storageWarning, netWinReturn, setNetWinReturn, liveSetup,
  } = useMarketAndSignals()

  return (
    <div className="flex h-screen min-h-[650px] flex-col bg-[#0b0f14] text-slate-100">
      <header className="flex h-9 shrink-0 items-center gap-3 border-b border-slate-800 px-3 text-sm">
        <span className="font-bold text-white">V100 Smart Signal Bot</span>
        <span className="hidden text-slate-500 sm:inline">R_100 · M1</span>
        <div className="ml-auto flex items-center gap-3 text-xs tabular-nums">
          <span className="text-emerald-400">RISE {Math.round(100 * liveSetup.rise.confidence)}%</span>
          <span className="text-rose-400">FALL {Math.round(100 * liveSetup.fall.confidence)}%</span>
          <span
            className={
              market?.status === 'CONNECTED'
                ? 'text-emerald-400'
                : market?.status === 'RECONNECTING'
                  ? 'text-amber-400'
                  : 'text-rose-400'
            }
          >
            ● {market?.ready ? 'READY' : market?.status ?? 'DISCONNECTED'}
          </span>
          <span className="font-mono font-semibold text-amber-300">
            {market?.lastPrice != null ? market.lastPrice.toFixed(2) : '—'}
          </span>
        </div>
      </header>

      {liveSignal && (
        <div
          className={`shrink-0 px-3 py-1 text-center text-xs font-bold text-white ${
            liveSignal.final === 'FALL' ? 'bg-rose-600' : 'bg-emerald-600'
          }`}
        >
          {liveSignal.final} · all 5 conditions met on completed candle
        </div>
      )}

      {storageWarning && <p role="alert" className="px-3 py-1 text-xs text-amber-300">{storageWarning}</p>}
      <div className="grid min-h-[320px] flex-1 grid-cols-[minmax(0,1fr)_300px]">
        <main className="min-h-0 min-w-0">
          <TradingChart
            candles={market?.candles ?? []}
            evaluations={evaluations}
            lastPrice={market?.lastPrice ?? null}
          />
        </main>
        <SignalPanel
          market={market}
          eval_={latestEval}
          liveSignal={liveSignal}
          liveSetup={liveSetup}
          sound={sound}
          onToggleSound={() => setSound((v) => !v)}
        />
      </div>
      <ResearchPanel research={research} netWinReturn={netWinReturn} onReturnChange={setNetWinReturn} />
      <details className="shrink-0 border-t border-slate-800">
        <summary className="cursor-pointer px-3 py-2 text-xs text-slate-300">Signal history and CSV export ({history.length})</summary>
        <div className="h-48"><SignalHistory rows={history} onClear={clearHistory} /></div>
      </details>
    </div>
  )
}
