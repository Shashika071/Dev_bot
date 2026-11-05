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
    market, evaluations, latestEval, liveSignal, signalMeta, sound, setSound,
    liveSetup,
  } = useMarketAndSignals(trade.onTradeSignal)

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
          <span className="hidden text-slate-400 sm:inline">C5</span>
          <span className="text-emerald-400">RISE {liveSetup.rise.passed}/{liveSetup.rise.total}</span>
          <span className="text-rose-400">FALL {liveSetup.fall.passed}/{liveSetup.fall.total}</span>
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

      {liveSignal?.isQualified && (liveSignal.final === 'RISE' || liveSignal.final === 'FALL') && (
        <div
          className={`shrink-0 px-3 py-1 text-center text-xs font-bold text-white ${
            liveSignal.final === 'FALL' ? 'bg-rose-600' : 'bg-emerald-600'
          }`}
        >
          C5 {liveSignal.final} · 4/4 · entry window
          {signalMeta ? ` · ${signalMeta.remainingSec.toFixed(0)}s left` : ''}
          {trade.autoEnabled ? ' · auto Touch firing' : ''}
        </div>
      )}
      {!liveSignal && liveSetup.forming && liveSetup.lean !== 'NONE' && (
        <div className="shrink-0 bg-slate-800 px-3 py-1 text-center text-xs font-semibold text-slate-300">
          Forming · {liveSetup.lean} {liveSetup.lean === 'RISE' ? liveSetup.rise.passed : liveSetup.fall.passed}/4
          {((liveSetup.lean === 'RISE' ? liveSetup.rise.passed : liveSetup.fall.passed) >= 4)
            ? ' · waiting / already signaled'
            : ' required'}
        </div>
      )}

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
              signalMeta={signalMeta}
              sound={sound}
              onToggleSound={() => setSound(!sound)}
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
