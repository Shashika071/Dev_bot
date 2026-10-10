import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, BarChart2, Brain, CheckCircle, Database, Hash, Loader2, RefreshCw,
} from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, BarChart, Bar,
} from 'recharts';
import './digitmatch.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const SKIP_REASON: Record<string, string> = {
  stale_tick: 'The latest tick is too old, so no digit was chosen.',
  no_tick: 'No tick has arrived yet.',
  tick_clock_ahead: 'The tick clock is ahead of this server, so the check was skipped.',
  fresh_tick: 'The tick is fresh.',
  observe_mode: 'Mode is Off, so no demo buy is placed.',
  no_model: 'No promoted model yet. Train one, then press Promote.',
  model_expired: 'The promoted model is no longer valid.',
  emergency_stop: 'Emergency stop is on.',
  uncertain_purchase: 'An earlier buy is still uncertain, so new orders stay blocked.',
  execution_lock_held: 'Another order is still open.',
  stale_proposal: 'The price quote is too old.',
  proposal_older_than_tick: 'A newer tick arrived before the quote could be used.',
  proposal_missing_spot: 'The quote did not include a price.',
  tick_arrived_before_submit: 'A newer tick arrived before the order was sent.',
  purchase_rejected: 'The broker rejected the order.',
  purchase_not_sent: 'The order was not sent.',
  risk_unavailable: 'Risk limits could not be read.',
  unknown_mode: 'The trading mode is not recognized.',
};

function decisionSentence(decision: { action: string; reason: string; digit: number | null } | null): string {
  if (!decision) return 'Waiting for the first check.';
  const detail = SKIP_REASON[decision.reason] || decision.reason.split('_').join(' ');
  if (decision.action === 'buy') return `Bought digit ${decision.digit ?? '—'}.`;
  return detail;
}

function shownDecision(data: Dashboard | null): string {
  if (data?.decision?.reason === 'stale_tick' && data.freshness?.stale === false) {
    return 'A new tick is in. The next check can choose a digit.';
  }
  return decisionSentence(data?.decision ?? null);
}

function estimateTrainSeconds(ticks: number): number {
  return 2 * (2.9e-9 * ticks * ticks + 3.2e-5 * ticks + 40);
}

function formatTrainMinutes(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

function trainTimeLabel(ticks: number, progress?: string, cap = 200000): string {
  const used = Math.min(ticks, cap);
  if (used < 1050) return 'Need about 1,050 clean ticks before a train can start.';
  let seconds = estimateTrainSeconds(used);
  const feature = progress ? /Building features, (\d+) of (\d+)/.exec(progress) : null;
  if (feature) {
    const done = Number(feature[1]);
    const total = Number(feature[2]);
    const fraction = total > 0 ? Math.min(1, done / total) : 0;
    seconds = Math.max(30, seconds * (1 - 0.7 * fraction));
  } else if (progress && (progress.startsWith('Fitting') || progress.startsWith('Saving') || progress.startsWith('Using the latest'))) {
    seconds = Math.max(20, estimateTrainSeconds(used) * (progress.startsWith('Fitting') || progress.startsWith('Saving') ? 0.2 : 1));
  }
  const low = formatTrainMinutes(seconds);
  const high = formatTrainMinutes(seconds * 1.6);
  const range = low === high ? low : `${low}–${high}`;
  const capped = ticks > cap ? ` The latest ${cap.toLocaleString()} of ${ticks.toLocaleString()} ticks are used.` : '';
  return progress
    ? `Estimated time left: about ${range}.${capped}`
    : `Estimated time: about ${range} for ${used.toLocaleString()} ticks.${capped}`;
}

function trainingBanner(
  job: { status: string; progress: string; error: string | null; model_id: number | null } | null,
  ticks: number,
  cap = 200000,
) {
  if (!job) return null;
  if (job.status === 'queued' || job.status === 'running') {
    return (
      <div className="alert alert-success">
        <Loader2 size={14} className="spin" />
        <div>
          Training is running. {job.progress || 'Waiting for the trainer.'} {trainTimeLabel(ticks, job.progress, cap)} You can leave this page. This line is still here when you come back.
        </div>
      </div>
    );
  }
  if (job.status === 'done') {
    return (
      <div className="alert alert-success">
        <CheckCircle size={14} />
        <div>
          Training finished{job.model_id ? ` as model #${job.model_id}` : ''}. It is saved, and the desk does not use it until you press Promote.
        </div>
      </div>
    );
  }
  if (job.status === 'error') {
    return (
      <div className="alert alert-error">
        <AlertTriangle size={14} />
        <div>{job.error || 'Training stopped.'}</div>
      </div>
    );
  }
  return null;
}

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
  train_tick_limit?: number;
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

  async function post(path: string, body: unknown) {
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
    }
  }

  function modeButton(label: string, path: string, body: unknown, pressed: boolean) {
    return (
      <button
        type="button"
        className={`btn ${pressed ? 'btn-primary' : 'btn-ghost'}`}
        aria-pressed={pressed}
        onClick={() => post(path, body)}
      >
        {label}
      </button>
    );
  }

  const label = mode === 'demo_explore' ? 'ON' : mode === 'demo_filtered' ? 'ON if quote passes' : 'OFF';

  return (
    <div className="glass dash-account-bar dm-dash-control">
      <strong>Digit Matches</strong>
      <span className={mode === 'observe' ? 'badge badge-dim' : 'badge badge-green'}>{label}</span>
      {paused && <span className="badge badge-amber">Paused</span>}
      {stopped && <span className="badge badge-amber">Emergency stop</span>}
      {modeButton('Off', '/api/digitmatch/mode', { mode: 'observe' }, mode === 'observe')}
      {modeButton('On', '/api/digitmatch/mode', { mode: 'demo_explore' }, mode === 'demo_explore')}
      {modeButton('On if quote passes', '/api/digitmatch/mode', { mode: 'demo_filtered' }, mode === 'demo_filtered')}
      <button type="button" className={`btn ${paused ? 'btn-primary' : 'btn-ghost'}`} aria-pressed={paused} onClick={() => post(paused ? '/api/digitmatch/resume' : '/api/digitmatch/pause', {})}>
        {paused ? 'Resume' : 'Pause'}
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

  async function post(path: string, body: unknown, okText: string) {
    setNotice(null);
    try {
      const response = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      });
      if (!response.ok) throw new Error(await readError(response));
      const payload = await response.json().catch(() => null) as { note?: string } | null;
      const text = (payload?.note || okText || '').trim();
      if (text) setNotice({ ok: true, text });
      else setNotice(null);
      setError(null);
      await load();
    } catch (err) {
      setNotice({ ok: false, text: err instanceof Error ? err.message : 'Request failed' });
    }
  }

  function press(label: string, run: () => void, opts?: { on?: boolean; tone?: 'warn' | 'danger'; disabled?: boolean }) {
    const selected = !!opts?.on;
    const danger = opts?.tone === 'danger';
    return (
      <button
        type="button"
        className={`btn ${selected ? 'btn-primary' : 'btn-ghost'}`}
        style={danger && selected ? { background: 'var(--red)', color: '#fff' } : undefined}
        aria-pressed={selected}
        disabled={!!opts?.disabled}
        onClick={run}
      >
        {label}
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

  const trainingNow = data?.train_job?.status === 'queued' || data?.train_job?.status === 'running';
  const trainAlert = trainingBanner(data?.train_job ?? null, data?.ticks_stored ?? 0, data?.train_tick_limit ?? 200000);
  const trades = data?.trades ?? [];
  const wins = trades.filter((trade) => (trade.profit ?? 0) > 0).length;
  const losses = trades.filter((trade) => (trade.profit ?? 0) < 0).length;
  const settled = trades.filter((trade) => trade.profit != null).length;
  const winRate = wins + losses > 0 ? `${Math.round((wins / (wins + losses)) * 100)}%` : '—';
  const modeOn = data?.mode === 'demo_explore' || data?.mode === 'demo_filtered';
  const modeLabel = data?.mode === 'demo_explore' ? 'ON' : data?.mode === 'demo_filtered' ? 'ON IF QUOTE PASSES' : 'OFF';
  const online = data?.connection_status === 'online' && !!data?.demo_verified;
  const symbol = data?.instrument.resolved_symbol || 'R_100';
  const timezoneChoices = ['Asia/Colombo', 'UTC', 'Europe/London', 'America/New_York', 'Asia/Dubai', 'Asia/Singapore'];
  if (timezone && !timezoneChoices.includes(timezone)) timezoneChoices.unshift(timezone);
  const marginChoices = ['0', '0.01', '0.02', '0.05'];
  if (margin && !marginChoices.includes(margin)) marginChoices.unshift(margin);

  return (
    <>
      <div className="glass page-header" style={{ flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h1 className="page-title">Digit Matches</h1>
          <p className="page-subtitle">
            Monitoring {symbol}
            &nbsp;·&nbsp;live {(data?.ticks_stored ?? 0).toLocaleString()}
            &nbsp;·&nbsp;DB {(data?.touch_ticks_available ?? 0).toLocaleString()} ticks saved
          </p>
        </div>
        <div className="flex gap-3 items-center" style={{ flexWrap: 'wrap' }}>
          <span className="badge badge-amber"><AlertTriangle size={11} />DEMO ONLY</span>
          {press('Off', () => post('/api/digitmatch/mode', { mode: 'observe' }, 'Off. No demo buy will be placed.'), { on: (data?.mode || 'observe') === 'observe' })}
          {press('On', () => post('/api/digitmatch/mode', { mode: 'demo_explore' }, 'On. Demo trades can be placed at the fixed stake.'), { on: data?.mode === 'demo_explore' })}
          {press('On if quote passes', () => post('/api/digitmatch/mode', { mode: 'demo_filtered' }, 'On only when the live quote passes the filter.'), { on: data?.mode === 'demo_filtered' })}
          {press(data?.paused ? 'Resume' : 'Pause', () => post(data?.paused ? '/api/digitmatch/resume' : '/api/digitmatch/pause', {}, data?.paused ? 'Pause cleared.' : 'Paused. New orders are blocked.'), { on: !!data?.paused })}
          {press('Emergency stop', () => post('/api/digitmatch/emergency-stop', {}, 'Emergency stop is on. An open contract is not cancelled.'), { on: !!data?.emergency_stop, tone: 'danger' })}
          {data?.emergency_stop && press('Clear stop', () => post('/api/digitmatch/emergency-stop/clear', {}, 'Emergency stop cleared.'))}
          <button type="button" className="btn btn-ghost" onClick={() => void load()}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {notice && !trainAlert && (
        <div className={notice.ok ? 'alert alert-success' : 'alert alert-error'}>
          {notice.ok ? <CheckCircle size={14} /> : <AlertTriangle size={14} />}
          <div>{notice.text}</div>
        </div>
      )}
      {trainAlert}
      {error && <div className="alert alert-error"><AlertTriangle size={14} /><div>{error}</div></div>}
      {data?.last_error && <div className="alert alert-error"><AlertTriangle size={14} /><div>{data.last_error}</div></div>}
      {data?.reconciliation.blocked && (
        <div className="alert alert-error">
          <AlertTriangle size={14} />
          <div>
            Uncertain purchase. {data.reconciliation.reason || 'Match the broker statement before any new order.'}
            New orders stay blocked. This screen does not retry the buy.
          </div>
        </div>
      )}

      <div className="glass dash-account-bar">
        <div className="dash-account-main">
          <Database size={14} style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="flex items-center gap-2" style={{ flexWrap: 'wrap', marginBottom: 4 }}>
              <strong className="text-sm">Trade account</strong>
              {modeOn
                ? <span className="badge badge-green">{modeLabel}</span>
                : <span className="badge badge-dim">OFF</span>}
              {online
                ? <span className="badge badge-green">LIVE</span>
                : <span className="badge badge-dim">{data?.connection_status || 'offline'}</span>}
              {data?.demo_verified && <span className="badge badge-amber">DEMO</span>}
              {data?.paused && <span className="badge badge-amber">Paused</span>}
              {data?.emergency_stop && <span className="badge badge-amber">Emergency stop</span>}
            </div>
            <div className="font-mono text-xs dash-account-grid">
              <span>
                {data?.loginid || 'No account yet'}
                <span className="text-dim">
                  {' '}· {data?.credential_source === 'touch_bot' ? 'same token as the touch bot' : data?.credential_source === 'digitmatch_env' ? 'Digit Matches token' : 'not configured'}
                </span>
              </span>
              <span>
                Balance{' '}
                <strong className="text-primary">{money(data?.balance, data?.currency || null)}</strong>
              </span>
            </div>
            <p className="text-xs text-dim" style={{ marginTop: 6 }}>{STATE_COPY[state] || 'Loading the research desk.'}</p>
            <div className="dash-trade-stats">
              <div className="dash-stat">
                <div className="dash-stat-label">Won</div>
                <div className="dash-stat-value text-green">{wins}</div>
                <div className="dash-stat-sub">saved trades</div>
              </div>
              <div className="dash-stat">
                <div className="dash-stat-label">Lost</div>
                <div className="dash-stat-value" style={{ color: 'var(--red)' }}>{losses}</div>
                <div className="dash-stat-sub">saved trades</div>
              </div>
              <div className="dash-stat">
                <div className="dash-stat-label">Settled</div>
                <div className="dash-stat-value">{settled}</div>
                <div className="dash-stat-sub">open {data?.active_contract ? 1 : 0}</div>
              </div>
              <div className="dash-stat">
                <div className="dash-stat-label">Win rate</div>
                <div className="dash-stat-value">{winRate}</div>
                <div className="dash-stat-sub">
                  Today P/L{' '}
                  <strong style={{ color: (data?.pnl.daily ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {(data?.pnl.daily ?? 0) >= 0 ? '+' : ''}{(data?.pnl.daily ?? 0).toFixed(2)}
                  </strong>
                </div>
              </div>
              <div className="dash-stat">
                <div className="dash-stat-label">Recent P/L</div>
                <div className="dash-stat-value" style={{ color: (data?.pnl.cumulative ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {(data?.pnl.cumulative ?? 0) >= 0 ? '+' : ''}{(data?.pnl.cumulative ?? 0).toFixed(2)}
                </div>
                <div className="dash-stat-sub">{data?.currency || 'USD'}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="alert alert-warning">
        <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
        <div>
          <div className="text-sm">{shownDecision(data)}</div>
          <div className="text-xs font-mono text-dim" style={{ marginTop: 4 }}>
            {data?.instrument.resolved_display || data?.instrument.expected_display_name}
            {' · '}{data?.contract.type} · {data?.contract.duration_ticks} ticks
            {' · '}tick age {data?.freshness.age_seconds == null ? '—' : `${data.freshness.age_seconds.toFixed(1)}s`}
          </div>
        </div>
      </div>

      <div className="glass chart-panel">
        <div className="section-header">
          <h3 className="section-title"><Hash size={16} />Estimated probability</h3>
        </div>
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
          <p className="text-sm text-secondary">No live percentages yet. These boxes stay blank until a fresh tick and a promoted model are both available.</p>
        )}
        <div className="dash-trade-stats">
          <div className="dash-stat">
            <div className="dash-stat-label">Selected digit</div>
            <div className="dash-stat-value">{data?.decision?.digit ?? '—'}</div>
          </div>
          <div className="dash-stat">
            <div className="dash-stat-label">Break-even</div>
            <div className="dash-stat-value">{data?.decision?.break_even == null ? '—' : `${(data.decision.break_even * 100).toFixed(1)}%`}</div>
          </div>
          <div className="dash-stat">
            <div className="dash-stat-label">Expected value</div>
            <div className="dash-stat-value">{data?.decision?.expected_value == null ? '—' : data.decision.expected_value.toFixed(3)}</div>
            <div className="dash-stat-sub">Net of stake</div>
          </div>
          <div className="dash-stat">
            <div className="dash-stat-label">Ask</div>
            <div className="dash-stat-value">{data?.decision?.ask_price ?? '—'}</div>
          </div>
          <div className="dash-stat">
            <div className="dash-stat-label">Total payout</div>
            <div className="dash-stat-value">{data?.decision?.total_payout ?? '—'}</div>
          </div>
        </div>
        <p className="text-xs text-dim" style={{ marginTop: 8 }}>{shownDecision(data)}</p>
      </div>

      <div className="grid-2">
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Hash size={16} />Active contract</h3>
          </div>
          {data?.active_contract ? (
            <div className="font-mono text-sm">
              <p>Broker id {data.active_contract.broker_contract_id}</p>
              <p>Digit {data.active_contract.digit} · {data.active_contract.status}</p>
              <p>Stake paid {money(data.active_contract.buy_price, data.currency)} · total payout {money(data.active_contract.total_payout, data.currency)}</p>
            </div>
          ) : <p className="text-sm text-secondary">No open bot contract.</p>}
        </div>
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Brain size={16} />Model</h3>
          </div>
          <p className="text-sm">{data?.model ? `${data.model.name} #${data.model.id}` : 'No promoted model'}</p>
          <p className="text-xs text-dim">{data?.evaluation?.edge_statement || data?.target_note}</p>
          <p className="text-xs text-dim">Edge claimed: no</p>
        </div>
      </div>

      <div className="glass chart-panel">
        <div className="section-header">
          <h3 className="section-title"><BarChart2 size={16} />Equity and drawdown</h3>
        </div>
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
        ) : <p className="text-sm text-secondary">No settled demo trades yet, so there is no equity curve.</p>}
      </div>

      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><BarChart2 size={16} />Historical evaluation</h3>
        </div>
        <p className="text-sm text-secondary">{data?.evaluation?.comparison_to_uniform || 'Train a candidate to see held-out log loss. That result is not a trading profit.'}</p>
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
        ) : <p className="text-sm text-secondary">No calibration bins yet.</p>}
        {data?.evaluation?.walk_forward && data.evaluation.walk_forward.length > 0 && (
          <ul className="dm-folds">
            {data.evaluation.walk_forward.map((fold) => (
              <li key={fold.fold}>Fold {fold.fold}: log loss {fold.log_loss.toFixed(4)} on {fold.validation_rows} pre-test rows</li>
            ))}
          </ul>
        )}
      </div>

      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Database size={16} />Trade history</h3>
        </div>
        {data && data.trades.length > 0 ? (
          <table className="dm-table">
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
        ) : <p className="text-sm text-secondary">No demo contracts recorded.</p>}
      </div>

      <div className="grid-2">
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Database size={16} />History</h3>
          </div>
          <p className="font-mono text-sm">{data?.history_job ? `${data.history_job.status} · ${data.history_job.ticks_stored}/${data.history_job.target_ticks}` : 'No download yet'}</p>
          <p className="text-xs text-dim">{data?.history_job?.note || data?.history_job?.error || 'The touch bot ticks can be copied. A broker download is separate.'}</p>
          <div className="flex gap-2" style={{ flexWrap: 'wrap', marginTop: 12 }}>
            {press('Use saved ticks', () => post('/api/digitmatch/history/use-saved', {}, 'Copy started. The History line shows the count.'))}
            {press('Download history', () => post('/api/digitmatch/history/start', { target_ticks: 100000 }, 'History download started.'))}
            {press('Cancel download', () => post('/api/digitmatch/history/cancel', {}, 'Download cancel requested.'))}
            <a className="btn btn-ghost" href={`${API}/api/digitmatch/history/export`}>Export CSV</a>
          </div>
        </div>
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Brain size={16} />Training</h3>
          </div>
          <p className="font-mono text-sm">
            {data?.train_job
              ? data.train_job.status === 'error'
                ? `error · ${data.train_job.error || 'Training stopped.'}`
                : `${data.train_job.status} · ${data.train_job.progress}`
              : 'Idle'}
          </p>
          {data?.train_job?.status !== 'error' && (
            <p className="text-sm">{trainTimeLabel(data?.ticks_stored ?? 0, trainingNow ? data?.train_job?.progress : undefined, data?.train_tick_limit ?? 200000)}</p>
          )}
          <p className="text-xs text-dim">
            {data?.train_job?.status === 'error'
              ? data.train_job.error || 'Training stopped.'
              : trainingNow
                ? 'This step updates while the job runs. The button stays off until it finishes.'
                : data?.train_job?.status === 'done'
                  ? 'Finished and saved. Press Promote on the model you want the desk to use.'
                  : 'Press Train candidate once. A second press does nothing while this job is running.'}
          </p>
          <div className="flex gap-2" style={{ flexWrap: 'wrap', marginTop: 12 }}>
            <button
              type="button"
              className="btn btn-primary"
              disabled={trainingNow}
              onClick={() => post('/api/digitmatch/train', { enable_mlp: false }, '')}
            >
              {trainingNow ? <Loader2 size={14} className="spin" /> : <Brain size={14} />}
              Train candidate
            </button>
          </div>
          <ul className="dm-folds">
            {models.map((model) => (
              <li key={model.id}>
                <span className="font-mono text-sm">#{model.id} {model.name} {model.is_active ? '(active)' : ''}</span>
                {!model.is_active && (
                  <button type="button" className="btn btn-ghost" onClick={() => post(`/api/digitmatch/models/${model.id}/promote`, {}, `Model #${model.id} promoted for live scoring.`)}>
                    Promote
                  </button>
                )}
                {model.is_active && <span className="badge badge-green">Active</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="glass">
        <div className="section-header">
          <h3 className="section-title">Risk limits</h3>
          <span className="badge badge-dim">{data?.risk.reset_timezone}</span>
        </div>
        <form
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
            }, 'Limits saved.');
          }}
        >
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">Stake</label>
              <input className="form-input" value={stake} onChange={(event) => setStake(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Cooldown seconds</label>
              <input className="form-input" value={cooldown} onChange={(event) => setCooldown(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Max trades / day</label>
              <input className="form-input" value={maxTrades} onChange={(event) => setMaxTrades(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Daily loss limit</label>
              <input className="form-input" value={lossLimit} onChange={(event) => setLossLimit(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Daily profit stop</label>
              <input className="form-input" value={profitStop} onChange={(event) => setProfitStop(event.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Margin above break-even</label>
              <select className="form-select" value={margin} onChange={(event) => setMargin(event.target.value)}>
                {marginChoices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Reset timezone</label>
              <select className="form-select" value={timezone} onChange={(event) => setTimezone(event.target.value)}>
                {timezoneChoices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
              </select>
            </div>
          </div>
          <button type="submit" className="btn btn-primary">Save limits</button>
        </form>
        <p className="text-xs text-dim" style={{ marginTop: 12 }}>Maximum simultaneous bot contracts stays at 1. Stake is not increased after a loss.</p>
        <p className="text-xs text-dim">{data?.stop_note}</p>
      </div>
    </>
  );
}
