import type { MarketState } from '../lib/deriv/marketFeed'
import type { LiveSetupConfidence, SideSetup } from '../lib/strategy/liveConfidence'
import type { SignalEvaluation } from '../lib/types'

type SignalPanelProps = {
  market: MarketState | null
  eval_: SignalEvaluation | null
  liveSignal: SignalEvaluation | null
  liveSetup: LiveSetupConfidence
  minConfidencePct?: number
  sound: boolean
  onToggleSound: () => void
}

export function SignalPanel({
  market,
  eval_,
  liveSignal,
  liveSetup,
  minConfidencePct = 80,
  sound,
  onToggleSound,
}: SignalPanelProps) {
  const risePct = Math.round(100 * liveSetup.rise.confidence)
  const fallPct = Math.round(100 * liveSetup.fall.confidence)
  const partialSide = risePct >= fallPct ? 'RISE' as const : 'FALL' as const
  const partialPct = Math.max(risePct, fallPct)
  const hasPartial = market?.ready && !liveSignal && partialPct >= minConfidencePct

  const final = market?.ready ? liveSignal?.final ?? (hasPartial ? partialSide : 'NO_TRADE') : 'NO_TRADE'
  const mode = liveSignal ? 'full' : hasPartial ? 'partial' : 'none'
  const finalCls =
    mode === 'full' && final === 'RISE'
      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/50'
      : mode === 'full' && final === 'FALL'
        ? 'bg-rose-500/20 text-rose-400 border-rose-500/50'
        : mode === 'partial' && partialSide === 'RISE'
          ? 'bg-cyan-500/15 text-cyan-300 border-cyan-500/40 border-dashed'
          : mode === 'partial'
            ? 'bg-amber-500/15 text-amber-200 border-amber-500/40 border-dashed'
            : 'bg-slate-800/70 text-slate-200 border-slate-600'

  return (
    <aside className="flex h-full min-h-0 w-full flex-col gap-3 overflow-auto border-l border-slate-800 bg-[#0c1118] p-4">
      <div className="text-center">
        <div
          className={
            market?.status === 'CONNECTED'
              ? 'text-xs font-semibold text-emerald-400'
              : market?.status === 'RECONNECTING'
                ? 'text-xs font-semibold text-amber-400'
                : 'text-xs font-semibold text-rose-400'
          }
        >
          ● {market?.ready ? 'READY' : market?.status === 'CONNECTED' ? 'SYNCING / PAUSED' : market?.status ?? 'DISCONNECTED'}
        </div>
        <div className="mt-1 font-mono text-2xl font-bold text-amber-300">
          {market?.lastPrice != null ? market.lastPrice.toFixed(2) : '—'}
        </div>
      </div>

      {market?.error && <p role="status" className="text-xs text-amber-300">{market.error}</p>}

      <div className={`rounded-xl border-2 px-3 py-5 text-center ${finalCls}`}>
        <div className="text-[10px] uppercase tracking-widest opacity-70">
          {mode === 'full'
            ? 'Full signal · 5/5 · 5-second window'
            : mode === 'partial'
              ? `Partial setup · ≥${minConfidencePct}%`
              : 'No trade'}
        </div>
        <div className="mt-1 text-3xl font-black tracking-wide">
          {mode === 'partial' ? `${partialSide} ${partialPct}%` : final.replace('_', ' ')}
        </div>
        {mode === 'partial' && (
          <div className="mt-1 text-[10px] font-normal opacity-80">Dashed style · not a full 5/5 alert</div>
        )}
      </div>

      <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">
            Live setup · this minute
          </div>
          <div className="text-[10px] text-slate-500">
            {liveSetup.forming ? 'forming' : liveSetup.ready ? 'closed' : 'warmup'}
          </div>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <SetupCard setup={liveSetup.rise} active={liveSetup.lean === 'RISE'} minConfidencePct={minConfidencePct} />
          <SetupCard setup={liveSetup.fall} active={liveSetup.lean === 'FALL'} minConfidencePct={minConfidencePct} />
        </div>
        <p className="mt-2 text-[10px] leading-snug text-slate-500">
          Live condition count this minute. Full alert = 5/5; auto trade can fire from ≥{minConfidencePct}%.
        </p>
      </div>

      <ConditionList title="RISE checks now" setup={liveSetup.rise} tone="rise" />
      <ConditionList title="FALL checks now" setup={liveSetup.fall} tone="fall" />

      <div className="text-xs text-slate-300">
        <p className="font-semibold">Last completed candle</p>
        <p className="mt-1 text-slate-500">{eval_ ? new Date((eval_.time + 60) * 1000).toISOString().slice(11, 19) + ' UTC close' : 'Waiting for warmup'}</p>
        <p className="mt-2 text-amber-200/80">{eval_?.failures.join(' · ') || (eval_?.isQualified ? `${eval_.final} qualified` : '')}</p>
      </div>

      <label className="flex cursor-pointer items-center justify-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={sound} onChange={onToggleSound} />
        Sound on RISE / FALL
      </label>
    </aside>
  )
}

function SetupCard({
  setup,
  active,
  minConfidencePct,
}: {
  setup: SideSetup
  active: boolean
  minConfidencePct: number
}) {
  const rise = setup.side === 'RISE'
  const pct = Math.round(100 * setup.confidence)
  const partial = pct >= minConfidencePct && pct < 100
  const full = pct >= 100
  return (
    <div
      className={`rounded-md border p-2 ${
        full && active
          ? rise
            ? 'border-emerald-500/60 bg-emerald-500/10'
            : 'border-rose-500/60 bg-rose-500/10'
          : partial && active
            ? rise
              ? 'border-dashed border-cyan-500/60 bg-cyan-500/10'
              : 'border-dashed border-amber-500/60 bg-amber-500/10'
            : 'border-slate-700 bg-[#0b0f14]'
      }`}
    >
      <div className={`text-xs font-bold ${rise ? 'text-emerald-400' : 'text-rose-400'}`}>{setup.side}</div>
      <div className="mt-0.5 text-2xl font-black tabular-nums text-white">{pct}%</div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-slate-800">
        <div
          className={`h-full transition-[width] duration-150 ${rise ? 'bg-emerald-500' : 'bg-rose-500'}`}
          style={{ width: `${Math.max(pct > 0 ? 4 : 0, pct)}%` }}
        />
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        {setup.passed}/{setup.total} conditions
      </div>
    </div>
  )
}

function ConditionList({
  title,
  setup,
  tone,
}: {
  title: string
  setup: SideSetup
  tone: 'rise' | 'fall'
}) {
  return (
    <div className="text-xs">
      <p className={`font-semibold ${tone === 'rise' ? 'text-emerald-400' : 'text-rose-400'}`}>{title}</p>
      <ul className="mt-1 space-y-0.5">
        {setup.labels.map((row) => (
          <li key={row.key} className={row.ok ? (tone === 'rise' ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-500'}>
            {row.ok ? '✓' : '—'} {row.text}
          </li>
        ))}
      </ul>
    </div>
  )
}
