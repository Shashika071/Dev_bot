import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { TradingChart } from './components/TradingChart'
import { LiveEntryPanel } from './components/LiveEntryPanel'
import { useMarketAndSignals } from './hooks/useMarketAndSignals'
import { useAutoTrade } from './hooks/useAutoTrade'
import { useTimeframeContext } from './hooks/useTimeframeContext'
import { useDecisionJournal } from './hooks/useDecisionJournal'
import { AutoTradePanel } from './components/AutoTradePanel'
import { ManualTradeBar } from './components/ManualTradeBar'
import { ResearchTab } from './components/ResearchTab'
import { contextAt } from './lib/strategy/chartContext'
import { deriveEntryDisplay } from './lib/strategy/entryDisplay'
import { emptyMultiTimeframeContext } from './lib/timeframes/timeframeContext'
import { journalCandleTime } from './lib/journal/decisionJournal'

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

/** Directional agreement only for active 4/4 or exact 3/4 lean. */
function contextSide(
  entry: ReturnType<typeof deriveEntryDisplay>,
  liveSetup: ReturnType<typeof import('./lib/strategy/liveConfidence').computeLiveSetupConfidence>,
): 'RISE' | 'FALL' | null {
  const side = entry.side ?? entry.watchSide
  if (!side) return null
  if (entry.state !== 'WAIT') return entry.side
  const setup = side === 'RISE' ? liveSetup.rise : liveSetup.fall
  if (setup.passed === 3 && liveSetup.lean === side) return side
  return null
}

export default function App() {
  const [tab, setTab] = useState<TabId>(() => loadTab())
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000)
  const trade = useAutoTrade(SYMBOL)
  useEffect(() => {
    const timer = window.setInterval(() => setNowSec(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])

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

  const context = useMemo(
    () => market ? contextAt(market.candles, market.candles.length - 1, true) : null,
    [market],
  )
  const entry = useMemo(() => deriveEntryDisplay({
    market, liveSignal, liveSetup, signalMeta, nowSec,
    barrierAbs: trade.barrierAbs, duration: trade.duration, durationUnit: trade.durationUnit,
  }), [market, liveSignal, liveSetup, signalMeta, nowSec, trade.barrierAbs, trade.duration, trade.durationUnit])

  const m1ContextSide = useMemo(() => contextSide(entry, liveSetup), [entry, liveSetup])
  const timeframe = useTimeframeContext(market, m1ContextSide, nowSec)

  const displaySide = entry.side ?? entry.watchSide
  // A confirmed signal belongs to the candle that produced it, even though
  // liveSetup has already advanced to the next forming minute.
  const candleTime = journalCandleTime(entry.state, liveSignal?.time, liveSetup.candleTime)
  const journal = useDecisionJournal({
    entry,
    liveSetup,
    context: timeframe,
    side: displaySide,
    candleTime,
    price: market?.lastPrice ?? null,
    tickEpoch: market?.lastTickEpoch ?? null,
    nowSec,
  })
  const { recordSettlement } = journal
  const { setOnSettlement, acknowledgeSettlement, setQuoteSide } = trade

  useEffect(() => {
    setOnSettlement((row) => {
      const recorded = recordSettlement({
        attemptId: row.attemptId,
        accountId: row.accountId,
        execution: {
          contractId: row.contractId,
          entrySpot: row.entrySpot,
          barrier: row.barrier,
          buyPrice: row.buyPrice,
          payout: row.payout,
          profit: row.profit,
          status: row.status,
          currency: row.currency,
        },
      })
      if (recorded) acknowledgeSettlement(row.accountId, row.contractId)
    })
    return () => setOnSettlement(null)
  }, [setOnSettlement, acknowledgeSettlement, recordSettlement])

  // Keep a fresh display quote for the current watch/active side.
  useEffect(() => {
    const side = m1ContextSide ?? (entry.state !== 'WAIT' ? entry.side : null)
    setQuoteSide(side)
  }, [m1ContextSide, entry.state, entry.side, setQuoteSide])

  const tradeConnected = trade.status === 'CONNECTED'
  const quote = trade.liveQuote

  return (
    <div className="flex h-screen min-h-[650px] flex-col bg-[#0b0f14] text-slate-100">
      <header className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-800 px-3 py-2 text-sm">
        <span className="font-bold text-white">V100 Smart Signal Bot</span>
        <span className="hidden text-slate-500 sm:inline">R_100 · M1</span>

        <nav className="flex max-w-full flex-wrap items-center gap-1 rounded-md border border-slate-800 bg-[#0c1118] p-0.5 text-xs" aria-label="Main navigation">
          <TabButton active={tab === 'chart'} onClick={() => selectTab('chart')}>Chart</TabButton>
          <TabButton active={tab === 'trade'} onClick={() => selectTab('trade')}>
            Trade settings
            {trade.autoEnabled && (
              <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" title="Auto trade ON" />
            )}
          </TabButton>
          <TabButton active={tab === 'research'} onClick={() => selectTab('research')}>Research</TabButton>
        </nav>

        <div className="ml-auto hidden items-center gap-3 text-xs tabular-nums md:flex">
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

      <div className={`flex shrink-0 flex-wrap items-center justify-center gap-x-2 px-3 py-1.5 text-center text-xs ${
        entry.state === 'WAIT' ? 'bg-slate-800/70 text-slate-300'
          : entry.state === 'FORMING' ? 'bg-amber-400/10 text-amber-200'
            : entry.side === 'RISE' ? 'bg-emerald-400/10 text-emerald-200' : 'bg-rose-400/10 text-rose-200'
      }`}>
        <span className="font-semibold">C5 · {entry.state === 'WAIT' ? 'Wait' : `${entry.side} · ${entry.state === 'FORMING' ? 'Forming' : 'Confirmed'} 4/4`}</span>
        <span>{entry.state === 'WAIT' ? entry.reason : `${entry.expiresInSeconds.toFixed(0)}s left in signal window`}</span>
      </div>

      {tab === 'chart' ? (
        <>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:overflow-hidden">
            <main className="h-[55vh] min-h-[320px] min-w-0 shrink-0 lg:h-full lg:min-h-0">
              <TradingChart
                candles={market?.candles ?? []}
                evaluations={evaluations}
                lastPrice={market?.lastPrice ?? null}
                entry={entry}
              />
            </main>
            <div className="min-w-0 shrink-0 lg:min-h-0">
            <LiveEntryPanel
              market={market}
              eval_={latestEval}
              signal={liveSignal}
              liveSetup={liveSetup}
              entry={entry}
              context={context}
              timeframe={timeframe ?? emptyMultiTimeframeContext(m1ContextSide)}
              barrierAbs={trade.barrierAbs}
              sound={sound}
              onToggleSound={() => setSound(!sound)}
              quote={quote}
              canSkip
              onSkip={(reason) => journal.recordSkip(reason)}
            />
            </div>
          </div>
          <ManualTradeBar
            trade={trade}
            onBeforeManual={(side, attemptId) => {
              journal.recordManualClick(side, attemptId, quote.available ? {
                ask: quote.ask,
                payout: quote.payout,
                barrier: quote.barrier,
                breakEven: quote.breakEven,
                winGain: quote.winGain,
                maxLoss: quote.maxLoss,
              } : undefined)
            }}
            onManualFill={(fill, attemptId) => {
              journal.recordBuy({
                attemptId,
                accountId: fill.accountId,
                execution: {
                  contractId: fill.contractId,
                  entrySpot: fill.entrySpot,
                  barrier: fill.barrier,
                  buyPrice: fill.buyPrice,
                  payout: fill.payout,
                  profit: null,
                  status: 'open',
                  currency: fill.currency,
                },
              })
            }}
            onManualFailure={(attemptId) => journal.recordManualFailure(attemptId)}
          />
        </>
      ) : tab === 'trade' ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <AutoTradePanel trade={trade} />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <ResearchTab
            market={market}
            journalSummary={journal.summary}
            journal={journal.journal}
            onDownloadJournal={journal.downloadCsv}
          />
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
