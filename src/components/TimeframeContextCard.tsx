import type { TimeframeCard } from '../lib/timeframes/timeframeContext'
import { agreementLabel } from '../lib/timeframes/decisionSummary'

const price = (value: number | null | undefined) => (value == null ? '—' : value.toFixed(2))

function zoneLine(
  label: string,
  zone: { mid: number; hi: number; lo: number; distance: string } | null | undefined,
): string {
  if (!zone) return `${label}: No confirmed level`
  return `${label}: ${price(zone.lo)}–${price(zone.hi)} · ${zone.distance}`
}

export function TimeframeContextCard({
  card,
  m1Side,
}: {
  card: TimeframeCard
  m1Side: 'RISE' | 'FALL' | null
}) {
  const closed = card.closed
  const agreement = closed?.agreement ?? null
  const titleSide = m1Side && agreement
    ? `${agreementLabel(agreement)}${agreement !== 'Mixed' ? ` ${m1Side}` : ''}`
    : m1Side
      ? '—'
      : agreementLabel(agreement)

  return (
    <section
      className="rounded-xl border border-slate-700/80 bg-slate-900/50 p-3"
      aria-label={`${card.label} context`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">{card.label}</h3>
        <span className="text-xs font-medium text-sky-300">{titleSide}</span>
      </div>

      {card.status !== 'ready' || !closed ? (
        <p className="mt-2 text-xs text-amber-300">{card.statusText}</p>
      ) : (
        <>
          <p className="mt-2 text-xs text-slate-300">
            <span className="text-slate-400">Trend</span>
            {' · '}
            {closed.trend === 'Up' ? 'Uptrend' : closed.trend === 'Down' ? 'Downtrend' : 'Mixed'}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            {zoneLine('Support', closed.support)}
          </p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
            {zoneLine('Resistance', closed.resistance)}
          </p>
          <p className="mt-1 text-[11px] text-slate-300">
            <span className="text-slate-400">Candle</span>
            {' · '}
            {closed.behaviorText}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-200">
            {closed.agreementReason}
          </p>
        </>
      )}

      <p className="mt-2 border-t border-slate-800 pt-2 text-[11px] leading-relaxed text-slate-400" aria-label="Forming now">
        <span className="text-slate-500">Forming now</span>
        {' · '}
        {card.forming?.text ?? 'Waiting for the next candle.'}
      </p>
    </section>
  )
}
