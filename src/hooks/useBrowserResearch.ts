import { useEffect, useMemo, useRef, useState } from 'react'
import type { MarketState } from '../lib/deriv/marketFeed'
import {
  createInitialState,
  onTick,
  resumeCollecting,
  startDiscovery,
  type ExperimentState,
} from '../lib/research/experimentEngine'
import { summarizeExperiment, exportObservationsCsv, exportReportMarkdown } from '../lib/research/summarizeExperiment'
import { STRATEGY_REGISTRY } from '../lib/research/strategies/registry'
import { loadBrowserResearch, saveBrowserResearch } from '../lib/research/browserResearchStore'
import type { StrategyId } from '../lib/research/strategies/types'

/**
 * In-browser research runner. Uses the same public MarketFeed already open in the app.
 * No localhost collector required for deployed sites.
 */
export function useBrowserResearch(market: MarketState | null) {
  const [state, setState] = useState<ExperimentState>(() => loadBrowserResearch())
  const stateRef = useRef(state)
  const lastKey = useRef('')

  useEffect(() => { stateRef.current = state }, [state])

  useEffect(() => {
    if (!market?.lastTickEpoch || market.lastPrice == null) return
    const key = `${market.lastTickEpoch}:${market.lastPrice}:${market.ready}:${market.candles.length}`
    if (key === lastKey.current) return
    lastKey.current = key

    const tick = {
      epoch: market.lastTickEpoch,
      quote: market.lastPrice,
      symbol: market.symbol,
    }
    const next = onTick(
      stateRef.current,
      tick,
      market.candles,
      Date.now() / 1000,
      !!(market.ready && market.status === 'CONNECTED'),
    )
    if (next === stateRef.current) return
    stateRef.current = next
    setState(next)
    saveBrowserResearch(next)
  }, [market?.lastTickEpoch, market?.lastPrice, market?.ready, market?.status, market?.candles, market?.symbol])

  const summary = useMemo(() => summarizeExperiment(state), [state])

  function start() {
    const next = startDiscovery(createInitialState(), Date.now())
    stateRef.current = next
    setState(next)
    saveBrowserResearch(next)
  }

  function resume() {
    const next = resumeCollecting(stateRef.current)
    stateRef.current = next
    setState(next)
    saveBrowserResearch(next)
  }

  function stopEntries() {
    const next = { ...stateRef.current, stage: 'finishing' as const }
    stateRef.current = next
    setState(next)
    saveBrowserResearch(next)
  }

  function startConfirmation(ids?: StrategyId[]) {
    const enabled = ids ?? stateRef.current.enabledStrategyIds
    let next = startDiscovery(createInitialState('confirmation', enabled), Date.now())
    next = { ...next, mode: 'confirmation' }
    stateRef.current = next
    setState(next)
    saveBrowserResearch(next)
  }

  function downloadCsv() {
    const blob = new Blob([exportObservationsCsv(stateRef.current)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'research-observations.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  function downloadReport() {
    const blob = new Blob([exportReportMarkdown(summary)], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'research-REPORT.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  function downloadSummary() {
    const blob = new Blob([JSON.stringify(summary, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'research-summary.json'
    a.click()
    URL.revokeObjectURL(url)
  }

  return {
    state,
    summary,
    registry: STRATEGY_REGISTRY,
    live: !!(market?.ready && market.status === 'CONNECTED'),
    start,
    resume,
    stopEntries,
    startConfirmation,
    downloadCsv,
    downloadReport,
    downloadSummary,
  }
}
