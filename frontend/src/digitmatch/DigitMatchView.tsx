import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, BarChart, Bar,
} from 'recharts';
import './digitmatch.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type Dashboard = {
  demo_only: boolean;
  ui_state: string;
  connection_status: string;
  auth_status: string;
  demo_verified: boolean;
  credential_source?: string;
  loginid: string | null;
  balance: number | null;
  currency: string | null;
  credentials_configured: boolean;
  mode: string;
  paused: boolean;
  emergency_stop: boolean;
  stop_note: string;
  target_note: string;
  freshness_policy: string;
  freshness: { age_seconds: number | null; stale: boolean; last_epoch: number | null };
  instrument: {
    expected_legacy_symbol: string;
    expected_display_name: string;
    resolved_symbol: string | null;
    resolved_display: string | null;
    matches_legacy_symbol: boolean | null;
  };
  contract: { type: string; duration_ticks: number; ready: boolean; error: string | null };
  model: { id: number; name: string; checksum: string; created_at: string | null; has_demonstrated_edge: boolean } | null;
  probabilities: number[] | null;
  probability_label: string;
  decision: {
    action: string;
    reason: string;
    digit: number | null;
    break_even: number | null;
    expected_value: number | null;
    ask_price: number | null;
    total_payout: number | null;
  } | null;
  active_contract: {
    broker_contract_id: string;
    digit: number;
    status: string;
    buy_price: number | null;
    total_payout: number | null;
    profit: number | null;
  } | null;
  trades: Array<{
    broker_contract_id: string;
    digit: number;
    status: string;
    buy_price: number | null;
    total_payout: number | null;
    profit: number | null;
    mode: string;
  }>;
  equity: Array<{ contract_id: string; equity: number; drawdown: number; profit: number }>;
  pnl: { daily: number; cumulative: number };
  risk: {
    stake: number;
    cooldown_seconds: number;
    max_trades_per_day: number;
    trades_today: number;
    daily_loss_limit: number;
    daily_profit_stop: number;
    pnl_today: number;
    reset_timezone: string;
    margin: number;
    broker_min_stake: number | null;
    max_open_contracts: number;
  };
  ticks_stored: number;
  touch_ticks_available?: number;
  history_job: { status: string; ticks_stored: number; target_ticks: number; note: string | null; error: string | null } | null;
  train_job: { status: string; progress: string; error: string | null; model_id: number | null } | null;
  reconciliation: { blocked: boolean; reason: string | null };
  last_error: string | null;
  evaluation: {
    final_test?: {
      log_loss?: number;
      brier?: number;
      top_hit_rate?: number;
      count?: number;
      calibration_bins?: Array<{ bin_start: number; mean_confidence: number | null; empirical_hit_rate: number | null; count: number }>;
      payout_adjusted_return?: number | null;
      selected_trades_reason?: string;
    };
    comparison_to_uniform?: string;
    edge_statement?: string;
    has_demonstrated_edge?: boolean;
    walk_forward?: Array<{ fold: number; log_loss: number; validation_rows: number }>;
  } | null;
};

const STATE_COPY: Record<string, string> = {
  credentials_missing: 'No Deriv token is saved. Add it in Configuration. Nothing here is simulated.',
  disconnected: 'The saved Deriv login is not connected yet. The message at the bottom is the reason.',
  real_account_rejected: 'The authorized account is not a verified demo account. Purchases are blocked.',
  contract_unavailable: 'Volatility 100 Index or a 5-tick Digit Matches contract is unavailable. No substitute is used.',
  warming_up: 'Collecting ticks. Features need 1,000 clean ticks before a live row can be built.',
  stale_data: 'The latest tick is older than the freshness limit.',
  no_model: 'No model has been promoted. Training saves a candidate and does not activate it.',
  observe: 'OBSERVE is on. Predictions can be recorded. Contracts are not bought.',
  no_qualifying_signal: 'Filtered mode is idle. The quote does not clear the break-even margin.',
  daily_limit: 'A daily trade, loss, or profit limit has been reached.',
  uncertain_purchase: 'A purchase response was lost. New orders stay blocked until broker records identify one contract.',
  emergency_stop: 'Emergency stop is on. New orders are blocked. An open contract is not cancelled.',
  paused: 'Paused. New orders are blocked.',
  in_contract: 'A demo contract is open. The next purchase waits for broker settlement.',
  ready: 'Connected and inside the current limits.',
};

function money(value: number | null | undefined, currency: string | null) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(2)} ${currency || ''}`.trim();
}

async function readError(response: Response) {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === 'string' && parsed.detail) return parsed.detail;
  } catch {
    /* keep the raw body */
  }
  return text || `HTTP ${response.status}`;
}

export function DigitMatchDashControl() {
  const [mode, setMode] = useState('observe');
  const [paused, setPaused] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(`${API}/api/digitmatch/dashboard`);
    if (!response.ok) return;
    const body = await response.json() as { mode?: string; paused?: boolean; emergency_stop?: boolean };
    setMode(body.mode || 'observe');
    setPaused(!!body.paused);
    setStopped(!!body.emergency_stop);
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function post(path: string, body: unknown, key: string) {
    setBusyKey(key);
    setError(null);
    try {
      const response = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? '{}' : JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await readError(response));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setBusyKey(null);
    }
  }

  function modeButton(id: string, label: string, path: string, body: unknown, pressed: boolean) {
    const busy = busyKey === id;
    return (
      <button
        type="button"
        className={`btn ${pressed ? 'btn-primary' : 'btn-ghost'}`}
        aria-pressed={pressed}
        disabled={busyKey !== null}
        onClick={() => post(path, body, id)}
      >
        {busy ? 'Working…' : pressed ? `Selected · ${label}` : label}
      </button>
    );
  }

  const label = mode === 'demo_explore' ? 'ON' : mode === 'demo_filtered' ? 'ON if quote passes' : 'OFF';

  return (
    <div className="glass dm-dash-control">
      <strong>Digit Matches</strong>
      <span className={mode === 'observe' ? 'badge badge-dim' : 'badge badge-green'}>{label}</span>
      {paused && <span className="badge badge-amber">Paused</span>}
      {stopped && <span className="badge badge-amber">Emergency stop</span>}
      {modeButton('off', 'Off', '/api/digitmatch/mode', { mode: 'observe' }, mode === 'observe')}
      {modeButton('on', 'On', '/api/digitmatch/mode', { mode: 'demo_explore' }, mode === 'demo_explore')}
      {modeButton('filter', 'On if quote passes', '/api/digitmatch/mode', { mode: 'demo_filtered' }, mode === 'demo_filtered')}
      <button type="button" className={`btn ${paused ? 'btn-primary' : 'btn-ghost'}`} aria-pressed={paused} disabled={busyKey !== null} onClick={() => post(paused ? '/api/digitmatch/resume' : '/api/digitmatch/pause', {}, 'pause')}>
        {busyKey === 'pause' ? 'Working…' : paused ? 'Selected · Paused' : 'Pause'}
      </button>
      {error && <span className="text-xs" style={{ color: 'var(--red)' }}>{error}</span>}
    </div>
  );
}

export default function DigitMatchView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<Array<{ id: number; name: string; is_active: boolean; checksum: string }>>([]);
  const [stake, setStake] = useState('1');
  const [margin, setMargin] = useState('0.02');
  const [timezone, setTimezone] = useState('Asia/Colombo');
  const [lossLimit, setLossLimit] = useState('20');
  const [profitStop, setProfitStop] = useState('20');
  const [maxTrades, setMaxTrades] = useState('50');
  const [cooldown, setCooldown] = useState('30');
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const formReady = useRef(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/digitmatch/dashboard`);
      if (!response.ok) throw new Error(`Dashboard HTTP ${response.status}`);
      const body = await response.json() as Dashboard;
      setData(body);
      setError(null);
      if (!formReady.current) {
        setStake(String(body.risk.stake));
        setMargin(String(body.risk.margin));
        setTimezone(body.risk.reset_timezone);
        setLossLimit(String(body.risk.daily_loss_limit));
        setProfitStop(String(body.risk.daily_profit_stop));
        setMaxTrades(String(body.risk.max_trades_per_day));
        setCooldown(String(body.risk.cooldown_seconds));
        formReady.current = true;
      }
      const modelResponse = await fetch(`${API}/api/digitmatch/models`);
      if (modelResponse.ok) {
        const payload = await modelResponse.json() as { models: Array<{ id: number; name: string; is_active: boolean; checksum: string }> };
        setModels(payload.models);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Dashboard request failed');
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function post(path: string, body: unknown, key: string, okText: string) {
    setBusyKey(key);
    setNotice(null);
    try {
      const response = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json().catch(() => null) as { note?: string } | null;
      setNotice({ ok: true, text: payload?.note || okText });
      setError(null);
      await load();
    } catch (err) {
      setNotice({ ok: false, text: err instanceof Error ? err.message : 'Request failed' });
    } finally {
      setBusyKey(null);
    }
  }

  function press(id: string, label: string, run: () => void, opts?: { on?: boolean; tone?: 'warn' | 'danger' }) {
    const busy = busyKey === id;
    const tone = opts?.tone === 'warn' ? 'is-warn' : opts?.tone === 'danger' ? 'is-danger' : '';
    return (
      <button
        type="button"
        className={`dm-btn ${opts?.on ? 'is-on' : ''} ${tone} ${busy ? 'is-busy' : ''}`}
        aria-pressed={!!opts?.on}
        disabled={busyKey !== null}
        onClick={run}
      >
        {busy ? 'Working…' : opts?.on ? `✓ ${label}` : label}
      </button>
    );
  }

  const state = data?.ui_state || 'loading';
  const probs = data?.probabilities;
  const calibration = (data?.evaluation?.final_test?.calibration_bins || [])
    .filter((bin) => bin.count > 0 && bin.mean_confidence != null && bin.empirical_hit_rate != null)
    .map((bin) => ({
      confidence: Number(bin.mean_confidence?.toFixed(3)),
      empirical: Number(bin.empirical_hit_rate?.toFixed(3)),
    }));

  return (
    <div className="dm-root">
      <header className="dm-top">
        <div>
          <div className="dm-badge">DEMO ONLY</div>
          <h1>Digit Matches research</h1>
          <p>Volatility 100 Index · DIGITMATCH · 5 ticks · estimated probability, not a guarantee.</p>
        </div>
        <div className="dm-state">{STATE_COPY[state] || 'Loading the research desk.'}</div>
      </header>

      {notice && <div className={notice.ok ? 'dm-ok' : 'dm-alert'}>{notice.text}</div>}
      {error && <div className="dm-alert">{error}</div>}
      {data?.last_error && <div className="dm-alert">{data.last_error}</div>}
      {data?.reconciliation.blocked && (
        <div className="dm-alert">
          Uncertain purchase. {data.reconciliation.reason || 'Match the broker statement before any new order.'}
          New orders stay blocked. This screen does not retry the buy.
        </div>
      )}

      <section className="dm-grid">
        <article>
          <h2>Connection</h2>
          <p>{data?.connection_status || '…'} · auth {data?.auth_status || '…'}</p>
          <p>Demo verified: {data?.demo_verified ? 'yes' : 'no'}</p>
          <p>Account: {data?.loginid || '—'}</p>
          <p>
            Login: {data?.credential_source === 'touch_bot'
              ? 'same token as the touch bot'
              : data?.credential_source === 'digitmatch_env'
                ? 'Digit Matches token'
                : 'not configured'}
          </p>
          <p>Balance: {money(data?.balance, data?.currency || null)}</p>
          <p>Tick age: {data?.freshness.age_seconds == null ? '—' : `${data.freshness.age_seconds.toFixed(1)}s`}</p>
        </article>
        <article>
          <h2>Contract</h2>
          <p>{data?.instrument.resolved_display || data?.instrument.expected_display_name}</p>
          <p>Symbol: {data?.instrument.resolved_symbol || 'not resolved'} (legacy expected {data?.instrument.expected_legacy_symbol})</p>
          <p>{data?.contract.type} · {data?.contract.duration_ticks} ticks</p>
          <p>Digit Matches ticks: {data?.ticks_stored ?? '—'}</p>
          <p>Saved by the touch bot: <span className="dm-keep">{(data?.touch_ticks_available ?? 0).toLocaleString()} R_100</span></p>
        </article>
        <article>
          <h2>Model</h2>
          <p>{data?.model ? `${data.model.name} #${data.model.id}` : 'No promoted model'}</p>
          <p>Edge claimed: no</p>
          <p className="dm-note">{data?.evaluation?.edge_statement || data?.target_note}</p>
        </article>
        <article>
          <h2>Mode</h2>
          <p className="dm-mode">{data?.mode === 'demo_explore' ? 'ON' : data?.mode === 'demo_filtered' ? 'ON IF QUOTE PASSES' : 'OFF'}</p>
          <div className="dm-actions stack">
            {press('off', 'Off', () => post('/api/digitmatch/mode', { mode: 'observe' }, 'off', 'Off. No demo buy will be placed.'), { on: (data?.mode || 'observe') === 'observe' })}
            {press('on', 'On', () => post('/api/digitmatch/mode', { mode: 'demo_explore' }, 'on', 'On. Demo trades can be placed at the fixed stake.'), { on: data?.mode === 'demo_explore' })}
            {press('filter', 'On if quote passes', () => post('/api/digitmatch/mode', { mode: 'demo_filtered' }, 'filter', 'On only when the live quote passes the filter.'), { on: data?.mode === 'demo_filtered' })}
            {press('pause', data?.paused ? 'Paused' : 'Pause', () => post(data?.paused ? '/api/digitmatch/resume' : '/api/digitmatch/pause', {}, 'pause', data?.paused ? 'Pause cleared.' : 'Paused. New orders are blocked.'), { on: !!data?.paused, tone: 'warn' })}
            {press('stop', 'Emergency stop', () => post('/api/digitmatch/emergency-stop', {}, 'stop', 'Emergency stop is on. An open contract is not cancelled.'), { on: !!data?.emergency_stop, tone: 'danger' })}
            {data?.emergency_stop && press('clear', 'Clear stop', () => post('/api/digitmatch/emergency-stop/clear', {}, 'clear', 'Emergency stop cleared.'), { tone: 'danger' })}
          </div>
          <p className="dm-note">{data?.stop_note}</p>
        </article>
      </section>

      <section className="dm-panel">
        <h2>{data?.probability_label || 'Estimated probability'}</h2>
        {probs && probs.length === 10 ? (
          <div className="dm-bars">
            {probs.map((value, digit) => (
              <div key={digit} className={data?.decision?.digit === digit ? 'picked' : ''}>
                <span>{digit}</span>
                <div className="track"><div style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div>
                <em>{(value * 100).toFixed(1)}%</em>
              </div>
            ))}
          </div>
        ) : (
          <p>No live probability vector yet. The desk does not fill this with placeholder numbers.</p>
        )}
        <div className="dm-decision">
          <p>Selected digit: {data?.decision?.digit ?? '—'}</p>
          <p>Break-even probability: {data?.decision?.break_even == null ? '—' : data.decision.break_even.toFixed(4)}</p>
          <p>Model-implied expected value: {data?.decision?.expected_value == null ? '—' : data.decision.expected_value.toFixed(4)} (net of stake, using the current total payout)</p>
          <p>Ask / total payout: {data?.decision?.ask_price ?? '—'} / {data?.decision?.total_payout ?? '—'}</p>
          <p>Why: {data?.decision ? `${data.decision.action} — ${data.decision.reason}` : 'No decision recorded yet.'}</p>
        </div>
      </section>

      <section className="dm-grid">
        <article>
          <h2>Active contract</h2>
          {data?.active_contract ? (
            <>
              <p>Broker id {data.active_contract.broker_contract_id}</p>
              <p>Digit {data.active_contract.digit} · {data.active_contract.status}</p>
              <p>Stake paid {money(data.active_contract.buy_price, data.currency)} · total payout {money(data.active_contract.total_payout, data.currency)}</p>
            </>
          ) : <p>No open bot contract.</p>}
        </article>
        <article>
          <h2>Demo P/L</h2>
          <p>Today ({data?.risk.reset_timezone}): {money(data?.pnl.daily, data?.currency || null)}</p>
          <p>Cumulative settled: {money(data?.pnl.cumulative, data?.currency || null)}</p>
          <p className="dm-note">These figures come from stored broker settlement profits. They are not assumed-payout simulations.</p>
        </article>
      </section>

      <section className="dm-panel">
        <h2>Equity and drawdown</h2>
        {data && data.equity.length > 0 ? (
          <div className="dm-chart">
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={data.equity}>
                <CartesianGrid stroke="rgba(0,212,255,0.08)" />
                <XAxis dataKey="contract_id" hide />
                <YAxis stroke="#6b80a8" />
                <Tooltip />
                <Area dataKey="equity" stroke="#00e5a0" fill="rgba(0,229,160,0.15)" name="Equity" />
                <Area dataKey="drawdown" stroke="#ff4d6d" fill="rgba(255,77,109,0.08)" name="Drawdown" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : <p>No settled demo trades yet, so there is no equity curve.</p>}
      </section>

      <section className="dm-panel">
        <h2>Historical evaluation</h2>
        <p className="dm-note">{data?.evaluation?.comparison_to_uniform || 'Train a candidate to see held-out log loss. That result is not a trading profit.'}</p>
        {data?.evaluation?.final_test && (
          <p>
            Test rows {data.evaluation.final_test.count ?? '—'} · log loss {data.evaluation.final_test.log_loss?.toFixed(4) ?? '—'} · Brier {data.evaluation.final_test.brier?.toFixed(4) ?? '—'} · top-choice hit {data.evaluation.final_test.top_hit_rate?.toFixed(3) ?? '—'}
          </p>
        )}
        <p>{data?.evaluation?.final_test?.selected_trades_reason}</p>
        {calibration.length > 0 ? (
          <div className="dm-chart">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={calibration}>
                <CartesianGrid stroke="rgba(0,212,255,0.08)" />
                <XAxis dataKey="confidence" stroke="#6b80a8" />
                <YAxis stroke="#6b80a8" />
                <Tooltip />
                <Bar dataKey="empirical" fill="#3b82f6" name="Empirical hit rate" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : <p>No calibration bins yet.</p>}
        {data?.evaluation?.walk_forward && data.evaluation.walk_forward.length > 0 && (
          <ul>
            {data.evaluation.walk_forward.map((fold) => (
              <li key={fold.fold}>Fold {fold.fold}: log loss {fold.log_loss.toFixed(4)} on {fold.validation_rows} pre-test rows</li>
            ))}
          </ul>
        )}
      </section>

      <section className="dm-panel">
        <h2>Trade history</h2>
        {data && data.trades.length > 0 ? (
          <table>
            <thead>
              <tr><th>Contract</th><th>Digit</th><th>Status</th><th>Ask</th><th>Total payout</th><th>Profit</th><th>Mode</th></tr>
            </thead>
            <tbody>
              {data.trades.map((trade) => (
                <tr key={trade.broker_contract_id}>
                  <td>{trade.broker_contract_id}</td>
                  <td>{trade.digit}</td>
                  <td>{trade.status}</td>
                  <td>{trade.buy_price ?? '—'}</td>
                  <td>{trade.total_payout ?? '—'}</td>
                  <td>{trade.profit ?? '—'}</td>
                  <td>{trade.mode}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p>No demo contracts recorded.</p>}
      </section>

      <section className="dm-grid">
        <article>
          <h2>History</h2>
          <p>{data?.history_job ? `${data.history_job.status} · ${data.history_job.ticks_stored}/${data.history_job.target_ticks}` : 'No download yet'}</p>
          <p className="dm-note">{data?.history_job?.note || data?.history_job?.error || 'The touch bot ticks can be copied. A broker download is separate.'}</p>
          <div className="dm-actions">
            {press('saved', 'Use saved ticks', () => post('/api/digitmatch/history/use-saved', {}, 'saved', 'Saved ticks copied.'))}
            {press('download', 'Download history', () => post('/api/digitmatch/history/start', { target_ticks: 100000 }, 'download', 'History download started.'))}
            {press('cancel', 'Cancel download', () => post('/api/digitmatch/history/cancel', {}, 'cancel', 'Download cancel requested.'))}
            <a href={`${API}/api/digitmatch/history/export`}>Export CSV</a>
          </div>
        </article>
        <article>
          <h2>Training</h2>
          <p>{data?.train_job ? `${data.train_job.status} · ${data.train_job.progress}` : 'Idle'}</p>
          <p className="dm-note">{data?.train_job?.error || 'A finished train stays a candidate until you promote it.'}</p>
          <div className="dm-actions">
            {press('train', 'Train candidate', () => post('/api/digitmatch/train', { enable_mlp: false }, 'train', 'Training started. It stays a candidate until you promote it.'))}
          </div>
          <ul>
            {models.map((model) => (
              <li key={model.id}>
                #{model.id} {model.name} {model.is_active ? '(active)' : ''}
                {!model.is_active && (
                  <button type="button" className="dm-btn" disabled={busyKey !== null} onClick={() => post(`/api/digitmatch/models/${model.id}/promote`, {}, `promote-${model.id}`, `Model #${model.id} promoted for live scoring.`)}>
                    {busyKey === `promote-${model.id}` ? 'Working…' : 'Promote'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className="dm-panel">
        <h2>Risk · reset timezone {data?.risk.reset_timezone}</h2>
        <form
          className="dm-form"
          onSubmit={(event) => {
            event.preventDefault();
            void post('/api/digitmatch/risk', {
              stake: Number(stake),
              cooldown_seconds: Number(cooldown),
              max_trades_per_day: Number(maxTrades),
              daily_loss_limit: Number(lossLimit),
              daily_profit_stop: Number(profitStop),
              reset_timezone: timezone,
              margin: Number(margin),
            }, 'risk', 'Limits saved.');
          }}
        >
          <label>Stake<input value={stake} onChange={(event) => setStake(event.target.value)} /></label>
          <label>Cooldown seconds<input value={cooldown} onChange={(event) => setCooldown(event.target.value)} /></label>
          <label>Max trades / day<input value={maxTrades} onChange={(event) => setMaxTrades(event.target.value)} /></label>
          <label>Daily loss limit<input value={lossLimit} onChange={(event) => setLossLimit(event.target.value)} /></label>
          <label>Daily profit stop<input value={profitStop} onChange={(event) => setProfitStop(event.target.value)} /></label>
          <label>Margin above break-even<input value={margin} onChange={(event) => setMargin(event.target.value)} /></label>
          <label>Reset timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></label>
          <button type="submit" className="dm-btn" disabled={busyKey !== null}>{busyKey === 'risk' ? 'Working…' : 'Save limits'}</button>
        </form>
        <p className="dm-note">Maximum simultaneous bot contracts stays at 1. Stake is not increased after a loss.</p>
        <p className="dm-note">{data?.freshness_policy}</p>
      </section>
    </div>
  );
}
