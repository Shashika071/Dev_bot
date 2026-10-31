import { useState, type ReactNode } from 'react'
import { TradingChart } from './components/TradingChart'
import { SignalPanel } from './components/SignalPanel'
import { useMarketAndSignals } from './hooks/useMarketAndSignals'
import { useAutoTrade } from './hooks/useAutoTrade'
import { ResearchPanel } from './components/ResearchPanel'
import { SignalHistory } from './components/SignalHistory'
import { AutoTradePanel } from './components/AutoTradePanel'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'

type TabId = 'chart' | 'trade'

export default function App() {
  const [tab, setTab] = useState<TabId>('chart')
  const trade = useAutoTrade(SYMBOL)
  const {
    market, evaluations, latestEval, liveSignal, sound, setSound, history, clearHistory,
    research, storageWarning, netWinReturn, setNetWinReturn, liveSetup,
  } = useMarketAndSignals(trade.onTradeSignal)

  const tradeConnected = trade.status === 'CONNECTED'

  return (
    <div className="flex h-screen min-h-[650px] flex-col bg-[#0b0f14] text-slate-100">
      <header className="flex h-9 shrink-0 items-center gap-3 border-b border-slate-800 px-3 text-sm">
        <span className="font-bold text-white">V100 Smart Signal Bot</span>
        <span className="hidden text-slate-500 sm:inline">R_100 · M1</span>

        <nav className="ml-2 flex items-center gap-1 rounded-md border border-slate-800 bg-[#0c1118] p-0.5 text-xs">
          <TabButton active={tab === 'chart'} onClick={() => setTab('chart')}>Chart</TabButton>
          <TabButton active={tab === 'trade'} onClick={() => setTab('trade')}>
            Trade settings
            {trade.autoEnabled && (
              <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" title="Auto trade ON" />
            )}
          </TabButton>
        </nav>

        <div className="ml-auto flex items-center gap-3 text-xs tabular-nums">
          <span className="text-emerald-400">RISE {Math.round(100 * liveSetup.rise.confidence)}%</span>
          <span className="text-rose-400">FALL {Math.round(100 * liveSetup.fall.confidence)}%</span>
          {tab === 'chart' && (
            <span
              className={
                trade.autoEnabled
                  ? 'text-emerald-400'
                  : tradeConnected
                    ? 'text-sky-400'
                    : 'text-slate-500'
              }
              title="Trading connection"
            >
              trade {trade.autoEnabled ? 'AUTO' : trade.status === 'CONNECTED' ? 'ready' : 'off'}
              {trade.cooldownLeft > 0 ? ` · wait ${trade.cooldownLeft}s` : ''}
            </span>
          )}
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
          FULL {liveSignal.final} · 5/5 on completed candle
          {trade.autoEnabled ? ' · auto Touch firing' : ''}
        </div>
      )}
      {!liveSignal && (() => {
        const risePct = Math.round(100 * liveSetup.rise.confidence)
        const fallPct = Math.round(100 * liveSetup.fall.confidence)
        const side = risePct >= fallPct ? 'RISE' as const : 'FALL' as const
        const pct = Math.max(risePct, fallPct)
        if (pct < trade.minConfidencePct) return null
        return (
          <div
            className={`shrink-0 px-3 py-1 text-center text-xs font-semibold ${
              side === 'FALL'
                ? 'bg-amber-500/20 text-amber-200'
                : 'bg-cyan-500/20 text-cyan-200'
            }`}
          >
            PARTIAL {side} {pct}% · {pct >= 100 ? '5/5' : `${Math.round(pct / 20)}/5`} (not full signal)
            {trade.autoEnabled ? ' · auto may fire' : ''}
          </div>
        )
      })()}

      {storageWarning && <p role="alert" className="px-3 py-1 text-xs text-amber-300">{storageWarning}</p>}

      {tab === 'chart' ? (
        <>
          <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px]">
            <main className="min-h-0 min-w-0">
              <TradingChart
                candles={market?.candles ?? []}
                evaluations={evaluations}
                lastPrice={market?.lastPrice ?? null}
                minConfidencePct={trade.minConfidencePct}
              />
            </main>
            <SignalPanel
              market={market}
              eval_={latestEval}
              liveSignal={liveSignal}
              liveSetup={liveSetup}
              minConfidencePct={trade.minConfidencePct}
              sound={sound}
              onToggleSound={() => setSound((v) => !v)}
            />
          </div>
          <ResearchPanel research={research} netWinReturn={netWinReturn} onReturnChange={setNetWinReturn} />
          <details className="shrink-0 border-t border-slate-800">
            <summary className="cursor-pointer px-3 py-2 text-xs text-slate-300">
              Signal history and CSV export ({history.length})
            </summary>
            <div className="h-48"><SignalHistory rows={history} onClear={clearHistory} /></div>
          </details>
        </>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <AutoTradePanel trade={trade} />
        </div>
      )}
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center rounded px-2.5 py-1 font-medium transition-colors ${
        active
          ? 'bg-slate-700 text-white'
          : 'text-slate-400 hover:bg-slate-800/80 hover:text-slate-200'
      }`}
    >
      {children}
    </button>
  )
}
