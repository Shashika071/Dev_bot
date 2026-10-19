import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle, ChevronDown, Clock, Globe2, Loader2, Pause, Play, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { forexApi, type ForexAnalysis, type ForexFactor, type ForexStatus, type ForexSymbol } from '../api/client';
import './ForexPage.css';

const FACTORS = [
  ['Market analysis', ['Market trend', 'Market structure', 'Support and resistance', 'Supply and demand', 'Liquidity zones', 'Breakouts', 'False breakouts', 'Momentum', 'Volatility', 'Candlestick patterns', 'Chart patterns', 'Multi-timeframe analysis']],
  ['Technical indicators', ['EMA / SMA', 'RSI', 'MACD', 'ATR', 'Bollinger Bands', 'ADX', 'Fibonacci retracement', 'Volume / tick volume']],
  ['Fundamental analysis', ['Interest rates', 'Inflation / CPI', 'Employment / NFP', 'GDP', 'Central bank speeches', 'Economic calendar', 'Geopolitical events', 'Market sentiment', 'Currency strength']],
  ['Sessions and execution', ['Asian session', 'London session', 'New York session', 'Session overlap', 'Day of the week', 'Market liquidity', 'Spread', 'Slippage']],
] as const;
const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];
const TZ = 'Asia/Colombo';

function describe(value: unknown): string {
  if (value == null) return 'Not supplied';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(describe).join(' · ');
  if (typeof value === 'object') return Object.entries(value).map(([key, entry]) => `${key.replace(/_/g, ' ')}: ${describe(entry)}`).join(' · ');
  return String(value);
}

function dateTime(value?: string | number | null): string {
  if (!value) return 'Not yet';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : date.toLocaleString('en-GB', { timeZone: TZ, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function tone(value: string): string {
  if (/^(rise|bullish|up|pass|ready|available|ok|aligned|supported|positive|confirmed)$/i.test(value)) return 'positive';
  if (/^(fall|bearish|down|fail|blocked|error|negative|rejected)$/i.test(value)) return 'negative';
  if (/missing|stale|unavailable|unknown|not.evaluated/i.test(value)) return 'muted';
  return 'neutral';
}

function Direction({ direction }: { direction: string }) {
  return <span className={`fx-direction fx-${tone(direction)}`}>
    {direction === 'RISE' ? <ArrowUpRight size={17} /> : direction === 'FALL' ? <ArrowDownRight size={17} /> : <Pause size={14} />}
    {direction || 'WAIT'}
  </span>;
}

function timeframeValue(analysis: ForexAnalysis | undefined, timeframe: string): unknown {
  const data = analysis?.timeframes;
  if (!data) return undefined;
  const aliases: Record<string, string[]> = { '1m': ['1m', 'M1', '60'], '5m': ['5m', 'M5', '300'], '15m': ['15m', 'M15', '900'], '1h': ['1h', 'H1', '3600'], '4h': ['4h', 'H4', '14400'], '1d': ['1d', 'D1', 'daily', '86400'] };
  if (Array.isArray(data)) return data.find(entry => aliases[timeframe].includes(String(entry.timeframe ?? entry.interval ?? entry.name)));
  return aliases[timeframe].map(key => data[key]).find(value => value != null);
}

function TimeframeCard({ analysis, timeframe }: { analysis?: ForexAnalysis; timeframe: string }) {
  const value = timeframeValue(analysis, timeframe);
  const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
  const bias = record ? describe(record.bias ?? record.trend ?? record.direction ?? (record.ready === false ? 'unavailable' : 'evaluated')) : value == null ? 'Pending' : describe(value);
  return <div className="fx-timeframe" title={value == null ? 'Awaiting completed candle analysis' : describe(value)}>
    <span>{timeframe === '1d' ? 'Daily' : timeframe}</span>
    <strong className={`fx-${tone(bias)}`}>{bias}</strong>
    <small>{record?.reason ? describe(record.reason) : record?.closed_bars != null ? `${describe(record.closed_bars)} closed candles${record.stale ? ' · stale' : ''}` : record?.count != null ? `${describe(record.count)} candles` : 'Closed candles'}</small>
  </div>;
}

function Checklist({ analysis }: { analysis?: ForexAnalysis }) {
  let offset = 0;
  return <div className="fx-checklist">
    {FACTORS.map(([group, names], groupIndex) => {
      const start = offset;
      offset += names.length;
      const rows = names.map((name, index) => {
        const id = start + index + 1;
        const factor: ForexFactor | undefined = analysis?.factors?.find(item => Number(item.id) === id);
        return { id, name: factor?.name || name, status: factor?.status || 'not evaluated', detail: factor ? describe(factor.detail) : 'Run a scan to inspect the available evidence.' };
      });
      return <details className="fx-check-group" key={group} open={groupIndex === 0 ? true : undefined}>
        <summary><span>{group}</span><span className="fx-check-count">{rows.length} checks <ChevronDown size={15} /></span></summary>
        <div className="fx-check-rows">{rows.map(row => <div className="fx-check-row" key={row.id}>
          <span className="fx-check-number">{String(row.id).padStart(2, '0')}</span>
          <div><strong>{row.name}</strong><p>{row.detail}</p></div>
          <span className={`fx-tag fx-${tone(row.status)}`}>{row.status.replace(/_/g, ' ')}</span>
        </div>)}</div>
      </details>;
    })}
  </div>;
}

export function ForexPage() {
  const [status, setStatus] = useState<ForexStatus | null>(null);
  const [symbols, setSymbols] = useState<ForexSymbol[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [stake, setStake] = useState('1');
  const [selectedAnalysis, setSelectedAnalysis] = useState('');
  const [error, setError] = useState('');
  const [symbolError, setSymbolError] = useState('');
  const [online, setOnline] = useState(false);
  const [loading, setLoading] = useState(true);
  const [symbolLoading, setSymbolLoading] = useState(true);
  const [busy, setBusy] = useState<'scan' | 'start' | 'stop' | 'account' | null>(null);
  const [token, setToken] = useState('');
  const [appId, setAppId] = useState('');
  const [accountMessage, setAccountMessage] = useState('');
  const [lastReceived, setLastReceived] = useState<string>();
  const initialized = useRef(false);
  const mutation = useRef(false);
  const revision = useRef(0);
  const alive = useRef(true);

  const applyStatus = useCallback((next: ForexStatus) => {
    setStatus(next);
    setOnline(true);
    setLastReceived(new Date().toISOString());
    if (!initialized.current) {
      setSelectedSymbols(next.config?.symbols || []);
      setStake(String(next.config?.stake ?? 1));
      initialized.current = true;
    }
  }, []);

  const loadSymbols = useCallback(async () => {
    setSymbolLoading(true);
    try {
      const result = await forexApi.symbols();
      if (!alive.current) return;
      setSymbols(result.symbols || []);
      setSymbolError(result.error || '');
    } catch (err) {
      if (alive.current) setSymbolError(err instanceof Error ? err.message : 'Unable to discover forex markets.');
    } finally { if (alive.current) setSymbolLoading(false); }
  }, []);

  useEffect(() => {
    alive.current = true;
    let controller: AbortController | undefined;
    let pending = false;
    const poll = async () => {
      if (mutation.current || pending) return;
      pending = true;
      const requestRevision = revision.current;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 15000);
      try {
        const next = await forexApi.status(controller.signal);
        if (alive.current && !mutation.current && revision.current === requestRevision) applyStatus(next);
      } catch {
        if (alive.current && !mutation.current && revision.current === requestRevision) setOnline(false);
      } finally {
        clearTimeout(timeout);
        pending = false;
        if (alive.current) setLoading(false);
      }
    };
    void poll();
    void loadSymbols();
    const timer = setInterval(() => void poll(), 5000);
    return () => { alive.current = false; clearInterval(timer); controller?.abort(); };
  }, [applyStatus, loadSymbols]);

  const act = async (action: 'scan' | 'start' | 'stop') => {
    if (mutation.current && !(action === 'stop' && busy === 'scan')) return;
    const requestRevision = ++revision.current;
    mutation.current = true;
    setBusy(action);
    setError('');
    try {
      const next = action === 'start'
        ? await forexApi.start({ symbols: selectedSymbols, stake: Number(stake), duration_seconds: 14400, demo_auto_trade: true })
        : action === 'stop' ? await forexApi.stop() : await forexApi.scan();
      if (alive.current && revision.current === requestRevision) applyStatus(next);
    } catch (err) {
      if (alive.current && revision.current === requestRevision) setError(err instanceof Error ? err.message : 'The request failed.');
    } finally {
      if (revision.current === requestRevision) {
        mutation.current = false;
        if (alive.current) setBusy(null);
      }
    }
  };

  const saveAccount = async (enabled: boolean) => {
    if (mutation.current) return;
    const requestRevision = ++revision.current;
    mutation.current = true;
    setBusy('account');
    setError('');
    setAccountMessage('');
    try {
      const next = await forexApi.account({ enabled, ...(enabled && token.trim() ? { token: token.trim() } : {}), ...(enabled && appId.trim() ? { app_id: appId.trim() } : {}) });
      if (alive.current && revision.current === requestRevision) {
        applyStatus(next);
        setToken('');
        setAccountMessage(enabled ? 'Demo connection settings saved. Execution checks still apply.' : 'Demo execution disabled.');
      }
    } catch (err) {
      if (alive.current && revision.current === requestRevision) setError(err instanceof Error ? err.message : 'Could not save the demo connection.');
    } finally {
      if (revision.current === requestRevision) {
        mutation.current = false;
        if (alive.current) setBusy(null);
      }
    }
  };

  const analyses = status?.analyses || [];
  const active = analyses.find(item => item.symbol === selectedAnalysis) || analyses[0];
  const invalidStake = !Number.isFinite(Number(stake)) || Number(stake) < 0.35 || Number(stake) > 10;
  const choices = [...symbols, ...selectedSymbols.filter(symbol => !symbols.some(item => item.symbol === symbol)).map(symbol => ({ symbol, display_name: symbol, is_open: false }))];
  const cap = status?.config?.max_signals_per_day ?? status?.product?.daily_cap ?? 2;
  const used = status?.daily_count ?? 0;
  const remaining = status?.signals_remaining_today ?? Math.max(0, cap - used);
  const durationHours = status?.duration_hours ?? (status?.config?.duration_seconds ?? 14400) / 3600;
  const signals = status?.signals || [];
  const newsReady = Boolean(status?.news?.ready);
  const newsBlocked = online && status != null && !newsReady;

  return <div className="fx-page">
    <header className="fx-header">
      <div><div className="fx-eyebrow"><Globe2 size={14} /> FOREX · RISE / FALL</div><h1>Patient setups. Clear decisions.</h1><p>Four-hour CALL/PUT on Deriv majors · maximum two signals per Colombo day · not Touch.</p></div>
      <span className="fx-demo-pill"><ShieldCheck size={15} /> Demo account only</span>
    </header>

    {!loading && !online && <div className="fx-notice fx-error" role="alert"><AlertTriangle size={18} /><div><strong>Forex service unavailable</strong><p>{status ? `Showing the last response from ${dateTime(lastReceived)}. The scanner state may have changed.` : 'Start the backend with the Forex routes enabled. This page reconnects automatically.'}</p></div></div>}
    {error && <div className="fx-notice fx-error" role="alert"><AlertTriangle size={18} /><div><strong>Request could not complete</strong><p>{error}</p></div><button type="button" className="fx-icon-button" aria-label="Dismiss request error" onClick={() => setError('')}><X size={16} /></button></div>}

    <section className="fx-stats" aria-label="Scanner summary">
      <div className="fx-stat"><span>SCANNER</span><strong className={online && status?.running ? 'fx-positive' : ''}>{loading ? 'Connecting' : !online ? 'Unknown' : status?.running ? 'Running' : 'Stopped'}</strong><small>{status?.running ? 'Evaluates markets every 5 minutes' : 'Start to enable automatic demo entries'}</small></div>
      <div className="fx-stat"><span>TODAY’S SLOTS</span><strong>{used} <em>/ {cap}</em></strong><small>{remaining === 0 ? 'Daily cap reached · next Colombo day resets' : `${remaining} remaining today · never a forced target`}</small></div>
      <div className="fx-stat"><span>CONTRACT DURATION</span><strong>{durationHours}<em> hours</em></strong><small>Fixed 4h Rise/Fall · CALL = rise · PUT = fall</small></div>
      <div className="fx-stat"><span>LAST ANALYSIS</span><strong className="fx-date-value">{dateTime(status?.last_scan_at)}</strong><small>All times in Asia/Colombo · UTC+05:30</small></div>
    </section>

    {newsBlocked && <div className="fx-notice fx-warn" role="status"><AlertTriangle size={18} /><div><strong>News / calendar blocks new entries</strong><p>{status?.news?.reason || 'A fresh economic calendar is required before Rise/Fall signals can emit.'}</p><p>{status?.news?.setup_hint || 'Configure FOREX_CONTEXT_FILE, FOREX_CONTEXT_URL, or TRADING_ECONOMICS_API_KEY. See docs/forex-rise-fall.md.'}</p></div></div>}

    <div className="fx-control-grid">
      <section className="fx-panel">
        <div className="fx-panel-heading"><h2><Activity size={18} /> Scanner controls</h2><span className="fx-tag">Fixed daily cap: 2</span></div>
        <div className="fx-fields">
          <label>Contract duration<input type="text" value="4 hours" readOnly /><small>This strategy evaluates four-hour contracts.</small></label>
          <label>Stake · demo account currency<input type="number" min="0.35" max="10" step="0.01" value={stake} onChange={event => setStake(event.target.value)} disabled={!!busy || !!status?.running} aria-invalid={invalidStake} /><small>Fixed amount, 0.35–10 per contract</small></label>
        </div>
        <fieldset className="fx-pairs" disabled={!!busy || !!status?.running}><legend>Markets to watch <button type="button" className="fx-link-button" onClick={() => void loadSymbols()} disabled={symbolLoading}><RefreshCw size={12} className={symbolLoading ? 'spin' : ''} /> Refresh markets</button></legend>
          {symbolLoading && choices.length === 0 ? <p className="fx-muted">Discovering Deriv forex markets…</p> : choices.length === 0 ? <p className="fx-muted">No forex markets available yet.</p> : <div className="fx-pair-grid">{choices.map(item => <label key={item.symbol} className={`fx-pair ${selectedSymbols.includes(item.symbol) ? 'selected' : ''}`}><input type="checkbox" checked={selectedSymbols.includes(item.symbol)} onChange={event => setSelectedSymbols(previous => event.target.checked ? [...previous, item.symbol] : previous.filter(symbol => symbol !== item.symbol))} /><span>{item.display_name || item.symbol}<small>{symbols.some(found => found.symbol === item.symbol) ? item.is_open ? 'Open' : 'Closed' : 'Availability unverified'}</small></span></label>)}</div>}
        </fieldset>
        {symbolError && <p className="fx-inline-error">Market discovery: {symbolError}</p>}
        <div className="fx-actions">
          {status?.running ? <button type="button" className="fx-button fx-stop" onClick={() => void act('stop')} disabled={(busy != null && busy !== 'scan') || !online}>{busy === 'stop' ? <Loader2 size={16} className="spin" /> : <Pause size={16} />} Stop new entries</button> : <button type="button" className="fx-button fx-primary" onClick={() => void act('start')} disabled={!!busy || !online || selectedSymbols.length === 0 || invalidStake}>{busy === 'start' ? <Loader2 size={16} className="spin" /> : <Play size={16} />} Start demo auto-trading</button>}
          <button type="button" className="fx-button" onClick={() => void act('scan')} disabled={!!busy || !online}>{busy === 'scan' ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}{busy === 'scan' ? 'Analyzing markets…' : 'Scan now'}</button>
        </div>
        <p className="fx-footnote">{status?.running ? 'Scans may place eligible demo trades. Stop prevents new entries; existing contracts keep their original expiry.' : 'Scan now previews analysis while stopped. Automatic demo entries require Start and all execution checks to pass.'} Settings apply when starting.</p>
      </section>

      <section className="fx-panel fx-readiness">
        <div className="fx-panel-heading"><h2><ShieldCheck size={18} /> Entry checks</h2></div>
        <div className="fx-readiness-item">{status?.execution?.allowed ? <CheckCircle size={18} className="fx-positive" /> : <AlertTriangle size={18} className="fx-neutral" />}<div><strong>{status?.execution?.allowed ? 'Demo execution available' : 'Demo execution blocked'}</strong><p>{status?.execution?.reason || 'Waiting for account and execution checks.'}</p></div></div>
        <div className="fx-readiness-item">{newsReady ? <CheckCircle size={18} className="fx-positive" /> : <AlertTriangle size={18} className="fx-neutral" />}<div><strong>{newsReady ? 'Fundamental context ready' : 'News / calendar needs attention'}</strong><p>{status?.news?.reason || 'A fresh economic calendar and fundamental context are required.'}</p>{status?.news?.source && <small>Source: {status.news.source}</small>}</div></div>
        <div className="fx-readiness-item"><Clock size={18} /><div><strong>{remaining} of {cap} slots left today</strong><p>Hard cap {cap}/day · {durationHours}h cooldown between signals · Colombo day. Missing evidence or conflicting setups can mean no trade.</p></div></div>
        <p className="fx-footnote">Rule-based research; no calibrated win probability. Confluence scores describe agreement between checks, not evidence of profitability. Missing fundamentals remain unavailable. Spread and exchange volume stay unavailable on public Deriv FX.</p>
        {status?.last_error && <div className="fx-inline-error" role="status"><strong>Latest scanner issue:</strong> {status.last_error}</div>}
        <details className="fx-connection">
          <summary>Demo account connection <span className="fx-tag">{status?.execution?.token_configured ? 'Token configured' : 'Setup needed'}</span></summary>
          <p>Use a Deriv API token with trade permission. The server verifies a virtual account before any purchase.</p>
          <div className="fx-fields">
            <label>API token<input type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="off" placeholder={status?.execution?.token_configured ? 'Leave blank to keep saved token' : 'Demo account token'} disabled={!!busy} /></label>
            <label>Deriv App ID<input type="text" value={appId} onChange={event => setAppId(event.target.value)} autoComplete="off" placeholder="Optional · keep server setting" disabled={!!busy} /></label>
          </div>
          <div className="fx-actions"><button type="button" className="fx-button" onClick={() => void saveAccount(true)} disabled={!!busy || !online || (!token.trim() && !status?.execution?.token_configured)}>{busy === 'account' ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />} Save demo connection</button>{status?.execution?.server_allowed && <button type="button" className="fx-link-button" onClick={() => void saveAccount(false)} disabled={!!busy || !online}>Disable execution</button>}</div>
          {accountMessage && <p className="fx-positive" role="status">{accountMessage}</p>}
        </details>
        <details className="fx-connection" open={newsBlocked || undefined}>
          <summary>Connect fundamental data <span className={`fx-tag ${newsReady ? 'fx-positive' : ''}`}>{newsReady ? 'Ready' : 'Required'}</span></summary>
          <p>Entries stay blocked until a fresh calendar covers both currencies of the watched pairs for the full 4-hour hold.</p>
          <ol className="fx-setup-steps">
            <li>Copy <code>data/forex/context.sample.json</code> (uses <code>clock: server</code> so timestamps stay fresh) or point to your live feed.</li>
            <li>Set <code>FOREX_CONTEXT_FILE</code>, <code>FOREX_CONTEXT_URL</code>, or <code>TRADING_ECONOMICS_API_KEY</code> in <code>.env</code>.</li>
            <li>Restart the backend, then click <strong>Scan now</strong>. High-impact events in the hold window still block entry.</li>
          </ol>
          <p>Schema and CALL/PUT mapping: <code>docs/forex-rise-fall.md</code>.</p>
          {status?.news?.setup_hint && <p className="fx-muted">{status.news.setup_hint}</p>}
        </details>
      </section>
    </div>

    <section className="fx-panel fx-analysis">
      <div className="fx-panel-heading"><div><h2>Market workspace</h2><p>Inspect the evidence behind each decision.</p></div>{analyses.length > 0 && <label className="fx-analysis-select">Market<select value={active?.symbol || ''} onChange={event => setSelectedAnalysis(event.target.value)}>{analyses.map(item => <option key={item.symbol} value={item.symbol}>{symbols.find(symbol => symbol.symbol === item.symbol)?.display_name || item.symbol}</option>)}</select></label>}</div>
      {active ? <div className="fx-analysis-summary"><div><span className="fx-eyebrow">{active.symbol}</span><div className="fx-analysis-decision"><Direction direction={active.direction} /><span className="fx-score">{Number.isFinite(active.score) ? active.score.toFixed(0) : '—'}<small> / 100 confluence</small></span></div></div><div className={`fx-analysis-state ${active.ready ? 'fx-positive' : 'fx-neutral'}`}>{active.ready ? 'Analysis gates passed' : 'Waiting for a qualified setup'}<small>Execution checks still apply</small></div></div> : <div className="fx-empty"><Activity size={25} /><strong>No analysis yet</strong><p>Scan the configured markets to see trends, setups and any missing data.</p></div>}
      {active?.blockers && active.blockers.length > 0 && <div className="fx-blockers"><strong><AlertTriangle size={15} /> Holding entries</strong><ul>{active.blockers.map((reason, index) => <li key={index}>{reason}</li>)}</ul></div>}
      {active?.reasons && active.reasons.length > 0 && <div className="fx-reasons"><strong>Setup evidence</strong><ul>{active.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></div>}
      <div className="fx-timeframes">{TIMEFRAMES.map(timeframe => <TimeframeCard key={timeframe} analysis={active} timeframe={timeframe} />)}</div>
      {active?.quote && <details className="fx-quote"><summary>Current contract quote</summary><p>{describe(active.quote)}</p></details>}
      <div className="fx-checklist-heading"><h3>37-factor analysis</h3><span>Unavailable data stays visible</span></div>
      <Checklist analysis={active} />
    </section>

    <section className="fx-panel">
      <div className="fx-panel-heading"><div><h2>Recent signals &amp; demo orders</h2><p>Signal generation and order execution are shown separately.</p></div><span className="fx-tag">Asia/Colombo</span></div>
      {signals.length === 0 ? <div className="fx-empty fx-empty-small"><Clock size={23} /><strong>No signals recorded</strong><p>A signal appears only when the required checks pass.</p></div> : <div className="fx-table-wrap"><table className="fx-table"><thead><tr><th>Market / direction</th><th>Signal time</th><th>Expiry</th><th>Confluence</th><th>Demo execution</th></tr></thead><tbody>{signals.map(signal => <tr key={signal.id}><td><strong>{signal.symbol}</strong><Direction direction={signal.direction} /></td><td>{dateTime(signal.created_at)}</td><td>{dateTime(signal.expiry_epoch)}<small>{signal.duration_seconds / 3600} hour contract</small></td><td>{Number.isFinite(signal.score) ? signal.score.toFixed(0) : '—'} / 100</td><td><span className="fx-tag">{(signal.execution_status || 'pending').replace(/_/g, ' ')}</span>{signal.execution_result && <details><summary>Order details</summary><p>{describe(signal.execution_result)}</p></details>}</td></tr>)}</tbody></table></div>}
    </section>
  </div>;
}
