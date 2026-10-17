import { useEffect, useState } from 'react';
import { accuracyFeatureGroupLabel, accuracyProfileLabel } from '../chart/state';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
type PolicyStats = {
  signals: number; touches: number; misses: number; touch_rate: number | null;
  signals_per_full_day: number; full_day_cluster_ci95: number[] | null;
};
type Metrics = { brier: number; auc: number; policy: PolicyStats };
type AccuracyStatus = {
  ready: boolean; running: boolean; reason: string; error: string | null;
  feed_fresh: boolean; feed_age_seconds: number | null;
  history_loading?: boolean;
  feature_profile?: string; feature_groups?: string[];
  signals_today: number; cooldown_seconds?: number;
  last_tick: { epoch: number; price: number } | null;
  auto_trade?: {
    accuracy_enabled?: boolean;
    setup_enabled?: boolean;
    allowed?: boolean;
    reason?: string;
  };
  train?: {
    status?: 'idle' | 'running' | 'done' | 'error';
    progress?: string;
    error?: string | null;
    result?: {
      selected_model?: string;
      threshold?: number;
      run_id?: string;
      profile?: string;
    } | null;
  };
  report: null | {
    run_id: string; ticks: number; span_days: number; feature_count: number;
    feature_profile?: string; feature_groups?: Record<string, string[]>;
    selection: { model: string; threshold: number }; test: Metrics;
    comparison: Record<string, { selection: Metrics; test: Metrics; feature_profile?: string }>;
  };
  signals: Array<{
    signal_id: string; direction: string; barrier_input: string; estimated_probability: number;
    decision_epoch: number; expiry_epoch: number; resolved: boolean; outcome: boolean | null;
    auto_trade?: { ok?: boolean; contract_id?: string; stake?: number; error?: string };
  }>;
};
const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;

export function AccuracyPage() {
  const [status, setStatus] = useState<AccuracyStatus | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [tradeBusy, setTradeBusy] = useState(false);
  const [tradeMsg, setTradeMsg] = useState('');
  const [trainBusy, setTrainBusy] = useState(false);
  const [trainMsg, setTrainMsg] = useState('');
  const [cooldown, setCooldown] = useState(5760);
  const [cooldownDirty, setCooldownDirty] = useState(false);
  const [cooldownBusy, setCooldownBusy] = useState(false);
  const [cooldownMsg, setCooldownMsg] = useState('');
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const response = await fetch(`${API}/accuracy/status`);
        if (!response.ok) throw new Error('Accuracy mode is unavailable; restart the updated backend.');
        const data: AccuracyStatus = await response.json();
        if (alive) {
          setStatus(data);
          setError('');
          if (typeof data.cooldown_seconds === 'number' && !cooldownDirty && !cooldownBusy) {
            setCooldown(data.cooldown_seconds);
          }
        }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : String(e)); }
    };
    void refresh();
    const timer = setInterval(refresh, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [cooldownDirty, cooldownBusy]);
  const control = async (action: 'start' | 'stop') => {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${API}/accuracy/${action}`, { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not change signal mode');
      setStatus(data);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const startTrain = async () => {
    setTrainBusy(true); setTrainMsg(''); setError('');
    try {
      const response = await fetch(`${API}/accuracy/train`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: 'technical_v2', activate: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Train failed to start');
      setTrainMsg(data.message || 'Training started…');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTrainBusy(false);
    }
  };
  const setAccuracyAutoTrade = async (enabled: boolean) => {
    setTradeBusy(true); setTradeMsg(''); setError('');
    try {
      const response = await fetch(`${API}/accuracy/auto-trade`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accuracy_auto_trade_enabled: enabled }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not update auto-trade');
      setTradeMsg(data.message || (enabled ? 'Accuracy auto-trade on' : 'Accuracy auto-trade off'));
      if (data.status) setStatus(data.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTradeBusy(false);
    }
  };
  const saveCooldown = async () => {
    setCooldownBusy(true); setCooldownMsg(''); setError('');
    const seconds = Math.max(600, Math.min(7200, Math.round(Number(cooldown) || 5760)));
    setCooldown(seconds);
    try {
      const response = await fetch(`${API}/accuracy/cooldown`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ accuracy_cooldown_seconds: seconds }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = data.detail;
        const msg = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ')
            : 'Could not update cooldown';
        throw new Error(msg);
      }
      const saved = Number(data.accuracy_cooldown_seconds ?? seconds);
      setCooldown(saved);
      setCooldownDirty(false);
      setCooldownMsg(data.message || `Cooldown set to ${saved}s`);
      if (data.status) setStatus(data.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCooldownBusy(false);
    }
  };
  const report = status?.report;
  const test = report?.test.policy;
  const featureGroups = Object.entries(report?.feature_groups || {});
  const profile = report?.feature_profile || status?.feature_profile || 'compact_v1';
  const accuracyTradeOn = Boolean(status?.auto_trade?.accuracy_enabled);
  const tradeAllowed = Boolean(status?.auto_trade?.allowed);
  const train = status?.train;
  const training = train?.status === 'running' || trainBusy;
  return <div style={{ maxWidth: 1080 }}>
    <div className="glass page-header" style={{ marginBottom: '1.25rem' }}>
      <div><h1 className="page-title">Touch Accuracy</h1>
        <p className="page-subtitle">R_100 · ±0.01 price points · 9 minutes · either direction</p></div>
      <span className={`badge ${status?.running && status.feed_fresh && !status.history_loading ? 'badge-green' : 'badge-amber'}`}>
        {status?.running ? status.history_loading ? 'Loading model history' : status.feed_fresh ? 'Watching live ticks' : 'Waiting for live ticks' : 'Stopped'}
      </span>
    </div>

    <div className="glass" style={{ marginBottom: '1.25rem' }}>
      <h3 className="section-title">Train accuracy model</h3>
      <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
        Retrains the ±0.01 / 9m Touch Accuracy profile from staged parquet (same history as Train page).
        Does not change Dashboard ML models. Stop live accuracy signals before training.
      </p>
      <div style={{ display: 'flex', gap: '.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          className="btn btn-primary"
          disabled={training || Boolean(status?.running)}
          onClick={() => void startTrain()}
        >
          {training ? 'Training…' : status?.ready ? 'Retrain accuracy model' : 'Train accuracy model'}
        </button>
        <span className={`badge ${train?.status === 'done' ? 'badge-green' : train?.status === 'error' ? 'badge-amber' : 'badge-dim'}`}>
          {train?.status || 'idle'}
        </span>
      </div>
      {(train?.progress || trainMsg) && (
        <p className="text-sm text-dim" style={{ marginTop: '.75rem' }}>{train?.progress || trainMsg}</p>
      )}
      {train?.status === 'done' && train.result?.selected_model && (
        <div className="alert alert-success" style={{ marginTop: '.75rem' }}>
          Selected {train.result.selected_model}
          {train.result.threshold != null ? ` · floor ${(train.result.threshold * 100).toFixed(1)}%` : ''}
          {train.result.run_id ? ` · ${train.result.run_id}` : ''}
        </div>
      )}
      {train?.status === 'error' && train.error && (
        <div className="alert alert-error" style={{ marginTop: '.75rem' }}>{train.error}</div>
      )}
      {status?.running && (
        <p className="text-sm text-dim" style={{ marginTop: '.75rem' }}>Stop accuracy signals first to unlock Train.</p>
      )}
    </div>

    <div className="glass" style={{ marginBottom: '1.25rem' }}>
      <h3 className="section-title">Accuracy-focused signals</h3>
      <p style={{ margin: '.75rem 0' }}>
        This mode selects signals using calibrated touch probabilities for both directions.
        The model and confidence threshold come from an earlier selection period. The trained
        feature profile below shows the inputs supplied to that model.
      </p>
      <p className="text-sm text-dim" style={{ marginBottom: '1rem' }}>
        Target: 10–15 signals per day, capped at 15, with a configurable market-rest cooldown between signals
        (default 96 minutes / 5760s). Entries below the trained confidence threshold are skipped.
        Signals and simulated outcomes are recorded here.
      </p>
      <div style={{ display: 'flex', gap: '.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" disabled={busy || training || !status?.ready || status.running} onClick={() => void control('start')}>Start accuracy signals</button>
        <button className="btn btn-ghost" disabled={busy || !status?.running} onClick={() => void control('stop')}>Stop</button>
        <span>{status?.signals_today ?? 0} / 15 today · Asia/Colombo</span>
      </div>
      <div style={{ display: 'flex', gap: '.75rem', alignItems: 'flex-end', flexWrap: 'wrap', marginTop: '1rem' }}>
        <div>
          <label className="form-label">Cooldown (seconds)</label>
          <input
            type="number"
            className="form-input"
            min={600}
            max={7200}
            step={60}
            value={cooldown}
            disabled={cooldownBusy}
            onChange={e => {
              setCooldown(Number(e.target.value));
              setCooldownDirty(true);
            }}
            style={{ width: 140 }}
          />
          <div className="form-hint">
            Min 600s (10 min). Current ≈ {(cooldown / 60).toFixed(0)} min between signals.
          </div>
        </div>
        <button className="btn btn-ghost" disabled={cooldownBusy} onClick={() => void saveCooldown()}>
          {cooldownBusy ? 'Saving…' : 'Save cooldown'}
        </button>
      </div>
      {cooldownMsg && <p className="text-sm text-dim" style={{ marginTop: '.5rem' }}>{cooldownMsg}</p>}
      <p className="text-sm text-dim" style={{ marginTop: '.75rem' }}>{status?.reason || 'Loading profile…'}</p>
      {(error || status?.error) && <div className="alert alert-error" style={{ marginTop: '.75rem' }}>{error || status?.error}</div>}
      {status && !status.ready && !training && <p style={{ marginTop: '1rem' }}>No accuracy profile yet — use <strong>Train accuracy model</strong> above (needs staged parquet).</p>}
    </div>

    <div className="glass" style={{ marginBottom: '1.25rem' }}>
      <h3 className="section-title">Optional auto-trade</h3>
      <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
        When a Touch Accuracy signal fires, optionally place the matching One-Touch order
        (±0.01, 540s). Uses the same Setup token, stake, confirmation, and server
        <code> EXECUTION_ALLOWED </code> gate as Dashboard auto-trade.
      </p>
      <label className="text-sm flex items-center gap-2" style={{ cursor: 'pointer', marginBottom: '.75rem' }}>
        <input
          type="checkbox"
          checked={accuracyTradeOn}
          disabled={tradeBusy}
          onChange={e => void setAccuracyAutoTrade(e.target.checked)}
        />
        Enable auto-trade for Touch Accuracy signals
      </label>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <span className={`badge ${accuracyTradeOn ? 'badge-green' : 'badge-dim'}`}>
          Accuracy {accuracyTradeOn ? 'on' : 'off'}
        </span>
        <span className={`badge ${status?.auto_trade?.setup_enabled ? 'badge-green' : 'badge-amber'}`}>
          Setup auto-trade {status?.auto_trade?.setup_enabled ? 'on' : 'off'}
        </span>
        <span className={`badge ${tradeAllowed ? 'badge-green' : 'badge-amber'}`}>
          {tradeAllowed ? 'Orders allowed' : `Blocked: ${status?.auto_trade?.reason || '—'}`}
        </span>
      </div>
      {tradeMsg && <p className="text-sm text-dim" style={{ marginTop: '.75rem' }}>{tradeMsg}</p>}
      {accuracyTradeOn && !tradeAllowed && (
        <div className="alert alert-warning" style={{ marginTop: '.75rem' }}>
          Turn on Setup → Auto-trade: Allow server execution + enable + confirm + daily loss limit + token.
        </div>
      )}
    </div>

    {report && <div className="glass" style={{ marginBottom: '1.25rem' }}>
      <h3 className="section-title">Model inputs · {accuracyProfileLabel(profile)}</h3>
      <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
        {featureGroups.length > 0
          ? `${featureGroups.length} technical-analysis groups supply numeric features to the trained model. The model combines them into one calibrated probability.`
          : 'This compact model receives recent price movement, tick volatility and completed 1-minute/5-minute candle features.'}
        {' '}Inputs use information available at the decision time. Changing the chart timeframe does not change this profile.
      </p>
      {featureGroups.length > 0 && <p className="text-sm text-dim" style={{ marginBottom: '.75rem' }}>
        Uses completed 1-minute, 5-minute and 15-minute candles, plus 1-hour context.
        The 1-hour inputs use 3/6-period trend and price context; standard 14/26-period indicators use the shorter timeframes.
        Price sweeps describe price excursions and rejections; they do not observe stop orders.
      </p>}
      {featureGroups.length > 0 && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '.6rem' }}>
        {featureGroups.map(([group, names]) => <div key={group} className="model-row" style={{ padding: '.65rem' }}>
          <strong className="text-sm">{accuracyFeatureGroupLabel(group)}</strong>
          <span className="text-xs text-dim">{names.length} inputs</span>
        </div>)}
      </div>}
    </div>}
    {report && <>
      <div className="grid-3" style={{ marginBottom: '1.25rem' }}>
        <div className="glass"><div className="text-xs text-dim">SELECTED MODEL</div><div className="stat-value">{report.selection.model}</div><p>Probability floor {percent(report.selection.threshold)}</p></div>
        <div className="glass"><div className="text-xs text-dim">HISTORICAL TOUCH RATE</div><div className="stat-value">{percent(test?.touch_rate)}</div><p>{test?.touches} / {test?.signals} signals</p></div>
        <div className="glass"><div className="text-xs text-dim">REPLAY SIGNALS / DAY</div><div className="stat-value">{test?.signals_per_full_day.toFixed(1)}</div><p>Full calendar days</p></div>
      </div>
      <div className="glass" style={{ marginBottom: '1.25rem' }}>
        <h3 className="section-title">Model comparison</h3>
        <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
          {report.ticks.toLocaleString()} ticks across {report.span_days.toFixed(1)} days; {report.feature_count} past-only features plus direction interactions.
          Fit, calibration, selection, and final replay use separate chronological periods.
          These historical results are exploratory; fresh forward results establish live accuracy.
        </p>
        <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', textAlign: 'left', borderSpacing: '0 .75rem' }}>
          <thead><tr><th>Model</th><th>Final Brier ↓</th><th>Final AUC</th><th>Touch rate</th><th>Signals</th></tr></thead>
          <tbody>{Object.entries(report.comparison).map(([name, result]) => <tr key={name}>
            <td>{name}{name === report.selection.model ? ' · selected' : ''}
              {result.feature_profile && <div className="text-xs text-dim">{accuracyProfileLabel(result.feature_profile)}</div>}
            </td>
            <td>{result.test.brier.toFixed(5)}</td><td>{result.test.auc.toFixed(3)}</td>
            <td>{percent(result.test.policy.touch_rate)}</td><td>{result.test.policy.signals}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
          All comparison rows use the same eligible decision timestamps and the selected probability floor.
          Compact controls provide a benchmark for the expanded inputs.
        </p>
        {test?.full_day_cluster_ci95 && <p className="text-sm text-dim">
          Selected replay: 95% day-cluster interval {percent(test.full_day_cluster_ci95[0])}–{percent(test.full_day_cluster_ci95[1])}.
          This interval describes the tested period and does not guarantee future outcomes.
        </p>}
      </div>
    </>}
    <div className="glass">
      <h3 className="section-title">Forward signals</h3>
      <p className="text-sm text-dim" style={{ margin: '.75rem 0' }}>
        The outcome uses the first tick after an assumed 5-second entry delay and monitors the following contract window.
        Actual entry timing can differ. Enable signal sound in the sidebar for alerts while the app is open.
      </p>
      {!status?.signals.length ? <p>No forward signals recorded in this session yet.</p> : <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', textAlign: 'left', borderSpacing: '0 .75rem' }}>
          <thead><tr><th>Time (Colombo)</th><th>Touch</th><th>Estimated probability</th><th>Outcome</th><th>Auto-trade</th></tr></thead>
          <tbody>{status.signals.map(signal => <tr key={signal.signal_id}>
            <td>{new Date(signal.decision_epoch * 1000).toLocaleString('en-GB', { timeZone: 'Asia/Colombo' })}</td>
            <td>{signal.direction} {signal.barrier_input}</td><td>{percent(signal.estimated_probability)}</td>
            <td>{!signal.resolved ? 'Pending' : signal.outcome === null ? 'Unresolved data' : signal.outcome ? 'Touched' : 'Missed'}</td>
            <td>
              {!signal.auto_trade
                ? '—'
                : signal.auto_trade.ok
                  ? `OK ${signal.auto_trade.contract_id || ''}`.trim()
                  : (signal.auto_trade.error || 'Failed')}
            </td>
          </tr>)}</tbody>
        </table></div>}
    </div>
  </div>;
}
