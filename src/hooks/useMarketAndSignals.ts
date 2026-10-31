import { useEffect, useMemo, useRef, useState } from 'react'
import { MarketFeed, SIGNAL_WINDOW_SECONDS, type MarketState } from '../lib/deriv/marketFeed'
import { getStorageError, loadHistory, saveHistory } from '../lib/history/signalStore'
import { advancePaper, startPaper } from '../lib/history/paperTracker'
import { evaluateAll } from '../lib/strategy/signalEngine'
import { computeLiveSetupConfidence } from '../lib/strategy/liveConfidence'
import { summarize } from '../lib/research/statistics'
import type { Candle, SignalEvaluation, SignalHistoryRow } from '../lib/types'
import type { AutoTradeSignal } from './useAutoTrade'

function playBeep() {
  try {
    const ctx = new AudioContext()
    const oscillator = ctx.createOscillator()
    const gain = ctx.createGain()
    oscillator.connect(gain)
    gain.connect(ctx.destination)
    oscillator.frequency.value = 880
    gain.gain.value = 0.05
    oscillator.onended = () => { void ctx.close() }
    oscillator.start()
    oscillator.stop(ctx.currentTime + 0.15)
  } catch { /* Sound may require a browser interaction. */ }
}

export function useMarketAndSignals(onTradeSignal?: (sig: AutoTradeSignal) => void) {
  const [market, setMarket] = useState<MarketState | null>(null)
  const [history, setHistory] = useState(() => advancePaper(loadHistory(), null, false))
  const rowsRef = useRef(history)
  const [evaluations, setEvaluations] = useState<SignalEvaluation[]>([])
  const [closedCandles, setClosedCandles] = useState<Candle[]>([])
  const [liveSignal, setLiveSignal] = useState<SignalEvaluation | null>(null)
  const [sound, setSound] = useState(() => (import.meta.env.VITE_SOUND_ALERTS ?? 'true') !== 'false')
  const soundRef = useRef(sound)
  const onTradeRef = useRef(onTradeSignal)
  const [storageWarning, setStorageWarning] = useState<string | null>(null)
  const [netWinReturn, setNetWinReturn] = useState(0.8)

  useEffect(() => { soundRef.current = sound }, [sound])
  useEffect(() => { onTradeRef.current = onTradeSignal }, [onTradeSignal])

  useEffect(() => {
    const feed = new MarketFeed()
    let evaluationKey = ''
    let currentEvals: SignalEvaluation[] = []
    let expiresAt = 0
    // Keep deduplication for this session even if the visible history is cleared.
    const notified = new Set(rowsRef.current.map((row) => row.id))
    saveHistory(rowsRef.current)
    const unsubscribe = feed.subscribe((state, event) => {
      setMarket({ ...state })
      const tick = state.lastTickEpoch != null && state.lastPrice != null
        ? { epoch: state.lastTickEpoch, quote: state.lastPrice, symbol: state.symbol } : null
      let rows = advancePaper(rowsRef.current, tick, state.ready && state.status === 'CONNECTED')
      if (!state.ready || Date.now() / 1000 > expiresAt) setLiveSignal(null)

      const key = `${state.ready}:${state.lastConfirmedCandleTime}:${state.candles[0]?.time}`
      if (state.ready && key !== evaluationKey) {
        const closed = state.candles.filter((c) => c.isClosed)
        currentEvals = evaluateAll(closed)
        setEvaluations(currentEvals)
        setClosedCandles(closed)
      }
      evaluationKey = key
      if (state.ready && tick && event?.type === 'candle_close') {
        const ev = currentEvals.at(-1)
        if (ev && ev.time === event.candle.time && ev.rawUt !== 'NONE') {
          const id = `${ev.time}:${ev.rawUt}:${ev.final}`
          if (!notified.has(id)) {
            notified.add(id)
            const row: SignalHistoryRow = {
              id, timestamp: ev.time, notifyEpoch: tick.epoch,
              signal: ev.isQualified ? ev.final : ev.rawUt === 'BUY' ? 'UT_BUY_REJECTED' : 'UT_SELL_REJECTED',
              candleClose: ev.close, notifyPrice: tick.quote, stDir: ev.stDir,
              smaDir: ev.smaRising ? 'BULLISH' : ev.smaFalling ? 'BEARISH' : 'FLAT',
              ao: ev.ao, rsi: ev.rsi, connection: state.status, failures: ev.failures,
              isQualified: ev.isQualified,
              paper: ev.isQualified ? startPaper(tick.epoch, tick.quote) : undefined,
            }
            rows = [...rows, row].slice(-2000)
            if (ev.isQualified) {
              expiresAt = ev.time + 60 + SIGNAL_WINDOW_SECONDS
              setLiveSignal(ev)
              if (soundRef.current) playBeep()
            }
          }
        }
      }

      // Auto-trade: confidence ≥ min (default 80%) — no UT required.
      // Uses live setup on the current minute; once per candle side.
      if (state.ready) {
        const setup = computeLiveSetupConfidence(state.candles)
        if (setup.ready && setup.candleTime != null) {
          const rise = setup.rise.confidence
          const fall = setup.fall.confidence
          let side: 'RISE' | 'FALL' | null = null
          let confidence = 0
          if (rise > fall) {
            side = 'RISE'
            confidence = rise
          } else if (fall > rise) {
            side = 'FALL'
            confidence = fall
          } else if (rise > 0 && setup.lean === 'RISE') {
            side = 'RISE'
            confidence = rise
          } else if (fall > 0 && setup.lean === 'FALL') {
            side = 'FALL'
            confidence = fall
          } else if (rise >= fall && rise > 0) {
            side = 'RISE'
            confidence = rise
          }
          if (side) {
            onTradeRef.current?.({
              side,
              confidence,
              time: setup.candleTime,
              isQualified: confidence >= 1,
            })
          }
        }
      }

      if (rows !== rowsRef.current) {
        rowsRef.current = rows
        setHistory(rows)
        saveHistory(rows)
      }
      setStorageWarning(getStorageError())
    })
    feed.start()
    return () => { unsubscribe(); feed.stop() }
  }, [])

  const research = useMemo(() => {
    const byTime = new Map(closedCandles.map((c) => [c.time, c]))
    const outcomes: boolean[] = []
    for (const ev of evaluations) {
      const next = byTime.get(ev.time + 60)
      if (!ev.isQualified || !next) continue
      outcomes.push(ev.final === 'RISE' ? next.close > ev.close : next.close < ev.close)
    }
    const observed = history.filter((r) => r.paper?.status === 'WIN' || r.paper?.status === 'LOSS')
    return {
      bars: closedCandles.length,
      historical: summarize(outcomes, netWinReturn),
      paper: summarize(observed.map((r) => r.paper?.status === 'WIN'), netWinReturn),
      pending: history.filter((r) => r.paper?.status === 'PENDING').length,
      invalid: history.filter((r) => r.paper?.status === 'INVALID').length,
    }
  }, [closedCandles, evaluations, history, netWinReturn])

  // Recompute every tick so forming-candle conditions stay live.
  const liveSetup = useMemo(
    () => computeLiveSetupConfidence(market?.candles ?? []),
    [market?.candles, market?.lastPrice, market?.lastTickEpoch],
  )

  function clearHistory() {
    // Preserve in-flight observations so clearing the table cannot erase losing positions.
    const pending = rowsRef.current.filter((row) => row.paper?.status === 'PENDING')
    rowsRef.current = pending
    setHistory(pending)
    saveHistory(pending)
    setStorageWarning(getStorageError())
  }

  return {
    market, evaluations, latestEval: evaluations.at(-1) ?? null, liveSignal,
    history, clearHistory, sound, setSound, research, storageWarning, netWinReturn, setNetWinReturn,
    liveSetup,
  }
}
