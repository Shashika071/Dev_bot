import type { ReactNode } from 'react'
import type { useAutoTrade } from '../hooks/useAutoTrade'
import { TradeHistory } from './TradeHistory'

type AutoTrade = ReturnType<typeof useAutoTrade>

export function AutoTradePanel({ trade }: { trade: AutoTrade }) {
  const connected = trade.status === 'CONNECTED'
  const selected = trade.accounts.find((a) => a.account_id === trade.accountId)

  return (
    <section className="flex min-h-full flex-col bg-[#0c1118] px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-200">Auto trade · Touch both sides</h2>
        <span
          className={
            connected
              ? 'text-[10px] font-semibold text-emerald-400'
              : trade.status === 'RECONNECTING'
                ? 'text-[10px] font-semibold text-amber-400'
                : 'text-[10px] font-semibold text-slate-500'
          }
        >
          ● {trade.status}
        </span>
        {trade.balance != null && (
          <span className="text-[10px] tabular-nums text-amber-200/90">
            Bal {trade.balance.toFixed(2)} {trade.currency}
          </span>
        )}
        {trade.cooldownLeft > 0 && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-amber-300">
            Wait {trade.cooldownLeft}s
          </span>
        )}
        {selected?.account_type && (
          <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
            selected.account_type.toLowerCase() === 'demo'
              ? 'bg-sky-500/20 text-sky-300'
              : 'bg-rose-500/20 text-rose-300'
          }`}
          >
            {selected.account_type}
          </span>
        )}
      </div>

      <p className="mb-3 max-w-4xl text-[11px] leading-snug text-slate-500">
        Buys Deriv <span className="text-slate-300">ONETOUCH</span> when live setup confidence is ≥{' '}
        <span className="text-slate-300">Min confidence</span> (default 80% = 4/5 conditions).
        <span className="text-slate-300"> UT is not required.</span>{' '}
        Mode <span className="text-slate-300">both</span> places <span className="text-emerald-400">+barrier</span> and{' '}
        <span className="text-rose-400">−barrier</span>. One entry per minute side; wait-after-trade blocks the next fill.
      </p>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
        <Field label="App ID">
          <input
            className="field"
            value={trade.appId}
            onChange={(e) => trade.setAppId(e.target.value)}
            placeholder="from developers.deriv.com"
            autoComplete="off"
          />
        </Field>
        <Field label="API token (trade scope)">
          <input
            className="field"
            type="password"
            value={trade.token}
            onChange={(e) => trade.setToken(e.target.value)}
            placeholder="PAT with trade"
            autoComplete="off"
          />
        </Field>
        <Field label="Account">
          <select
            className="field"
            value={trade.accountId}
            onChange={(e) => trade.setAccountId(e.target.value)}
          >
            {!trade.accounts.length && <option value={trade.accountId || ''}>{trade.accountId || 'Load accounts…'}</option>}
            {trade.accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {a.account_id} · {a.account_type || '?'} · {a.currency || ''}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Stake">
          <input
            className="field"
            type="number"
            min={0.35}
            step={0.01}
            value={trade.stake}
            onChange={(e) => trade.setStake(Number(e.target.value) || 1)}
          />
        </Field>
        <Field label="Barrier ±">
          <input
            className="field"
            type="number"
            min={0.01}
            step={0.01}
            value={trade.barrierAbs}
            onChange={(e) => trade.setBarrierAbs(Number(e.target.value) || 0.05)}
          />
        </Field>
        <Field label="Duration">
          <div className="flex gap-1">
            <input
              className="field min-w-0 flex-1"
              type="number"
              min={1}
              step={1}
              value={Number.isFinite(trade.duration) && trade.duration > 0 ? trade.duration : 7}
              onChange={(e) => {
                const n = Number(e.target.value)
                trade.setDuration(Number.isFinite(n) && n >= 1 ? Math.floor(n) : 7)
              }}
            />
            <select
              className="field w-14"
              value={trade.durationUnit}
              onChange={(e) => trade.setDurationUnit(e.target.value as 's' | 'm' | 'h' | 'd')}
            >
              <option value="s">s</option>
              <option value="m">m</option>
              <option value="h">h</option>
              <option value="d">d</option>
            </select>
          </div>
        </Field>
        <Field label="Min confidence %">
          <input
            className="field"
            type="number"
            min={0}
            max={100}
            step={1}
            value={trade.minConfidencePct}
            onChange={(e) => {
              const n = Math.floor(Number(e.target.value))
              trade.setMinConfidencePct(Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : 80)
            }}
            title="Only trade when closed-candle confidence is at least this % (80 = 4 of 5 conditions)"
          />
        </Field>
        <Field label="Wait after trade (sec)">
          <input
            className="field"
            type="number"
            min={0}
            step={1}
            value={trade.cooldownSec}
            onChange={(e) => trade.setCooldownSec(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
            title="Cooldown seconds after a successful trade before next entry"
          />
        </Field>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-slate-300">
          <span>Sides</span>
          <select
            className="field w-auto"
            value={trade.mode}
            onChange={(e) => trade.setMode(e.target.value === 'signal' ? 'signal' : 'both')}
          >
            <option value="both">Both + and −</option>
            <option value="signal">Signal side only</option>
          </select>
        </label>

        <button type="button" className="btn" disabled={trade.busy} onClick={() => void trade.loadAccounts()}>
          1. Load accounts
        </button>
        <button type="button" className="btn" disabled={trade.busy || !trade.accountId} onClick={() => void trade.connect()}>
          2. Connect trade WS
        </button>
        <button type="button" className="btn-muted" disabled={!connected && trade.status === 'DISCONNECTED'} onClick={trade.disconnect}>
          Disconnect
        </button>

        <label className={`ml-auto flex cursor-pointer items-center gap-2 rounded border px-2 py-1 text-xs font-semibold ${
          trade.autoEnabled
            ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-300'
            : 'border-slate-700 text-slate-400'
        }`}
        >
          <input
            type="checkbox"
            checked={trade.autoEnabled}
            disabled={!connected}
            onChange={(e) => trade.setAutoEnabled(e.target.checked)}
          />
          Auto trade ON
        </label>
      </div>

      {trade.error && <p role="alert" className="mt-2 text-xs text-rose-300">{trade.error}</p>}

      <div className="mt-3 rounded border border-slate-800 bg-[#0b0f14] px-3 py-2 text-[11px] leading-snug text-slate-400">
        <p className="font-semibold text-slate-300">Why no trade yet?</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          <li>Chart must show <span className="text-emerald-400">READY</span> (not “Recovering / signals paused”).</li>
          <li>Live RISE/FALL confidence must be ≥ {trade.minConfidencePct}% (UT not required).</li>
          <li>Auto trade ON + trade socket connected; cooldown may still be blocking.</li>
        </ul>
      </div>

      <TradeHistory rows={trade.log} onClear={trade.clearTradeHistory} />

      <style>{`
        .field {
          width: 100%;
          border-radius: 0.375rem;
          border: 1px solid rgb(51 65 85);
          background: #0b0f14;
          padding: 0.35rem 0.5rem;
          font-size: 0.75rem;
          color: rgb(226 232 240);
        }
        .field:focus { outline: 1px solid rgb(56 189 248); }
        .btn, .btn-muted {
          border-radius: 0.375rem;
          border: 1px solid rgb(51 65 85);
          padding: 0.35rem 0.65rem;
          font-size: 0.75rem;
          color: rgb(226 232 240);
          background: rgb(30 41 59);
        }
        .btn:hover:not(:disabled) { background: rgb(51 65 85); }
        .btn-muted { color: rgb(148 163 184); }
        .btn:disabled, .btn-muted:disabled { opacity: 0.45; }
      `}</style>
    </section>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-[10px] uppercase tracking-wide text-slate-500">
      <span className="mb-1 block">{label}</span>
      {children}
    </label>
  )
}
