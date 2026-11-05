import { useEffect, useMemo, useRef, useState } from 'react'
import { MarketFeed, SIGNAL_WINDOW_SECONDS, type MarketState } from '../lib/deriv/marketFeed'
import { evaluateC5All, evaluateC5LatestClosed, C5_STRATEGY_ID } from '../lib/strategy/c5Strategy'
import { computeLiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { SignalEvaluation } from '../lib/types'
import type { AutoTradeSignal } from './useAutoTrade'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'

/** Shared AudioContext — browsers block beep until unlocked by a user click. */
let alertAudioCtx: AudioContext | null = null

function getAlertAudioCtx(): AudioContext | null {
  try {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AC) return null
    if (!alertAudioCtx || alertAudioCtx.state === 'closed') alertAudioCtx = new AC()
    return alertAudioCtx
  } catch {
    return null
  }
}

/** Call from a click (sound checkbox) so later C5 alerts are allowed to beep. */
export async function unlockC5AlertSound(): Promise<void> {
  const ctx = getAlertAudioCtx()
  if (!ctx) return
  try {
    if (ctx.state === 'suspended') await ctx.resume()
  } catch { /* ignore */ }
}

/** Soft beep for qualified C5 completed-candle alerts. */
function playBeep() {
  try {
    const ctx = getAlertAudioCtx()
    if (!ctx) return
    void ctx.resume().then(() => {
      const oscillator = ctx.createOscillator()
      const gain = ctx.createGain()
      oscillator.connect(gain)
      gain.connect(ctx.destination)
      oscillator.frequency.value = 880
      gain.gain.value = 0.05
      oscillator.start()
      oscillator.stop(ctx.currentTime + 0.15)
    }).catch(() => { /* autoplay still blocked */ })
  } catch { /* Sound may require a browser interaction. */ }
}

export function useMarketAndSignals(onTradeSignal?: (sig: AutoTradeSignal) => void) {
  const [market, setMarket] = useState<MarketState | null>(null)
  const [evaluations, setEvaluations] = useState<SignalEvaluation[]>([])
  const [liveSignal, setLiveSignal] = useState<SignalEvaluation | null>(null)
  const [signalMeta, setSignalMeta] = useState<{
    candleCloseEpoch: number
    expiresAt: number
    remainingSec: number
  } | null>(null)
  const [sound, setSound] = useState(() => (import.meta.env.VITE_SOUND_ALERTS ?? 'true') !== 'false')
  const soundRef = useRef(sound)
  const onTradeRef = useRef(onTradeSignal)

  useEffect(() => { soundRef.current = sound }, [sound])
  useEffect(() => { onTradeRef.current = onTradeSignal }, [onTradeSignal])

  const setSoundSafe = (on: boolean) => {
    setSound(on)
    if (on) void unlockC5AlertSound()
  }

  useEffect(() => {
    const feed = new MarketFeed()
    let evaluationKey = ''
    let currentEvals: SignalEvaluation[] = []
    let expiresAt = 0
    const notified = new Set<string>()
    const unsubscribe = feed.subscribe((state, event) => {
      setMarket({ ...state })
      const nowSec = Date.now() / 1000
      if (!state.ready || nowSec > expiresAt) {
        setLiveSignal(null)
        setSignalMeta(null)
      } else if (expiresAt > 0) {
        setSignalMeta((prev) => prev
          ? { ...prev, remainingSec: Math.max(0, expiresAt - nowSec) }
          : prev)
      }

      const key = `${state.ready}:${state.lastConfirmedCandleTime}:${state.candles[0]?.time}`
      if (state.ready && key !== evaluationKey) {
        const closed = state.candles.filter((c) => c.isClosed)
        currentEvals = evaluateC5All(closed)
        setEvaluations(currentEvals)
      }
      evaluationKey = key

      // Fresh completed-candle C5 only — never bootstrap/reconnect/forming history redraw.
      // Always forward qualified closes to Auto (even if not READY / past 5s) so Trade history
      // logs the skip; chart arrows alone previously looked like "Auto failed".
      if (event?.type === 'candle_close') {
        const ev = evaluateC5LatestClosed(state.candles)
        if (ev && ev.time === event.candle.time && ev.isQualified && (ev.final === 'RISE' || ev.final === 'FALL')) {
          const candleCloseEpoch = ev.time + 60
          const signalExpiresAt = candleCloseEpoch + SIGNAL_WINDOW_SECONDS
          const id = `${C5_STRATEGY_ID}:${ev.time}:${ev.final}`
          if (!notified.has(id)) {
            notified.add(id)
            const inWindow = state.ready && nowSec <= signalExpiresAt
            if (inWindow) {
              expiresAt = signalExpiresAt
              setLiveSignal(ev)
              setSignalMeta({
                candleCloseEpoch,
                expiresAt: signalExpiresAt,
                remainingSec: Math.max(0, signalExpiresAt - nowSec),
              })
              if (soundRef.current) playBeep()
            }
            const flags = ev.confirmations
            onTradeRef.current?.({
              strategyId: C5_STRATEGY_ID,
              side: ev.final,
              symbol: state.symbol || SYMBOL,
              candleTime: ev.time,
              time: ev.time,
              candleCloseEpoch,
              expiresAt: signalExpiresAt,
              isQualified: true,
              requiredPassed: ev.requiredPassed ?? 4,
              requiredTotal: ev.requiredTotal ?? 4,
              flags,
              confidence: (ev.requiredPassed ?? 4) / 4,
              feedReady: state.ready,
              indicators: {
                close: ev.close,
                smaRising: ev.smaRising,
                smaFalling: ev.smaFalling,
                ao: ev.ao,
                rsi: ev.rsi,
                stDir: ev.stDir,
                rawUt: ev.rawUt,
              },
            })
          }
        }
      }
    })
    feed.start()
    return () => { unsubscribe(); feed.stop() }
  }, [])

  const liveSetup = useMemo(
    () => computeLiveSetupConfidence(market?.candles ?? []),
    [market?.candles, market?.lastPrice, market?.lastTickEpoch],
  )

  return {
    market,
    evaluations,
    latestEval: evaluations.at(-1) ?? null,
    liveSignal,
    signalMeta,
    sound,
    setSound: setSoundSafe,
    liveSetup,
    readyAlert: null as null,
  }
}
