import { useState } from 'react'
import { MAX_TICK_GAP_SECONDS, type MarketState } from '../lib/deriv/marketFeed'
import type { LiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { ChartContext } from '../lib/strategy/chartContext'
import type { EntryDisplay } from '../lib/strategy/entryDisplay'
import type { SignalEvaluation } from '../lib/types'
import type { MultiTimeframeContext } from '../lib/timeframes/timeframeContext'
import { buildDecisionSummary } from '../lib/timeframes/decisionSummary'
import type { QuoteDisplay } from '../lib/deriv/quoteEconomics'
import { formatBreakEven } from '../lib/deriv/quoteEconomics'
import { TimeframeContextCard } from './TimeframeContextCard'

type Props = {
  market: MarketState | null
  eval_: SignalEvaluation | null
  signal: SignalEvaluation | null
  liveSetup: LiveSetupConfidence
  entry: EntryDisplay
  context: ChartContext | null
  timeframe: MultiTimeframeContext
  barrierAbs: number
  sound: boolean
  onToggleSound: () => void
  quote: QuoteDisplay
  onSkip?: (reason: string) => void
  canSkip?: boolean
}

const price = (value: number | null | undefined) => value == null ? '—' : value.toFixed(2)

const SKIP_SUGGESTIONS = ['M15 resistance', 'M5 mixed', 'late entry', 'weak quote']

export function LiveEntryPanel({
  market, eval_, signal, liveSetup, entry, context, timeframe,
  barrierAbs, sound, onToggleSound, quote, onSkip, canSkip = false,
}: Props) {
  const active = entry.state !== 'WAIT'
  const side = entry.side ?? entry.watchSide
  const currentSetup = side === 'RISE' ? liveSetup.rise : side === 'FALL' ? liveSetup.fall : null
  const setup = entry.state === 'CONFIRMED' && signal && currentSetup ? {
    ...currentSetup,
    requiredLabels: currentSetup.requiredLabels.map(row => ({ ...row, ok: signal.confirmations[row.key] })),
    aoOptional: { ...currentSetup.aoOptional, ok: signal.confirmations.ao, value: signal.ao },
  } : currentSetup
  const fresh = market?.status === 'CONNECTED' && market.ready && entry.tickAgeSeconds != null && entry.tickAgeSeconds <= MAX_TICK_GAP_SECONDS && entry.tickAgeSeconds >= -2
  const tone = !active ? 'border-slate-700 bg-slate-900/60' : entry.state === 'FORMING' ? 'border-amber-400/40 bg-amber-400/5' : entry.side === 'RISE' ? 'border-emerald-400/40 bg-emerald-400/5' : 'border-rose-400/40 bg-rose-400/5'
  const location = context?.locationPct == null ? null : Math.max(0, Math.min(100, context.locationPct))
  const regime = context?.chop14 == null ? 'Unavailable' : context.chop14 < 38.2 ? 'Directional recently' : context.chop14 > 61.8 ? 'Choppy recently' : 'Mixed movement'
  const decision = buildDecisionSummary({ entry, liveSetup, side, context: timeframe })
  const watching34 = entry.state === 'WAIT' && !!side && currentSetup?.passed === 3
  const [skipReason, setSkipReason] = useState('')

  const c5Headline = active
    ? `M1 C5 · ${entry.side} 4/4 · ${entry.state === 'FORMING' ? 'Forming' : 'Confirmed'}`
    : watching34
      ? `M1 C5 · ${side} 3/4 · Watching`
      : entry.state === 'WAIT' && side
        ? `M1 C5 · Watching ${side}`
        : 'M1 C5 · Waiting'

  return <aside className="flex h-full min-h-0 flex-col gap-4 overflow-auto border-l border-slate-800 bg-[#0c131d] p-4 text-sm">
    <div className="flex items-start justify-between gap-2">
      <div><p className="text-xs uppercase tracking-[0.18em] text-sky-300">Live market read</p><h2 className="mt-1 text-lg font-semibold text-white">Your C5 entry view</h2></div>
      <span className={`mt-1 rounded-full px-2 py-1 text-xs ${fresh ? 'bg-emerald-400/10 text-emerald-300' : 'bg-amber-400/10 text-amber-300'}`}>{fresh ? '● Live' : '● Paused'}</span>
    </div>

    <section className={`rounded-2xl border p-4 ${tone}`} aria-label="C5 entry status">
      <div className="flex items-center justify-between gap-2 text-xs text-slate-400">
        <span>{c5Headline}</span>
        {active && <span className="font-mono text-slate-200">{entry.expiresInSeconds.toFixed(0)}s</span>}
      </div>
      <p className="mt-1 text-[11px] text-slate-500">{entry.state === 'CONFIRMED' ? 'Completed candle · C5 4/4' : entry.state === 'FORMING' ? 'Forming candle · C5 4/4' : watching34 ? 'Watching · not an entry yet' : 'Waiting for a current setup'}</p>
      <div className={`mt-3 text-3xl font-semibold tracking-tight ${active ? entry.side === 'RISE' ? 'text-emerald-300' : 'text-rose-300' : 'text-slate-200'}`}>
        {active ? `${entry.side === 'RISE' ? '↗' : '↘'} ${entry.side} setup` : watching34 ? `${side} · Watching` : 'Wait'}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-300" role="status">{decision.headline}</p>
      <div className="mt-3 space-y-1.5 rounded-lg border border-slate-700/50 bg-slate-950/40 p-3" aria-label="Decision summary">
        <p className="text-xs text-emerald-200/90">{decision.forSide}</p>
        <p className="text-xs text-amber-200/90">{decision.againstSide}</p>
        <p className="text-[11px] text-slate-500">{decision.disclaimer}</p>
      </div>
      {active && <>
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-700/60 pt-3">
          <div><p className="text-xs text-slate-400">Entry reference</p><p className="mt-1 font-mono text-lg text-white">{price(entry.entryReference)}</p></div>
          <div><p className="text-xs text-slate-400">Indicative touch level</p><p className="mt-1 font-mono text-lg text-violet-300">{price(entry.target)}</p></div>
        </div>
        <p className="mt-2 text-xs text-slate-400">Signal reference {price(entry.signalPrice)} · {entry.side === 'RISE' ? '+' : '−'}{barrierAbs} · {entry.durationLabel}</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-500">References update with ticks. The broker quote sets the actual entry and barrier.</p>
      </>}
      <div className="mt-3 border-t border-slate-700/60 pt-3" aria-label="Quote payoff">
        <p className="text-xs font-medium text-slate-300">Current quote</p>
        {quote.available ? (
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
            <div><span className="text-slate-500">If it wins</span><p className="font-mono text-emerald-300">+{quote.winGain.toFixed(2)}</p></div>
            <div><span className="text-slate-500">If it misses</span><p className="font-mono text-rose-300">−{quote.maxLoss.toFixed(2)}</p></div>
            <div className="col-span-2"><span className="text-slate-500">Break-even win rate</span><p className="font-mono text-slate-200">{formatBreakEven(quote.breakEven)} <span className="text-slate-500">(buy ÷ payout — not a forecast)</span></p></div>
          </div>
        ) : (
          <p className="mt-1 text-xs text-amber-300">Quote unavailable</p>
        )}
      </div>
      {canSkip && onSkip && (watching34 || active) && (
        <div className="mt-3 border-t border-slate-700/60 pt-3" aria-label="Skip setup">
          <div className="flex flex-wrap gap-1">
            {SKIP_SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:border-slate-500 hover:text-slate-200"
                onClick={() => setSkipReason(s)}
              >
                {s}
              </button>
            ))}
          </div>
          <div className="mt-2 flex gap-2">
            <input
              value={skipReason}
              onChange={(e) => setSkipReason(e.target.value)}
              placeholder="Optional reason"
              className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200"
            />
            <button
              type="button"
              className="rounded bg-slate-700 px-3 py-1 text-xs font-semibold text-white hover:bg-slate-600"
              onClick={() => {
                onSkip(skipReason)
                setSkipReason('')
              }}
            >
              Skip
            </button>
          </div>
        </div>
      )}
    </section>

    <section className="space-y-2" aria-label="Higher timeframe context">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium text-white">M5 / M15 context</h3>
        <span className="text-[11px] text-slate-500">Manual confirmation only</span>
      </div>
      <TimeframeContextCard card={timeframe.m5} m1Side={timeframe.m1Side} />
      <TimeframeContextCard card={timeframe.m15} m1Side={timeframe.m1Side} />
      <p className="text-[11px] leading-relaxed text-slate-500">
        M5/M15 readings do not change C5 scoring, Auto order placement, or the entry timer.
      </p>
    </section>

    <section aria-label="Live range zone">
      <div className="flex items-center justify-between gap-2"><h3 className="font-medium text-white">Range zone</h3><span className="text-xs text-sky-300">Previous 20 minutes</span></div>
      <div className="mt-3 flex justify-between gap-3 font-mono text-sm text-sky-200"><span>{price(context?.low)}</span><span>{price(context?.high)}</span></div>
      <div className="relative my-2 h-2 rounded-full bg-sky-400/15" aria-label={context ? `Price location: ${context.label}` : 'Range unavailable'}>{location != null && <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-200 ring-4 ring-sky-300/10 transition-[left] duration-150" style={{ left: `${location}%` }} />}</div>
      <div className="flex justify-between text-xs text-slate-500"><span>Prior low</span><span>Prior high</span></div>
      <p className="mt-3 font-medium text-sky-200">{context?.label ?? 'Waiting for continuous history'}{context && !context.isClosed ? ' · forming' : ''}</p>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">{context?.description ?? 'The range needs 20 consecutive completed candles.'}</p>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-800 pt-3 text-xs"><span className="text-slate-400">CHOP 14 · {context?.chop14?.toFixed(1) ?? '—'}</span><span className="text-slate-200">{regime}</span></div>
      <p className="mt-1 text-xs text-slate-500">Describes recent movement; does not choose a direction.</p>
      {!fresh && <p className="mt-2 text-xs text-amber-300">Last available range · waiting for fresh prices.</p>}
    </section>

    <section className="border-t border-slate-800 pt-4" aria-label="Reasons for C5 direction">
      <h3 className="font-medium text-white">{active ? `Why C5 selected ${entry.side}` : side ? `Watching ${side} · not an entry` : 'Waiting for directional agreement'}</h3>
      <p className="mt-1 text-xs text-slate-500">{entry.state === 'CONFIRMED' ? 'The checklist shows the confirmed signal. Scores below track the current candle.' : liveSetup.forming ? 'Conditions on the forming candle can change.' : 'Conditions on the latest completed candle.'}</p>
      <div className="my-3 grid grid-cols-2 gap-2">{[liveSetup.rise, liveSetup.fall].map(s => <div key={s.side} className={`rounded-lg border px-3 py-2 ${s.side === side ? 'border-slate-600 bg-slate-800/60' : 'border-slate-800'}`}>
        <div className="flex items-center justify-between text-xs"><span className={s.side === 'RISE' ? 'text-emerald-300' : 'text-rose-300'}>{s.side}</span><span className="font-mono text-slate-300">{s.passed}/{s.total}</span></div>
        <div className="mt-2 flex gap-1" aria-hidden="true">{Array.from({ length: 4 }, (_, i) => <span key={i} className={`h-1 flex-1 rounded ${i < s.passed ? s.side === 'RISE' ? 'bg-emerald-400' : 'bg-rose-400' : 'bg-slate-700'}`} />)}</div>
      </div>)}</div>
      {setup ? <ul className="space-y-2 text-xs">{setup.requiredLabels.map(row => <li key={row.key} className={`flex gap-2 ${row.ok ? 'text-slate-200' : 'text-amber-200'}`}><span className={row.ok ? 'text-emerald-400' : 'text-amber-300'}>{row.ok ? '✓' : '○'}</span><span>{row.text}{!row.ok ? ' · waiting' : ''}</span></li>)}<li className="pt-1 text-slate-500">Optional AO {setup.aoOptional.ok ? 'agrees' : 'does not agree'} · {setup.aoOptional.value?.toFixed(4) ?? '—'}</li></ul> : <p className="text-xs text-slate-400">Neither direction has a stronger current C5 setup.</p>}
      <p className="mt-3 text-xs leading-relaxed text-slate-500">4/4 counts conditions, not win probability. Range and CHOP are context only; they do not change C5 or Auto.</p>
    </section>

    <details className="border-t border-slate-800 pt-3 text-xs text-slate-400"><summary className="cursor-pointer text-slate-300">Last closed signal & feed</summary>
      <p className="mt-2">{eval_ ? `${new Date((eval_.time + 60) * 1000).toISOString().slice(11, 19)} UTC · ${eval_.final.replace('_', ' ')}` : 'Waiting for history'}</p><p className="mt-1">{eval_?.failures.join(' · ')}</p>
      <p className="mt-2">Last tick {market?.lastTickEpoch == null ? '—' : new Date(market.lastTickEpoch * 1000).toISOString().slice(11, 19) + ' UTC'} · {market?.status ?? 'DISCONNECTED'}</p>{market?.error && <p className="mt-1 text-amber-300">{market.error}</p>}
    </details>
    <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-400"><input type="checkbox" checked={sound} onChange={onToggleSound} /> C5 sound alerts</label>
  </aside>
}
