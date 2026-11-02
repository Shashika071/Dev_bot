import type { useAutoTrade } from '../hooks/useAutoTrade'

type AutoTrade = ReturnType<typeof useAutoTrade>

export function ManualTradeBar({ trade }: { trade: AutoTrade }) {
  const connected = trade.status === 'CONNECTED'
  const cooling = trade.cooldownLeft > 0
  const locked = !connected || trade.busy || cooling
  const lockReason = !connected
    ? 'Connect in Trade settings first'
    : trade.busy
      ? 'Trade in progress…'
      : cooling
        ? `Wait ${trade.cooldownLeft}s before next trade`
        : null
  const stakeLabel = trade.useFullStake
    ? `full bal${trade.balance != null ? ` ≈ ${trade.balance.toFixed(2)}` : ''}`
    : `stake ${trade.stake}`
  const barrier = trade.barrierAbs
  const dur = `${trade.duration}${trade.durationUnit}`

  return (
    <div className="relative z-20 shrink-0 border-t border-slate-800 bg-[#0c1118] px-3 py-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
        <span className="font-semibold uppercase tracking-wide text-slate-400">Manual trade</span>
        <span className="tabular-nums">
          {connected ? (
            <span className="text-emerald-400">connected</span>
          ) : (
            <span className="text-amber-400">connect in Trade settings first</span>
          )}
          {' · '}
          {stakeLabel}
          {' · '}
          ±{barrier} · {dur}
          {cooling ? (
            <span className="font-semibold text-amber-300"> · wait {trade.cooldownLeft}s</span>
          ) : null}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <button
          type="button"
          disabled={locked}
          onClick={() => void trade.placeManualTrade('RISE')}
          className="cursor-pointer rounded-lg border border-emerald-500/50 bg-emerald-600 px-4 py-3 text-sm font-bold text-white shadow hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          title={lockReason ?? 'Buy ONETOUCH with +barrier'}
        >
          {cooling ? `WAIT ${trade.cooldownLeft}s` : trade.busy ? 'WORKING…' : `RISE · +${barrier}`}
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => void trade.placeManualTrade('FALL')}
          className="cursor-pointer rounded-lg border border-rose-500/50 bg-rose-600 px-4 py-3 text-sm font-bold text-white shadow hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-40"
          title={lockReason ?? 'Buy ONETOUCH with −barrier'}
        >
          {cooling ? `WAIT ${trade.cooldownLeft}s` : trade.busy ? 'WORKING…' : `FALL · −${barrier}`}
        </button>
      </div>
      {lockReason && (
        <p className="mt-2 text-xs text-amber-300">{lockReason}</p>
      )}
      {trade.error && <p className="mt-2 text-xs text-rose-300">{trade.error}</p>}
    </div>
  )
}
