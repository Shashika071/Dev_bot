import { useState, type ReactNode } from 'react'
import { TradingChart } from './components/TradingChart'
import { SignalPanel } from './components/SignalPanel'
import { useMarketAndSignals } from './hooks/useMarketAndSignals'
import { useAutoTrade } from './hooks/useAutoTrade'
import { AutoTradePanel } from './components/AutoTradePanel'
import { ManualTradeBar } from './components/ManualTradeBar'
import { ResearchTab } from './components/ResearchTab'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'
const TAB_KEY = 'v100_ui_tab_v1'

type TabId = 'chart' | 'trade' | 'research'

function loadTab(): TabId {
  try {
    const v = localStorage.getItem(TAB_KEY)
    if (v === 'trade' || v === 'research') return v
    return 'chart'
  } catch {
    return 'chart'
  }
}

export default function App() {
  const [tab, setTab] = useState<TabId>(() => loadTab())
  const trade = useAutoTrade(SYMBOL)

  const selectTab = (next: TabId) => {
    setTab(next)
    try {
      localStorage.setItem(TAB_KEY, next)
    } catch { /* ignore */ }
  }
  const {
    market, evaluations, latestEval, liveSignal, readyAlert, sound, setSound,
    liveSetup,
  } = useMarketAndSignals(trade.onTradeSignal, trade.minConfidencePct)

  const tradeConnected = trade.status === 'CONNECTED'

  return (
    <div className="flex h-screen min-h-[650px] flex-col bg-[#0b0f14] text-slate-100">
      <header className="flex h-9 shrink-0 items-center gap-3 border-b border-slate-800 px-3 text-sm">
        <span className="font-bold text-white">V100 Smart Signal Bot</span>
        <span className="hidden text-slate-500 sm:inline">R_100 · M1</span>

        <nav className="ml-2 flex items-center gap-1 rounded-md border border-slate-800 bg-[#0c1118] p-0.5 text-xs">
          <TabButton active={tab === 'chart'} onClick={() => selectTab('chart')}>Chart</TabButton>
          <TabButton active={tab === 'trade'} onClick={() => selectTab('trade')}>
            Trade settings
            {trade.autoEnabled && (
              <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" title="Auto trade ON" />
            )}
          </TabButton>
          <TabButton active={tab === 'research'} onClick={() => selectTab('research')}>Research</TabButton>
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
        if (pct >= trade.minConfidencePct) {
          return (
            <div
              className={`shrink-0 px-3 py-1 text-center text-xs font-semibold ${
                side === 'FALL'
                  ? 'bg-amber-500/20 text-amber-200'
                  : 'bg-cyan-500/20 text-cyan-200'
              }`}
            >
              PARTIAL {side} {pct}% · {pct >= 100 ? '5/5' : `${Math.round(pct / 20)}/5`} (trade level)
              {trade.autoEnabled ? ' · auto may fire' : ''}
            </div>
          )
        }
        if (readyAlert) {
          const near = readyAlert.near.slice(0, 2).join(' · ')
          return (
            <div className="shrink-0 bg-orange-600 px-3 py-1.5 text-center text-xs font-bold text-white">
              GET READY · {readyAlert.side} {readyAlert.pct}% ({readyAlert.passed}/{readyAlert.total})
              {' · '}alert coming — be ready
              {near ? ` · near: ${near}` : ''}
            </div>
          )
        }
        return null
      })()}

      {tab === 'chart' ? (
        <>
          <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px]">
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
              minConfidencePct={trade.minConfidencePct}
              sound={sound}
              onToggleSound={() => setSound((v) => !v)}
            />
          </div>
          <ManualTradeBar trade={trade} />
        </>
      ) : tab === 'trade' ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <AutoTradePanel trade={trade} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <ResearchTab market={market} />
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
