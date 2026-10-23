import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, Check, ChevronDown, Clock, Download, Loader2, Pause, Play, RefreshCw, ShieldCheck } from 'lucide-react';
import { syntheticApi, IndicatorState, JobState, SyntheticStatus } from '../api/synthetic';
import { SyntheticChart } from '../components/SyntheticChart';
import { pathLabel, SignalPath } from '../components/syntheticChartData';
import { decisionIsFresh } from './syntheticPresentation';
import './SyntheticPage.css';

const FEATURE_NAMES = ['Market trend · HH / HL / LH / LL', 'BOS / CHoCH and market structure', 'Multi-timeframe alignment', 'Support and resistance', 'Equal highs / lows and liquidity sweeps', 'Breakouts, retests and false breaks', 'Candlestick patterns and rejection wicks', 'EMA 20 / 50, RSI, MACD and ATR', 'Momentum and follow-through'];
const TIMEZONE = 'Asia/Colombo';
type Action = 'scan' | 'start' | 'stop' | 'account' | 'download';

function number(value?: number, digits = 2) {
  return value != null && Number.isFinite(value) ? value.toLocaleString('en-GB', { maximumFractionDigits: digits }) : '—';
}
function dateTime(value?: number | string) {
  if (!value) return 'Not yet';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : date.toLocaleString('en-GB', { timeZone: TIMEZONE, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
function detail(value: unknown): string {
  if (value == null) return 'Awaiting analysis.';
  if (Array.isArray(value)) return value.map(detail).join(' · ');
  if (typeof value === 'object') return Object.entries(value).map(([key, entry]) => `${key.replace(/_/g, ' ')}: ${detail(entry)}`).join(' · ');
  return String(value);
}
function tone(value?: string) {
  if (/^(rise|pass|ready|up|bullish|confirmed|completed)$/i.test(value || '')) return 'positive';
  if (/^(fall|block|blocked|error|failed|bearish|down)$/i.test(value || '')) return 'negative';
  return 'neutral';
}
function Direction({ value = 'NO TRADE', large = false }: { value?: string; large?: boolean }) {
  const display = ['RISE', 'FALL'].includes(value) ? value : 'NO TRADE';
  return <span className={`sy-direction sy-${tone(display)} ${large ? 'sy-direction-large' : ''}`}>
    {display === 'RISE' ? <ArrowUpRight /> : display === 'FALL' ? <ArrowDownRight /> : <Pause />}
    {display}
  </span>;
}
function IndicatorCard({ name, state, type }: { name: string; state?: IndicatorState; type: 'frama' | 'range' }) {
  const bias = state?.direction || 'Waiting';
  return <div className="sy-indicator">
    <div><h3>{name}</h3><span className={`sy-tag sy-${tone(bias)}`}>{bias}</span></div>
    {type === 'frama' ? <p>Mid {number(state?.mid)} <span>·</span> Band {number(state?.lower)} – {number(state?.upper)}</p>
      : <p>Filter {number(state?.line)} <span>·</span> Range {number(state?.range)}</p>}
    <small>{state ? `Cross: ${state.cross == null ? 'none' : detail(state.cross)}${state.signal_age != null ? ` · ${state.signal_age} candle(s) ago` : ''}` : 'Waiting for closed-candle analysis'}</small>
  </div>;
}
function JobProgress({ job, label }: { job?: JobState; label: string }) {
  if (!job?.status) return null;
  const progress = job.progress;
  const value = typeof progress === 'number' && Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : undefined;
  const detail = typeof progress === 'string' && progress ? progress : job.message;
  const active = /running|pending|queued|training|downloading/i.test(job.status);
  return <div className="sy-job" aria-live="polite">
    <div><span>{active && <Loader2 size={12} className="sy-spin" />}{label}: {job.status.replace(/_/g, ' ')}</span>{value != null && <strong>{value.toFixed(0)}%</strong>}</div>
    {value != null && <progress max={100} value={value} aria-label={`${label} progress`} />}
    {detail && <p>{detail}</p>}
    {job.error && <p className="sy-error">{job.error}</p>}
  </div>;
}
let alarmContext: AudioContext | null = null;

function alarmAudio() {
  const Ctx = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) return null;
  alarmContext = alarmContext || new Ctx();
  return alarmContext;
}

function signalAlarm() {
  const ctx = alarmAudio();
  if (!ctx) return;
  const burst = () => {
    const master = ctx.createGain();
    master.gain.value = 1;
    master.connect(ctx.destination);
    [523, 784, 1047, 784, 1175, 1568, 1175, 1568].forEach((freq, index) => {
      const tone = ctx.createOscillator();
      const level = ctx.createGain();
      tone.type = 'square';
      tone.frequency.value = freq;
      const start = ctx.currentTime + index * 0.22;
      level.gain.setValueAtTime(0.001, start);
      level.gain.exponentialRampToValueAtTime(1, start + 0.02);
      level.gain.exponentialRampToValueAtTime(0.001, start + 0.2);
      tone.connect(level);
      level.connect(master);
      tone.start(start);
      tone.stop(start + 0.22);
    });
  };
  if (ctx.state === 'suspended') void ctx.resume().then(burst).catch(() => undefined);
  else burst();
}

export function SyntheticPage() {
  const [status, setStatus] = useState<SyntheticStatus | null>(null);
  const [online, setOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [paths, setPaths] = useState<SignalPath[]>([]);
  const rememberPaths = useCallback((next: SignalPath[]) => {
    setPaths(current => JSON.stringify(current) === JSON.stringify(next) ? current : next);
  }, []);
  const [stake, setStake] = useState('1');
  const [cap, setCap] = useState('20');
  const [demoAutoTrade, setDemoAutoTrade] = useState(false);
  const [token, setToken] = useState('');
  const [appId, setAppId] = useState('');
  const [lastReceived, setLastReceived] = useState<string>();
  const mounted = useRef(false);
  const initialized = useRef(false);
  const heardSignals = useRef<Set<string> | null>(null);
  const mutation = useRef(false);
  const revision = useRef(0);

  const applyStatus = useCallback((next: SyntheticStatus) => {
    setStatus(next);
    setOnline(true);
    setLastReceived(new Date().toISOString());
    if (!initialized.current) {
      setStake(String(next.config?.stake ?? 1));
      setCap(String(next.config?.max_signals_per_day ?? 20));
      setDemoAutoTrade(next.config?.demo_auto_trade === true);
      initialized.current = true;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let pending = false;
    let controller: AbortController | undefined;
    async function poll() {
      if (pending || mutation.current) return;
      pending = true;
      const currentRevision = revision.current;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 15000);
      try {
        const next = await syntheticApi.status(controller.signal);
        if (mounted.current && currentRevision === revision.current && !mutation.current) applyStatus(next);
      } catch {
        if (mounted.current && currentRevision === revision.current && !mutation.current) setOnline(false);
      } finally {
        pending = false;
        clearTimeout(timeout);
        if (mounted.current) setLoading(false);
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), 5000);
    const unlock = () => { const ctx = alarmAudio(); if (ctx?.state === 'suspended') void ctx.resume(); };
    window.addEventListener('pointerdown', unlock);
    return () => { mounted.current = false; clearInterval(timer); controller?.abort(); window.removeEventListener('pointerdown', unlock); };
  }, [applyStatus]);

  useEffect(() => {
    if (!status) return;
    const ids = (status.signals || []).map(signal => signal.id || `${signal.decision_epoch ?? ''}:${signal.direction ?? ''}`);
    if (heardSignals.current == null) {
      heardSignals.current = new Set(ids);
      return;
    }
    const fresh = ids.some(id => !heardSignals.current?.has(id));
    ids.forEach(id => heardSignals.current?.add(id));
    if (fresh) signalAlarm();
  }, [status]);

  async function act(action: Action, enabled = true) {
    if (mutation.current && action !== 'stop') return;
    const currentRevision = ++revision.current;
    mutation.current = true;
    setBusy(action);
    setError('');
    setMessage('');
    try {
      let next: SyntheticStatus | undefined;
      if (action === 'start') next = await syntheticApi.start({ stake: Number(stake), max_signals_per_day: Number(cap), demo_auto_trade: demoAutoTrade });
      if (action === 'stop') next = await syntheticApi.stop();
      if (action === 'scan') next = await syntheticApi.scan();
      if (action === 'account') {
        next = await syntheticApi.account({ enabled, ...(token.trim() ? { token: token.trim() } : {}), ...(appId.trim() ? { app_id: appId.trim() } : {}) });
        if (mounted.current) { setToken(''); if (!enabled) setDemoAutoTrade(false); }
        setMessage(enabled ? 'Demo account settings saved. Enable demo auto-trading and start when ready.' : 'Demo account disabled.');
      }
      if (action === 'download') { await syntheticApi.download(90); setMessage('History download requested.'); }
      if (!next) next = await syntheticApi.status();
      if (mounted.current && currentRevision === revision.current) applyStatus(next);
    } catch (err) {
      if (mounted.current && currentRevision === revision.current) setError(err instanceof Error ? err.message : 'The request failed.');
    } finally {
      if (currentRevision === revision.current) {
        mutation.current = false;
        if (mounted.current) setBusy(null);
      }
    }
  }

  const analysis = status?.analyses?.find(item => item.symbol === 'R_100');
  const freshDecision = decisionIsFresh(analysis);
  const finalDirection = online && analysis?.eligible && freshDecision ? analysis.direction : 'NO TRADE';
  const validInputs = Number.isFinite(Number(stake)) && Number(stake) >= 0.35 && Number(stake) <= 10 && Number.isInteger(Number(cap)) && Number(cap) >= 1 && Number(cap) <= 100;
  const jobActive = (job?: JobState) => /running|pending|queued|training|downloading/i.test(job?.status || '');
  const jobBusy = jobActive(status?.history?.job);
  const factors = analysis?.factors?.length ? analysis.factors : FEATURE_NAMES.map((name, index) => ({ id: index, name, status: 'WAIT', detail: 'Run a scan to evaluate completed candles.' }));
  const blockers = [...(!online ? ['Service unavailable; displayed readings may be old.'] : []),
    ...(analysis && !freshDecision ? ['This decision candle is outside the 20-second entry window. Waiting for a fresh completed-candle decision.'] : []),
    ...(analysis?.blockers || [])];
  const latestSignals = (status?.signals || []).slice(0, 10);

  return <div className="sy-page">
    <header className="sy-header">
      <div><div className="sy-eyebrow"><Activity size={14} /> DERIV / SYNTHETIC MARKETS</div><h1>Volatility 100 <span>Rise / Fall</span></h1><p>FRAMA Channel and Range Filter on the closed 1-minute candle, confirmed by 5m and 15m.</p></div>
      <div className="sy-header-status"><span className={`sy-service ${online ? 'connected' : ''}`}><i />{loading ? 'Connecting' : online ? 'Service connected' : 'Service unavailable'}</span><span className="sy-demo-label"><ShieldCheck size={13} /> Demo execution only</span></div>
    </header>

    {!loading && !online && <div className="sy-notice" role="alert">The analysis service is unavailable. Displayed results may be old; new signals cannot be confirmed. Retrying automatically.</div>}
    {error && <div className="sy-notice sy-error" role="alert">{error}</div>}
    {message && <div className="sy-notice" role="status">{message}</div>}

    <div className="sy-stats">
      <div><span>MARKET</span><strong>Volatility 100</strong><small>R_100 · Synthetic index</small></div>
      <div><span>ENTRY / EXPIRY</span><strong>1m <em>/</em> 1m</strong><small>Only completed entry candles</small></div>
      <div><span>TREND CONFIRMATION</span><strong>5m <em>+</em> 15m</strong><small>Higher-timeframe alignment</small></div>
      <div><span>BOT STATE</span><strong className={status?.running ? 'sy-positive' : ''}>{status?.running ? 'Running' : 'Stopped'}</strong><small>{status?.config?.demo_auto_trade ? 'Demo auto-trading enabled' : 'Signal monitoring only'}</small></div>
    </div>

    <div className="sy-workspace">
      <div>
        <SyntheticChart
          online={online}
          signals={status?.signals || []}
          liveSignal={finalDirection === 'RISE' || finalDirection === 'FALL' ? { direction: finalDirection, decision_epoch: analysis?.decision_epoch } : undefined}
          onPaths={rememberPaths}
        />
        <section className="sy-panel">
          <div className="sy-panel-title"><div><h2><Check size={17} /> Entry confirmation</h2><p>Both indicators must agree, then pass the 5-minute and 15-minute checks.</p></div><span className="sy-tag">M1 closed</span></div>
          <div className="sy-indicators"><IndicatorCard name="FRAMA Channel" state={analysis?.indicators?.frama} type="frama" /><div className="sy-plus">+</div><IndicatorCard name="Range Filter" state={analysis?.indicators?.range_filter} type="range" /></div>
          <div className="sy-timeframes">{(['1m', '5m', '15m'] as const).map(tf => {
            const frame = analysis?.timeframes?.[tf];
            return <div key={tf}><span>{tf} {tf === '1m' ? 'entry' : 'confirmation'}</span><strong className={`sy-${tone(frame?.trend)}`}>{frame?.trend || 'Waiting'}</strong><small>{frame?.structure?.replace(/_/g, ' / ') || 'No structure yet'} · {frame?.bars ?? 0} candles</small></div>;
          })}</div>
          <details className="sy-evidence" open><summary>Market evidence <span>{factors.length} checks <ChevronDown size={15} /></span></summary>
            <div>{factors.map((factor, index) => <div className="sy-factor" key={`${factor.id}-${index}`}><span className="sy-factor-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{factor.name}</strong><p>{detail(factor.detail)}</p></div><span className={`sy-tag sy-${tone(factor.status)}`}>{factor.status}</span></div>)}</div>
          </details>
        </section>
      </div>

      <aside>
        <section className="sy-panel sy-decision">
          <div className="sy-panel-title"><h2>Final signal</h2><span className="sy-tag">{finalDirection !== 'NO TRADE' ? 'Confirmed' : 'Waiting'}</span></div>
          <Direction value={finalDirection} large />
          <p className="sy-decision-time"><Clock size={12} /> Decision close: {dateTime(analysis?.decision_epoch)}</p>
          <p className="sy-footnote">A demo entry needs FRAMA, the Range Filter, and the 5-minute and 15-minute checks to agree on the closed candle.</p>
          <div className="sy-blockers"><h3>{finalDirection === 'NO TRADE' ? 'What is blocking a demo entry' : 'Demo confirmation status'}</h3>
            {blockers.length ? <ul>{blockers.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>{analysis?.eligible ? 'The signal checks passed for this candle.' : 'Run a scan to evaluate FRAMA, the Range Filter, 5m and 15m.'}</p>}
          </div>
        </section>

        <section className="sy-panel">
          <div className="sy-panel-title"><h2><Play size={16} /> Bot controls</h2></div>
          <div className="sy-fields"><label>Stake per demo trade (0.35–10)<input type="number" min="0.35" max="10" step="0.01" value={stake} disabled={status?.running || !!busy} onChange={event => setStake(event.target.value)} /></label><label>Daily signal limit<input type="number" min="1" max="100" step="1" value={cap} disabled={status?.running || !!busy} onChange={event => setCap(event.target.value)} /></label></div>
          <label className="sy-checkbox"><input type="checkbox" checked={demoAutoTrade} disabled={status?.running || !!busy} onChange={event => setDemoAutoTrade(event.target.checked)} /><span><strong>Enable demo auto-trading</strong><small>Buy a 1-minute Rise/Fall contract when all gates pass.</small></span></label>
          <div className="sy-actions">
            <button type="button" className="sy-button sy-primary" disabled={!online || !!busy || !!status?.running || !validInputs} onClick={() => void act('start')}>{busy === 'start' ? <Loader2 className="sy-spin" size={14} /> : <Play size={14} />}{demoAutoTrade ? 'Start demo bot' : 'Start monitoring'}</button>
            <button type="button" className="sy-button" disabled={busy === 'stop' || (!status?.running && busy !== 'scan' && busy !== 'start')} onClick={() => void act('stop')}><Pause size={14} /> Stop</button>
            <button type="button" className="sy-button" disabled={!online || !!busy} onClick={() => void act('scan')}><RefreshCw size={14} className={busy === 'scan' ? 'sy-spin' : ''} /> Scan preview</button>
          </div>
          <p className="sy-footnote">Scan preview analyses the market without placing a trade. Expiry is fixed at 1 minute.</p>
          <div className="sy-execution"><ShieldCheck size={16} /><p>{status?.execution?.reason || 'Waiting for demo execution readiness.'}</p></div>
          <details className="sy-account"><summary>Demo account connection <ChevronDown size={15} /></summary>
            <p>Use a Deriv demo account token with trading permission.</p>
            <div className="sy-fields sy-one-column"><label>Demo API token<input type="password" autoComplete="off" value={token} disabled={!!busy} onChange={event => setToken(event.target.value)} placeholder="Enter token or keep existing" /></label><label>Deriv app ID<input type="text" autoComplete="off" value={appId} disabled={!!busy} onChange={event => setAppId(event.target.value)} placeholder="Keep configured app ID" /></label></div>
            <div className="sy-actions"><button className="sy-button" disabled={!online || !!busy} onClick={() => void act('account', true)}>Save demo connection</button><button className="sy-button" disabled={!online || !!busy} onClick={() => void act('account', false)}>Disable connection</button></div>
          </details>
        </section>
      </aside>
    </div>

    <section className="sy-panel">
      <div className="sy-panel-title"><div><h2><Download size={17} /> Stored candles</h2><p>Download up to 90 days of Volatility 100 history for the closed-candle checks.</p></div></div>
      <div className="sy-history-stats">{['1m', '5m', '15m'].map(tf => {
        const history = status?.history?.timeframes?.[tf];
        return <div key={tf}><span>{tf}</span><strong>{number(history?.bars, 0)}</strong><small>{number(history?.days, 1)} days · {number(history?.gaps, 0)} gaps</small></div>;
      })}</div>
      <div className="sy-actions"><button type="button" className="sy-button" disabled={!online || !!busy || jobBusy} onClick={() => void act('download')}><Download size={14} /> Download history</button></div>
      <JobProgress job={status?.history?.job} label="History" />
    </section>

    <section className="sy-panel">
      <div className="sy-panel-title"><div><h2><Clock size={17} /> Recent signals</h2><p>Each arrow is watched for 5 minutes: whether price went that way, and the farthest move in that direction.</p></div><span className="sy-tag">5-minute path</span></div>
      {latestSignals.length ? <div className="sy-table-wrap"><table><thead><tr><th>Decision time · Colombo</th><th>Market</th><th>Signal</th><th>5-minute path</th><th>Expiry</th><th>Execution</th></tr></thead><tbody>{latestSignals.map((signal, index) => {
        const key = `${signal.decision_epoch ?? signal.created_epoch ?? ''}:${signal.direction ?? ''}`;
        const path = paths.find(item => item.key === key);
        return <tr key={signal.id || index}><td>{dateTime(signal.decision_epoch || signal.created_epoch || signal.created_at)}</td><td>Volatility 100</td><td><Direction value={signal.direction} /></td><td>{path ? pathLabel(path) : 'Open the 1-minute chart to measure this arrow'}</td><td>{dateTime(signal.expiry_epoch)}</td><td>{signal.execution_status?.replace(/_/g, ' ') || 'Recorded'}</td></tr>;
      })}</tbody></table></div> : <div className="sy-empty"><Activity size={22} /><strong>No qualified signals yet</strong><p>A Rise or Fall arrow appears when FRAMA, Range Filter, and the 5-minute and 15-minute checks pass together. The chart then shows how far price goes that way for 5 minutes.</p></div>}
    </section>

    <footer className="sy-footer"><p>Indicator adaptations inspired by <a href="https://www.tradingview.com/script/hskyfPWP-FRAMA-Channel-BigBeluga/" target="_blank" rel="noreferrer">FRAMA Channel [BigBeluga]</a> and <a href="https://www.tradingview.com/script/J8GzFGfD-Range-Filter-Buy-and-Sell-5min-guikroth-version/" target="_blank" rel="noreferrer">Range Filter Buy and Sell 5min</a>. Exact Pine Script parity has not been verified.</p><span>Updated {dateTime(lastReceived)} · Asia/Colombo</span></footer>
  </div>;
}
