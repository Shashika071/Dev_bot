import { useEffect, useMemo, useRef, useState } from 'react'
import { MarketFeed, SIGNAL_WINDOW_SECONDS, type MarketState } from '../lib/deriv/marketFeed'
import {
  evaluateC5All,
  evaluateC5LatestClosed,
  evaluateC5LiveBar,
  C5_STRATEGY_ID,
} from '../lib/strategy/c5Strategy'
import { computeLiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { SignalEvaluation } from '../lib/types'
import type { AutoTradeSignal } from './useAutoTrade'

const SYMBOL = import.meta.env.VITE_DERIV_SYMBOL || 'R_100'

/** Shared AudioContext — browsers block beep until unlocked by a user click. */
let alertAudioCtx: AudioContext | null = null

function getAlertAudioCtx(): AudioContext | null {
  try {
    const AC = window.AudioContext
      || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
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

/** Soft beep for qualified C5 alerts. */
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

function toAutoSignal(
  ev: SignalEvaluation,
  state: MarketState,
  opts: { candleCloseEpoch: number; expiresAt: number; feedReady: boolean; forming?: boolean },
): AutoTradeSignal {
  const side = ev.final as 'RISE' | 'FALL'
  return {
    strategyId: C5_STRATEGY_ID,
    side,
    symbol: state.symbol || SYMBOL,
    candleTime: ev.time,
    time: ev.time,
    candleCloseEpoch: opts.candleCloseEpoch,
    expiresAt: opts.expiresAt,
    // Closed entries expire on the candle clock; forming entries get the entry
    // window from detection, so their deadline is in local receipt time.
    receivedTickEpoch: opts.forming ? undefined : state.lastTickEpoch ?? undefined,
    isQualified: true,
    requiredPassed: ev.requiredPassed ?? 4,
    requiredTotal: ev.requiredTotal ?? 4,
    flags: ev.confirmations,
    confidence: (ev.requiredPassed ?? 4) / 4,
    feedReady: opts.feedReady,
    indicators: {
      close: ev.close,
      smaRising: ev.smaRising,
      smaFalling: ev.smaFalling,
      ao: ev.ao,
      rsi: ev.rsi,
      stDir: ev.stDir,
      rawUt: ev.rawUt,
    },
  }
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
    let expiresAt = 0
    let lastTickSeen: number | null = null
    const notified = new Set<string>()

    const emitQualified = (
      state: MarketState,
      ev: SignalEvaluation,
      opts: { candleCloseEpoch: number; expiresAt: number; playSound: boolean; forming?: boolean },
    ) => {
      if (ev.final !== 'RISE' && ev.final !== 'FALL') return
      const id = `${C5_STRATEGY_ID}:${ev.time}:${ev.final}`
      if (notified.has(id)) return
      notified.add(id)

      const nowSec = Date.now() / 1000
      const entryTime = opts.forming ? nowSec : Math.max(nowSec, state.lastTickEpoch ?? nowSec)
      const inWindow = state.ready && entryTime <= opts.expiresAt
      if (inWindow) {
        expiresAt = opts.expiresAt
        setLiveSignal(ev)
        setSignalMeta({
          candleCloseEpoch: opts.candleCloseEpoch,
          expiresAt: opts.expiresAt,
          remainingSec: Math.max(0, opts.expiresAt - nowSec),
        })
        if (opts.playSound && soundRef.current) playBeep()
      }
      onTradeRef.current?.(toAutoSignal(ev, state, {
        candleCloseEpoch: opts.candleCloseEpoch,
        expiresAt: opts.expiresAt,
        feedReady: state.ready,
        forming: opts.forming,
      }))
    }

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

      // Completed-candle path (backup if 4/4 only appears at the close).
      if (event?.type === 'candle_close') {
        const ev = evaluateC5LatestClosed(state.candles)
        if (ev && ev.time === event.candle.time && ev.isQualified) {
          const candleCloseEpoch = ev.time + 60
          emitQualified(state, ev, {
            candleCloseEpoch,
            expiresAt: candleCloseEpoch + SIGNAL_WINDOW_SECONDS,
            playSound: true,
          })
        }
      }

      // Immediate forming path: first time this minute hits C5 4/4 → alert + Auto.
      // Skip the first tick after history load so reconnect bootstrap cannot buy.
      const tickEpoch = state.lastTickEpoch
      if (tickEpoch != null && tickEpoch !== lastTickSeen) {
        const isFirstTick = lastTickSeen == null
        lastTickSeen = tickEpoch
        const last = state.candles.at(-1)
        if (!isFirstTick && state.ready && last && !last.isClosed) {
          const ev = evaluateC5LiveBar(state.candles)
          if (ev && ev.time === last.time && ev.isQualified) {
            emitQualified(state, ev, {
              candleCloseEpoch: nowSec,
              expiresAt: nowSec + SIGNAL_WINDOW_SECONDS,
              playSound: true,
              forming: true,
            })
          }
        }
      }

      // Deliver fresh signals before the more expensive historical chart scan.
      const key = `${state.ready}:${state.lastConfirmedCandleTime}:${state.candles[0]?.time}`
      if (state.ready && key !== evaluationKey) {
        setEvaluations(evaluateC5All(state.candles.filter((c) => c.isClosed)))
      }
      evaluationKey = key
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
