import type { MarketState } from '../lib/deriv/marketFeed'
import type { LiveSetupConfidence, SideSetup } from '../lib/strategy/liveConfidence'
import { C5_STRATEGY_LABEL } from '../lib/strategy/c5Strategy'
import type { SignalEvaluation } from '../lib/types'

type SignalPanelProps = {
  market: MarketState | null
  eval_: SignalEvaluation | null
  liveSignal: SignalEvaluation | null
  liveSetup: LiveSetupConfidence
  signalMeta?: { candleCloseEpoch: number; expiresAt: number; remainingSec: number } | null
  sound: boolean
  onToggleSound: () => void
}

export function SignalPanel({
  market,
  eval_,
  liveSignal,
  liveSetup,
  signalMeta,
  sound,
  onToggleSound,
}: SignalPanelProps) {
  const actionable = !!(liveSignal?.isQualified && (liveSignal.final === 'RISE' || liveSignal.final === 'FALL'))
  const final = market?.ready && actionable ? liveSignal!.final : 'NO_TRADE'
  const finalCls =
    actionable && final === 'RISE'
      ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/50'
      : actionable && final === 'FALL'
        ? 'bg-rose-500/20 text-rose-400 border-rose-500/50'
        : 'bg-slate-800/70 text-slate-200 border-slate-600'

  const previewLean = liveSetup.forming ? liveSetup.lean : 'NONE'
  const previewSide = previewLean === 'RISE' ? liveSetup.rise : previewLean === 'FALL' ? liveSetup.fall : null

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
        <div className="mt-2 text-[11px] font-semibold text-sky-300">{C5_STRATEGY_LABEL}</div>
      </div>

      {market?.error && <p role="status" className="text-xs text-amber-300">{market.error}</p>}

      <div className={`rounded-xl border-2 px-3 py-5 text-center ${finalCls}`}>
        <div className="text-[10px] uppercase tracking-widest opacity-70">
          {actionable
            ? 'C5 qualified · 4/4 · 5-second entry window'
            : 'No actionable C5 signal'}
        </div>
        <div className="mt-1 text-3xl font-black tracking-wide">
          {actionable ? final.replace('_', ' ') : 'NO TRADE'}
        </div>
        {actionable && signalMeta && (
          <div className="mt-2 space-y-0.5 text-[10px] font-normal opacity-90">
            <div>Signal {new Date(signalMeta.candleCloseEpoch * 1000).toISOString().slice(11, 19)} UTC</div>
            <div>
              Entry window {signalMeta.remainingSec.toFixed(1)}s left
              {signalMeta.remainingSec <= 0 ? ' · expired' : ''}
            </div>
          </div>
        )}
        {actionable && (
          <div className="mt-1 text-[10px] opacity-80">
            4/4 met — fires on forming or close · not a 100% win probability
          </div>
        )}
      </div>

      {liveSetup.forming && previewSide && (
        <div className={`rounded-lg border border-dashed p-3 ${
          previewSide.passed >= 4
            ? 'border-emerald-500/50 bg-emerald-500/10'
            : 'border-slate-600 bg-slate-900/40'
        }`}
        >
          <div className="text-[10px] uppercase tracking-widest text-amber-200/90">
            {previewSide.passed >= 4
              ? 'Forming 4/4 · Auto may fire immediately'
              : 'Forming candle · building toward 4/4'}
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {previewSide.side} {previewSide.passed}/{previewSide.total} required on the open minute.
            {previewSide.passed >= 4
              ? ' First 4/4 this minute triggers sound + Auto (5s to buy).'
              : ' Trade triggers as soon as all 4 required conditions pass.'}
          </p>
        </div>
      )}

      <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">
            C5 checks · this minute
          </div>
          <div className="text-[10px] text-slate-500">
            {liveSetup.forming ? 'forming preview' : liveSetup.ready ? 'closed' : 'warmup'}
          </div>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <SetupCard setup={liveSetup.rise} active={liveSetup.lean === 'RISE'} />
          <SetupCard setup={liveSetup.fall} active={liveSetup.lean === 'FALL'} />
        </div>
        <p className="mt-2 text-[10px] leading-snug text-slate-500">
          Live Auto: first time this minute hits 4/4 (forming or close) → entry within 5s. AO optional.
        </p>
      </div>

      <ConditionList title="RISE required (C5)" setup={liveSetup.rise} tone="rise" />
      <ConditionList title="FALL required (C5)" setup={liveSetup.fall} tone="fall" />

      <div className="text-xs text-slate-300">
        <p className="font-semibold">Last completed candle</p>
        <p className="mt-1 text-slate-500">
          {eval_
            ? new Date((eval_.time + 60) * 1000).toISOString().slice(11, 19) + ' UTC close'
            : 'Waiting for warmup'}
        </p>
        <p className="mt-2 text-amber-200/80">
          {eval_?.isQualified
            ? `C5 ${eval_.final} · ${eval_.requiredPassed ?? 4}/4 required`
            : (eval_?.failures.join(' · ') || '')}
        </p>
        {eval_ && (
          <p className="mt-1 text-[10px] text-slate-500">
            AO {eval_.ao == null ? 'n/a' : eval_.ao.toFixed(4)} · optional / informational
            {eval_.confirmations.ao ? ' · currently agrees' : ' · currently disagrees or unavailable'}
          </p>
        )}
      </div>

      <label className="flex cursor-pointer items-center justify-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={sound} onChange={onToggleSound} />
        Sound on qualified C5 (first 4/4 this minute)
      </label>
    </aside>
  )
}

function SetupCard({ setup, active }: { setup: SideSetup; active: boolean }) {
  const rise = setup.side === 'RISE'
  const full = setup.passed >= setup.total
  return (
    <div
      className={`rounded-md border p-2 ${
        full && active
          ? rise
            ? 'border-emerald-500/60 bg-emerald-500/10'
            : 'border-rose-500/60 bg-rose-500/10'
          : 'border-slate-700 bg-[#0b0f14]'
      }`}
    >
      <div className={`text-xs font-bold ${rise ? 'text-emerald-400' : 'text-rose-400'}`}>{setup.side}</div>
      <div className="mt-0.5 text-2xl font-black tabular-nums text-white">
        {setup.passed}/{setup.total}
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded bg-slate-800">
        <div
          className={`h-full transition-[width] duration-150 ${rise ? 'bg-emerald-500' : 'bg-rose-500'}`}
          style={{ width: `${Math.max(setup.passed > 0 ? 4 : 0, (100 * setup.passed) / setup.total)}%` }}
        />
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        {full ? '4/4 required' : 'required conditions'}
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
        {setup.requiredLabels.map((row) => (
          <li key={row.key} className={row.ok ? (tone === 'rise' ? 'text-emerald-400' : 'text-rose-400') : 'text-slate-500'}>
            {row.ok ? '✓' : '—'} {row.text}
          </li>
        ))}
        <li className="text-slate-500">
          {setup.aoOptional.ok ? '○' : '○'} {setup.aoOptional.text}
          {setup.aoOptional.value != null ? ` · ${setup.aoOptional.value.toFixed(4)}` : ''}
        </li>
      </ul>
    </div>
  )
}
