import { useEffect, useMemo, useRef, useState } from 'react'
import { MarketFeed, SIGNAL_WINDOW_SECONDS, type MarketState } from '../lib/deriv/marketFeed'
import { evaluateAll } from '../lib/strategy/signalEngine'
import { computeLiveSetupConfidence, READY_ALERT_PCT } from '../lib/strategy/liveConfidence'
import type { SignalEvaluation } from '../lib/types'
import type { AutoTradeSignal } from './useAutoTrade'

export type ReadyAlert = {
  side: 'RISE' | 'FALL'
  pct: number
  passed: number
  total: number
  near: string[]
}

/** Soft beep for full 5/5 qualified alerts. */
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

/** “GET READY” warning at ~60% — two mid tones. */
function playReadyAlert() {
  try {
    const ctx = new AudioContext()
    const now = ctx.currentTime
    const tones = [740, 880]
    for (let i = 0; i < tones.length; i++) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'triangle'
      osc.frequency.value = tones[i]!
      osc.connect(gain)
      gain.connect(ctx.destination)
      const t0 = now + i * 0.22
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(0.18, t0 + 0.04)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.2)
      osc.start(t0)
      osc.stop(t0 + 0.21)
    }
    window.setTimeout(() => { void ctx.close() }, 700)
  } catch { /* Sound may require a browser interaction. */ }
}

/** Louder multi-tone alert when confidence hits ≥80% (partial setup). */
function playBigAlert() {
  try {
    const ctx = new AudioContext()
    const now = ctx.currentTime
    const tones = [988, 1319, 1568]
    for (let i = 0; i < tones.length; i++) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = tones[i]!
      osc.connect(gain)
      gain.connect(ctx.destination)
      const t0 = now + i * 0.2
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(0.28, t0 + 0.03)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18)
      osc.start(t0)
      osc.stop(t0 + 0.19)
    }
    window.setTimeout(() => { void ctx.close() }, 900)
  } catch { /* Sound may require a browser interaction. */ }
}

export function useMarketAndSignals(
  onTradeSignal?: (sig: AutoTradeSignal) => void,
  minConfidencePct = 80,
) {
  const [market, setMarket] = useState<MarketState | null>(null)
  const [evaluations, setEvaluations] = useState<SignalEvaluation[]>([])
  const [liveSignal, setLiveSignal] = useState<SignalEvaluation | null>(null)
  const [readyAlert, setReadyAlert] = useState<ReadyAlert | null>(null)
  const [sound, setSound] = useState(() => (import.meta.env.VITE_SOUND_ALERTS ?? 'true') !== 'false')
  const soundRef = useRef(sound)
  const onTradeRef = useRef(onTradeSignal)
  const minConfRef = useRef(minConfidencePct)

  useEffect(() => { soundRef.current = sound }, [sound])
  useEffect(() => { onTradeRef.current = onTradeSignal }, [onTradeSignal])
  useEffect(() => { minConfRef.current = minConfidencePct }, [minConfidencePct])

  useEffect(() => {
    const feed = new MarketFeed()
    let evaluationKey = ''
    let currentEvals: SignalEvaluation[] = []
    let expiresAt = 0
    const notified = new Set<string>()
    const sounded80 = new Set<string>()
    const sounded60 = new Set<string>()
    const unsubscribe = feed.subscribe((state, event) => {
      setMarket({ ...state })
      if (!state.ready || Date.now() / 1000 > expiresAt) setLiveSignal(null)

      const key = `${state.ready}:${state.lastConfirmedCandleTime}:${state.candles[0]?.time}`
      if (state.ready && key !== evaluationKey) {
        const closed = state.candles.filter((c) => c.isClosed)
        currentEvals = evaluateAll(closed)
        setEvaluations(currentEvals)
      }
      evaluationKey = key
      if (state.ready && event?.type === 'candle_close') {
        const ev = currentEvals.at(-1)
        if (ev && ev.time === event.candle.time && ev.rawUt !== 'NONE') {
          const id = `${ev.time}:${ev.rawUt}:${ev.final}`
          if (!notified.has(id)) {
            notified.add(id)
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
            const confPct = Math.round(100 * confidence)
            const soundKey = `${setup.candleTime}:${side}`
            const setupSide = side === 'RISE' ? setup.rise : setup.fall
            const nearTexts = setupSide.nearRemaining.map((n) => n.text)
            const hasNear = nearTexts.length > 0

            // GET READY: ≥60% and still below trade threshold, especially if remaining are near.
            if (
              confPct >= READY_ALERT_PCT
              && confPct < minConfRef.current
            ) {
              setReadyAlert({
                side,
                pct: confPct,
                passed: setupSide.passed,
                total: setupSide.total,
                near: nearTexts,
              })
              if (
                soundRef.current
                && !sounded60.has(soundKey)
                && (hasNear || confPct >= READY_ALERT_PCT)
              ) {
                sounded60.add(soundKey)
                playReadyAlert()
              }
            } else if (confPct < READY_ALERT_PCT) {
              setReadyAlert(null)
            } else {
              // At/above trade threshold — clear ready banner (partial/full takes over).
              setReadyAlert(null)
            }

            if (
              soundRef.current
              && confPct >= minConfRef.current
              && !sounded80.has(soundKey)
            ) {
              sounded80.add(soundKey)
              playBigAlert()
            }
            onTradeRef.current?.({
              side,
              confidence,
              time: setup.candleTime,
              isQualified: confidence >= 1,
            })
          } else {
            setReadyAlert(null)
          }
        }
      } else if (!state.ready) {
        setReadyAlert(null)
      }
    })
    feed.start()
    return () => { unsubscribe(); feed.stop() }
  }, [])

  // Recompute every tick so forming-candle conditions stay live.
  const liveSetup = useMemo(
    () => computeLiveSetupConfidence(market?.candles ?? []),
    [market?.candles, market?.lastPrice, market?.lastTickEpoch],
  )

  return {
    market, evaluations, latestEval: evaluations.at(-1) ?? null, liveSignal,
    readyAlert,
    sound, setSound,
    liveSetup,
  }
}
