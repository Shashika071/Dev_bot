import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Activity, ShieldCheck, Zap, AlertTriangle, Settings,
  RefreshCw, BarChart2, Brain, CheckCircle, XCircle,
  Loader2, Wifi, WifiOff, Clock, Database, TrendingUp,
  ChevronRight, Info, Signal, Cpu, Volume2
} from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import { playSignalAlertSound, unlockSignalAudio } from './signalAlert';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// ── Types ─────────────────────────────────────────────────────────────────────
interface ApiStatus { connected: boolean; deriv_connected: boolean; ticks_collected: number; quotes_collected: number; symbol?: string; }
interface SigData {
  signal_id: string; symbol: string; direction: string; probability: number;
  calibrated_probability?: number; created_at: string; status: string;
  is_validated?: boolean; explanation?: string; barrier_input?: string;
}
interface ChartPoint { epoch: number; quote: number; time?: string|null; }
interface ChartTicks { symbol: string; count: number; last_quote: number|null; points: ChartPoint[]; }
interface Candle { epoch: number; open: number; high: number; low: number; close: number; ticks?: number; }
interface ChartCandles { symbol: string; interval: string; count: number; last_quote: number|null; candles: Candle[]; }
type ChartTf = 'tick' | '1m' | '5m' | '15m';

function CandleChart({ data, interval }: { data: Candle[]; interval: string }) {
  const [hover, setHover] = useState<number|null>(null);
  if (data.length < 2) return null;

  const pad = { top: 12, right: 12, bottom: 28, left: 56 };
  const W = 900;
  const H = 280;
  const lows = data.map(d => d.low);
  const highs = data.map(d => d.high);
  const minP = Math.min(...lows);
  const maxP = Math.max(...highs);
  const span = Math.max(maxP - minP, 1e-6);
  const padP = span * 0.08;
  const yMin = minP - padP;
  const yMax = maxP + padP;
  const innerW = W - pad.left - pad.right;
  const innerH = H - pad.top - pad.bottom;
  const slot = innerW / data.length;
  const bodyW = Math.max(Math.min(slot * 0.55, 14), 2);
  const yScale = (p: number) => pad.top + ((yMax - p) / (yMax - yMin)) * innerH;
  const yTicks = 4;
  const labels = Array.from({ length: yTicks + 1 }, (_, i) => yMin + ((yMax - yMin) * i) / yTicks);
  const h = hover != null ? data[hover] : data[data.length - 1];
  const up = h.close >= h.open;

  return (
    <div className="candle-wrap">
      <div className="candle-legend font-mono text-xs" style={{ color: up ? 'var(--green)' : 'var(--red)' }}>
        {interval} · O {h.open.toFixed(4)} H {h.high.toFixed(4)} L {h.low.toFixed(4)} C {h.close.toFixed(4)}
        {h.ticks != null ? ` · ${h.ticks} ticks` : ''}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="candle-svg" preserveAspectRatio="none">
        {labels.map((v, i) => {
          const y = yScale(v);
          return (
            <g key={i}>
              <line x1={pad.left} y1={y} x2={W - pad.right} y2={y} stroke="rgba(0,212,255,0.06)" />
              <text x={pad.left - 8} y={y + 3} textAnchor="end" fill="#3d5070" fontSize="10">{v.toFixed(2)}</text>
            </g>
          );
        })}
        {data.map((c, i) => {
          const cx = pad.left + slot * i + slot / 2;
          const color = c.close >= c.open ? '#00e5a0' : '#ff4d6d';
          const yH = yScale(c.high);
          const yL = yScale(c.low);
          const yO = yScale(c.open);
          const yC = yScale(c.close);
          const top = Math.min(yO, yC);
          const bh = Math.max(Math.abs(yC - yO), 1);
          return (
            <g
              key={c.epoch}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              style={{ cursor: 'crosshair' }}
            >
              <rect x={cx - slot / 2} y={pad.top} width={slot} height={innerH} fill="transparent" />
              <line x1={cx} y1={yH} x2={cx} y2={yL} stroke={color} strokeWidth={1.25} />
              <rect x={cx - bodyW / 2} y={top} width={bodyW} height={bh} fill={color} opacity={hover === i ? 1 : 0.88} />
            </g>
          );
        })}
        {/* sparse x labels */}
        {data.map((c, idx) => {
          if (idx % Math.max(1, Math.floor(data.length / 6)) !== 0) return null;
          const cx = pad.left + slot * idx + slot / 2;
          const label = new Date(c.epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          return (
            <text key={`x-${c.epoch}`} x={cx} y={H - 8} textAnchor="middle" fill="#3d5070" fontSize="10">{label}</text>
          );
        })}
      </svg>
    </div>
  );
}
interface TrainStatus { status: 'idle'|'running'|'done'|'error'; progress: string; result?: TrainResult|null; error?: string|null; }
interface ModelMetrics { auc_roc?: number; brier_score?: number; accuracy?: number; selected_signal_count?: number; selected_win_rate_ci_lower?: number; }
interface TrainResult {
  timestamp?: string;
  split_info?: { train:number; val:number; cal:number; test:number; purged:number; unresolved_labels?: number };
  selected_pipeline?: string;
  barrier_unit?: string;
  contract_semantics?: string;
  has_demonstrated_edge?: boolean;
  edge_description?: string;
  quote_backtest_note?: string;
  version_tag?: string;
  validation_selection?: { scores?: Record<string, number>; selected_pipeline?: string };
  baseline?: ModelMetrics;
  xgboost?: ModelMetrics;
  catboost?: ModelMetrics;
  lstm?: ModelMetrics;
  weighted?: ModelMetrics;
  stacking?: ModelMetrics;
  selected?: ModelMetrics;
  historical_frequency?: { touch_rate?: number };
  top_features?: Array<{feature:string; importance:number}>;
}
interface DataInfo {
  has_data: boolean;
  total_ticks?: number;
  ready?: boolean;
  message?: string;
  note?: string;
  min_labels_required?: number;
  min_span_hours?: number;
  train_sampling_interval_seconds?: number;
  symbols?: Array<{
    symbol: string;
    tick_count: number;
    oldest?: string;
    newest?: string;
    ready_to_train: boolean;
    est_labels?: number;
    span_hours?: number;
    min_span_hours?: number;
  }>;
}
interface DailyStatus { signals_today: number; max_signals: number; cooldown_active: boolean; timezone: string; }
type Tab = 'dashboard' | 'train' | 'setup';

async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, { headers: {'Content-Type':'application/json'}, ...opts });
  if (!r.ok) {
    const b = await r.json().catch(()=>({}));
    const detail = typeof b.detail === 'string' ? b.detail
      : Array.isArray(b.detail) ? b.detail.map((x:any)=>x.msg||JSON.stringify(x)).join('; ')
      : b.detail ? JSON.stringify(b.detail) : `HTTP ${r.status}`;
    throw new Error(detail);
  }
  return r.json();
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [tab, setTab]           = useState<Tab>('dashboard');
  const [status, setStatus]     = useState<ApiStatus|null>(null);
  const [daily, setDaily]       = useState<DailyStatus|null>(null);
  const [online, setOnline]     = useState(false);
  const [signalFlash, setSignalFlash] = useState<string|null>(null);
  const [soundReady, setSoundReady] = useState(false);

  const applyStatus = useCallback((raw: any) => {
    if (!raw || typeof raw !== 'object') return;
    const connected = !!(raw.deriv_connected ?? raw.connected);
    setStatus({
      connected,
      deriv_connected: connected,
      ticks_collected: Number(raw.ticks_collected ?? 0),
      quotes_collected: Number(raw.quotes_collected ?? 0),
      symbol: raw.symbol ?? undefined,
    });
  }, []);

  const poll = useCallback(async () => {
    try { await api('/health'); setOnline(true); } catch { setOnline(false); }
    try { setDaily(await api<DailyStatus>('/signals/status')); } catch {}
    try { applyStatus(await api<ApiStatus>('/signals/worker-status')); } catch {}
  }, [applyStatus]);

  useEffect(() => { poll(); const id = setInterval(poll, 5000); return () => clearInterval(id); }, [poll]);

  useEffect(() => {
    const WS = (import.meta.env.VITE_WS_URL || 'ws://localhost:8000').replace(/^http/, 'ws');
    let statusWs: WebSocket; let noteWs: WebSocket;
    let statusTimer: ReturnType<typeof setTimeout>;
    let noteTimer: ReturnType<typeof setTimeout>;

    const connectStatus = () => {
      try {
        statusWs = new WebSocket(`${WS}/ws/status`);
        statusWs.onmessage = e => {
          try {
            const msg = JSON.parse(e.data);
            applyStatus(msg?.data ?? msg);
          } catch {}
        };
        statusWs.onclose = () => { statusTimer = setTimeout(connectStatus, 5000); };
        statusWs.onerror = () => statusWs.close();
      } catch {}
    };

    const connectNotes = () => {
      try {
        noteWs = new WebSocket(`${WS}/ws/notifications`);
        noteWs.onmessage = e => {
          try {
            const msg = JSON.parse(e.data);
            if (msg?.type !== 'signal_alert') return;
            const d = msg.data || {};
            const line = `${d.symbol || ''} ${d.direction || ''} ${d.barrier_input || ''} · p=${((d.calibrated_probability||0)*100).toFixed(1)}%`;
            setSignalFlash(line.trim());
            void playSignalAlertSound();
            if (typeof Notification !== 'undefined') {
              if (Notification.permission === 'granted') {
                new Notification('DerivQuant SIGNAL', {
                  body: line,
                  requireInteraction: true,
                });
              } else if (Notification.permission !== 'denied') {
                Notification.requestPermission();
              }
            }
          } catch {}
        };
        noteWs.onclose = () => { noteTimer = setTimeout(connectNotes, 5000); };
        noteWs.onerror = () => noteWs.close();
      } catch {}
    };

    connectStatus();
    connectNotes();
    return () => {
      clearTimeout(statusTimer);
      clearTimeout(noteTimer);
      statusWs?.close();
      noteWs?.close();
    };
  }, [applyStatus]);

  // Auto-hide big on-screen signal banner
  useEffect(() => {
    if (!signalFlash) return;
    const t = setTimeout(() => setSignalFlash(null), 20000);
    return () => clearTimeout(t);
  }, [signalFlash]);

  const enableSound = async () => {
    await unlockSignalAudio();
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      await Notification.requestPermission();
    }
    setSoundReady(true);
    await playSignalAlertSound(); // preview so user hears volume
  };

  const navItems: { id: Tab; label: string; icon: JSX.Element }[] = [
    { id: 'dashboard', label: 'Dashboard',    icon: <Activity size={16}/> },
    { id: 'train',     label: 'Train Model',  icon: <Brain size={16}/>    },
    { id: 'setup',     label: 'Configuration',icon: <Settings size={16}/> },
  ];

  const capPct = daily ? Math.min(100, (daily.signals_today / daily.max_signals) * 100) : 0;

  return (
    <div className="app-shell fade-in">
      {signalFlash && (
        <div className="signal-flash-banner" role="alert" onClick={() => setSignalFlash(null)}>
          <Volume2 size={28} />
          <div>
            <div className="signal-flash-title">SIGNAL ALERT</div>
            <div className="signal-flash-body">{signalFlash}</div>
          </div>
          <span className="text-xs" style={{opacity:0.8}}>click to dismiss</span>
        </div>
      )}

      {/* ── Sidebar ── */}
      <aside className="sidebar">
        {/* Logo */}
        <div className="logo">
          <div className="logo-icon"><Zap size={18} color="#fff" /></div>
          <div>
            <span className="logo-text">DerivQuant</span>
            <span className="logo-sub">ML Signal Engine</span>
          </div>
        </div>

        <button
          id="btn-enable-signal-sound"
          className={`btn ${soundReady ? 'btn-ghost' : 'btn-primary'} w-full`}
          style={{ margin: '0 0 0.75rem', justifyContent: 'center' }}
          onClick={enableSound}
          title="Browsers block sound until you click once"
        >
          <Volume2 size={14} />
          {soundReady ? 'Sound ready (test again)' : 'Enable signal sound'}
        </button>

        {/* Connection pills */}
        <div className="conn-pills">
          <div className={`conn-pill ${online ? 'online' : 'offline'}`}>
            <span className={`pill-dot ${online ? 'on' : 'off'}`} />
            {online ? 'Backend Online' : 'Backend Offline'}
          </div>
          <div className={`conn-pill ${status?.deriv_connected ? 'online' : 'offline'}`}>
            {status?.deriv_connected ? <Wifi size={11}/> : <WifiOff size={11}/>}
            {status?.deriv_connected ? 'Deriv Connected' : 'Deriv Reconnecting…'}
          </div>
        </div>

        {/* Nav */}
        <nav className="nav">
          {navItems.map(n => (
            <button key={n.id} id={`nav-${n.id}`} className={`nav-item ${tab===n.id?'active':''}`} onClick={() => setTab(n.id)}>
              <span className="nav-icon">{n.icon}</span>
              {n.label}
              <ChevronRight size={13} className="nav-chevron"/>
            </button>
          ))}
        </nav>

        {/* Cap widget */}
        <div className="cap-widget">
          <div className="cap-header">Daily Signal Cap</div>
          <div className="cap-numbers">
            <span className="cap-count">{daily?.signals_today ?? '–'}</span>
            <span className="cap-max">/ {daily?.max_signals ?? 3}</span>
            {daily?.cooldown_active
              ? <span className="badge badge-amber" style={{marginLeft:'auto'}}><Clock size={10}/>Cooldown</span>
              : <span className="badge badge-green" style={{marginLeft:'auto'}}>Ready</span>}
          </div>
          <div className="cap-bar"><div className="cap-bar-fill" style={{width:`${capPct}%`}}/></div>
          <div className="cap-meta">Resets midnight · {daily?.timezone ?? 'Asia/Colombo'}</div>
        </div>
      </aside>

      {/* ── Main ── */}
      <main className="main-content">
        {tab === 'dashboard' && <DashboardView apiStatus={status} />}
        {tab === 'train'     && <TrainView />}
        {tab === 'setup'     && <SetupView online={online} />}
      </main>
    </div>
  );
}

interface ModelInfo {
  direction?: string;
  selected_pipeline?: string;
  has_demonstrated_edge?: boolean;
  edge_description?: string;
  version_tag?: string;
  symbol?: string;
  barrier_distance?: number;
  metrics?: { auc_roc?: number };
}
interface ModelsResponse { trained: boolean; models: ModelInfo[]; min_ticks_to_train: number; recommended_ticks: number; }

// ── Dashboard ─────────────────────────────────────────────────────────────────
function DashboardView({ apiStatus }: { apiStatus: ApiStatus|null }) {
  const [sigs, setSigs]     = useState<SigData[]>([]);
  const [models, setModels] = useState<ModelsResponse|null>(null);
  const [dbTicks, setDbTicks] = useState<number|null>(null);
  const [chart, setChart]   = useState<ChartTicks|null>(null);
  const [candles, setCandles] = useState<ChartCandles|null>(null);
  const [tf, setTf]         = useState<ChartTf>('1m');
  const [loading, setLoad]  = useState(true);
  const [err, setErr]       = useState('');
  const [analyzeBusy, setAnalyzeBusy] = useState(false);
  const [analyzeMsg, setAnalyzeMsg] = useState('');
  const [analyzeOk, setAnalyzeOk] = useState(false);
  const [analysisRows, setAnalysisRows] = useState<Array<{
    direction: string; ok?: boolean; calibrated_probability?: number;
    breakeven_probability?: number; margin_over_breakeven?: number;
    meets_confidence?: boolean; confluence_met?: boolean;
    selected_pipeline?: string; reason?: string;
  }>>([]);
  const [perf, setPerf] = useState<{
    alerts_paused?: boolean; pause_reason?: string|null;
    performance?: { resolved?: number; win_rate?: number|null; ci_lower?: number|null; mean_breakeven?: number|null };
  }|null>(null);

  const load = useCallback(async () => {
    setLoad(true); setErr('');
    try {
      const [s, m, d, p] = await Promise.all([
        api<SigData[]>('/signals/?limit=20'),
        api<ModelsResponse>('/train/models'),
        api<DataInfo>('/train/data-info'),
        api<typeof perf>('/signals/performance?days=7').catch(() => null),
      ]);
      setSigs(s);
      setModels(m);
      setDbTicks(d.total_ticks ?? 0);
      setPerf(p);
    } catch (e:any) { setErr(e.message); }
    finally { setLoad(false); }
  }, []);

  const loadChart = useCallback(async () => {
    try {
      const sym = apiStatus?.symbol || 'R_100';
      if (tf === 'tick') {
        setChart(await api<ChartTicks>(`/signals/chart-ticks?symbol=${encodeURIComponent(sym)}&limit=400`));
      } else {
        setCandles(await api<ChartCandles>(
          `/signals/chart-candles?symbol=${encodeURIComponent(sym)}&interval=${tf}&limit=120`
        ));
      }
    } catch { /* keep last chart */ }
  }, [apiStatus?.symbol, tf]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    loadChart();
    const id = setInterval(loadChart, tf === 'tick' ? 2500 : 5000);
    return () => clearInterval(id);
  }, [loadChart, tf]);

  const analyzeGenerate = async () => {
    setAnalyzeBusy(true); setAnalyzeMsg(''); setAnalyzeOk(false); setAnalysisRows([]);
    try {
      const res = await api<{
        ok: boolean;
        reason?: string;
        signal?: SigData & { calibrated_probability?: number; direction?: string; signal_id?: string };
        analysis?: typeof analysisRows;
        thresholds?: { min_confidence: number; min_margin_over_breakeven: number };
      }>('/signals/generate', { method: 'POST', body: JSON.stringify({}) });

      setAnalysisRows(res.analysis || []);
      if (res.ok && res.signal) {
        const s = res.signal;
        const p = ((s.calibrated_probability ?? s.probability ?? 0) * 100).toFixed(1);
        setAnalyzeOk(true);
        setAnalyzeMsg(`Signal: ${s.direction?.toUpperCase()} · ${p}% confidence · ${s.signal_id}`);
        await load();
      } else {
        setAnalyzeOk(false);
        setAnalyzeMsg(res.reason || 'No high-confidence setup right now.');
      }
    } catch (e: any) {
      setAnalyzeOk(false);
      setAnalyzeMsg(e.message || 'Analyze failed');
    } finally {
      setAnalyzeBusy(false);
    }
  };

  const tickData = (chart?.points || []).map(p => ({
    t: new Date(p.epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    quote: p.quote,
  }));
  const candleRows = candles?.candles || [];
  const lastQuote = tf === 'tick'
    ? (chart?.last_quote ?? null)
    : (candles?.last_quote ?? null);
  const chartReady = tf === 'tick' ? tickData.length >= 2 : candleRows.length >= 2;
  const tfOptions: { id: ChartTf; label: string }[] = [
    { id: 'tick', label: 'Tick' },
    { id: '1m', label: '1m' },
    { id: '5m', label: '5m' },
    { id: '15m', label: '15m' },
  ];

  return (
    <>
      {/* Header */}
      <div className="glass page-header">
        <div>
          <h1 className="page-title">Signals Dashboard</h1>
          <p className="page-subtitle">
            {apiStatus?.symbol ? `Monitoring ${apiStatus.symbol}` : 'Waiting for confirmed settings'}
            &nbsp;·&nbsp;live {(apiStatus?.ticks_collected ?? 0).toLocaleString()}
            &nbsp;·&nbsp;DB {(dbTicks ?? 0).toLocaleString()} ticks saved
          </p>
        </div>
        <div className="flex gap-3 items-center">
          <span className="badge badge-amber"><AlertTriangle size={11}/>Research Mode</span>
          <button
            id="btn-analyze-signal"
            className="btn btn-primary"
            onClick={analyzeGenerate}
            disabled={analyzeBusy || !models?.trained}
            title="Require touch confluence plus high model confidence before creating a signal"
          >
            {analyzeBusy ? <Loader2 size={14} className="spin"/> : <Zap size={14}/>}
            Analyze & Signal
          </button>
          <button id="btn-refresh-signals" className="btn btn-ghost" onClick={load}>
            {loading ? <Loader2 size={14} className="spin"/> : <RefreshCw size={14}/>} Refresh
          </button>
        </div>
      </div>

      {(analyzeMsg || analysisRows.length > 0) && (
        <div className={`alert ${analyzeOk ? 'alert-success' : 'alert-warning'}`} style={{marginBottom:'1rem'}}>
          <AlertTriangle size={14}/>
          <div style={{flex:1}}>
            <div>{analyzeMsg}</div>
            {analysisRows.length > 0 && (
              <div className="analyze-grid" style={{marginTop:8}}>
                {analysisRows.map(a => (
                  <div key={a.direction} className="analyze-row font-mono text-xs">
                    <strong>{(a.direction || '').toUpperCase()}</strong>
                    {!a.ok && <span> — {a.reason || 'n/a'}</span>}
                    {a.ok && (
                      <span>
                        {' '}p={(a.calibrated_probability! * 100).toFixed(1)}%
                        {' · '}BE={(a.breakeven_probability! * 100).toFixed(1)}%
                        {' · '}margin={(a.margin_over_breakeven! * 100).toFixed(1)}%
                        {a.confluence_met
                          ? <span className="text-green"> · confluence ✓</span>
                          : <span className="text-dim"> · no confluence</span>}
                        {a.meets_confidence
                          ? <span className="text-green"> · READY</span>
                          : <span className="text-dim"> · below bar</span>}
                        {a.selected_pipeline ? ` · ${a.selected_pipeline}` : ''}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Live Volatility chart */}
      <div className="glass chart-panel">
        <div className="section-header">
          <h3 className="section-title">
            <BarChart2 size={16}/>
            Live {(tf === 'tick' ? chart?.symbol : candles?.symbol) || apiStatus?.symbol || 'R_100'} Chart
            {tf !== 'tick' && <span className="badge badge-dim" style={{marginLeft:8}}>Candles</span>}
          </h3>
          <div className="flex items-center gap-3">
            <div className="tf-picker" role="group" aria-label="Chart timeframe">
              {tfOptions.map(opt => (
                <button
                  key={opt.id}
                  type="button"
                  className={`tf-btn ${tf === opt.id ? 'active' : ''}`}
                  onClick={() => setTf(opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            {lastQuote != null && (
              <span className="font-mono text-sm glow-text-cyan">{lastQuote.toFixed(4)}</span>
            )}
            <span className="badge badge-dim">
              {tf === 'tick' ? `${chart?.count ?? 0} ticks` : `${candles?.count ?? 0} candles`}
            </span>
          </div>
        </div>
        {!chartReady ? (
          <div className="empty-state" style={{padding:'2rem'}}>
            <p className="text-sm text-secondary">Waiting for chart data…</p>
            <p className="text-xs text-dim mt-1">Need more ticks for {tf === 'tick' ? 'tick' : `${tf} candle`} view.</p>
          </div>
        ) : tf === 'tick' ? (
          <div className="live-chart">
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={tickData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="volFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.35}/>
                    <stop offset="100%" stopColor="#00d4ff" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(0,212,255,0.06)" vertical={false}/>
                <XAxis dataKey="t" tick={{ fill: '#3d5070', fontSize: 10 }} minTickGap={40} axisLine={false} tickLine={false}/>
                <YAxis domain={['auto', 'auto']} tick={{ fill: '#3d5070', fontSize: 10 }} width={64} axisLine={false} tickLine={false}/>
                <Tooltip
                  contentStyle={{ background: 'rgba(8,18,36,0.95)', border: '1px solid rgba(0,212,255,0.2)', borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: '#6b80a8' }}
                  itemStyle={{ color: '#00d4ff' }}
                />
                <Area type="monotone" dataKey="quote" stroke="#00d4ff" strokeWidth={2} fill="url(#volFill)" isAnimationActive={false} name="Price"/>
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="live-chart">
            <CandleChart data={candleRows} interval={tf} />
          </div>
        )}
      </div>

      {/* Stat cards */}
      <div className="stat-grid">
        <div className="stat-card cyan">
          <div className="stat-icon"><Database size={18} color="var(--cyan)"/></div>
          <div className="stat-value glow-text-cyan">{(dbTicks ?? apiStatus?.ticks_collected ?? 0).toLocaleString()}</div>
          <div className="stat-label">Ticks in Database</div>
        </div>
        <div className="stat-card green">
          <div className="stat-icon"><TrendingUp size={18} color="var(--green)"/></div>
          <div className="stat-value glow-text-green">{(apiStatus?.quotes_collected ?? 0).toLocaleString()}</div>
          <div className="stat-label">Quotes Polled</div>
        </div>
        <div className="stat-card amber">
          <div className="stat-icon"><Signal size={18} color="var(--amber)"/></div>
          <div className="stat-value glow-text-amber">{sigs.length}</div>
          <div className="stat-label">Signals Generated</div>
        </div>
      </div>

      {/* Two-column */}
      <div className="grid-2">
        {/* Model readiness */}
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Cpu size={16}/>Model Readiness</h3>
          </div>
          {!models?.trained && (
            <>
              {['XGBoost','CatBoost','LSTM','Ensembles'].map(name => (
                <div key={name} className="model-row">
                  <div>
                    <div className="text-sm font-medium">{name}</div>
                    <div className="text-xs text-dim mt-1">Need ≥ 1,000 ticks · recommend 5,000+</div>
                  </div>
                  <span className="badge badge-amber" style={{marginLeft:'1rem',flexShrink:0}}>Train required</span>
                </div>
              ))}
              <p className="text-xs text-dim mt-1">
                Go to <span className="text-cyan">Train Model</span> after you have enough ticks.
              </p>
            </>
          )}
          {models?.trained && models.models.map(m => (
            <div key={`${m.direction}-${m.version_tag}`} className="model-row">
              <div>
                <div className="text-sm font-medium">
                  {(m.direction || '').toUpperCase()} · {m.selected_pipeline || 'pipeline'}
                </div>
                <div className="text-xs text-dim mt-1 font-mono">
                  {m.symbol} barrier={m.barrier_distance} · AUC {(m.metrics?.auc_roc ?? 0).toFixed(3)}
                  {m.version_tag ? ` · ${m.version_tag}` : ''}
                </div>
              </div>
              {m.has_demonstrated_edge
                ? <span className="badge badge-green" style={{marginLeft:'1rem',flexShrink:0}}>Edge OK</span>
                : <span className="badge badge-amber" style={{marginLeft:'1rem',flexShrink:0}}>No edge</span>}
            </div>
          ))}
          {perf?.alerts_paused && (
            <div className="alert alert-warning mt-2">
              <AlertTriangle size={14}/>
              Auto alerts paused{perf.pause_reason ? `: ${perf.pause_reason}` : '.'}
              {' '}Analyze & Signal still available for research.
            </div>
          )}
          {perf?.performance && (perf.performance.resolved ?? 0) > 0 && (
            <p className="text-xs text-dim mt-2 font-mono">
              Live 7d: n={perf.performance.resolved}
              {perf.performance.win_rate != null ? ` · win ${(perf.performance.win_rate*100).toFixed(1)}%` : ''}
              {perf.performance.ci_lower != null ? ` · CI≥${(perf.performance.ci_lower*100).toFixed(1)}%` : ''}
              {perf.performance.mean_breakeven != null ? ` · BE ${(perf.performance.mean_breakeven*100).toFixed(1)}%` : ''}
            </p>
          )}
          {models?.trained && (
            <p className="text-xs text-dim mt-3">
              Auto alerts need <span className="text-cyan">Edge OK</span> + confluence + EV gates.
              <span className="text-amber"> Analyze & Signal</span> uses the same confluence/EV evidence with a confidence floor (default ≥95%).
              Max 3/day Asia/Colombo.
            </p>
          )}
        </div>

        {/* Signal feed */}
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><Activity size={16}/>Recent Signals</h3>
            {sigs.length > 0 && <span className="badge badge-dim">{sigs.length} total</span>}
          </div>

          {err && <div className="alert alert-error"><XCircle size={14}/>{err}</div>}

          {!loading && !err && sigs.length === 0 && (
            <div className="empty-state">
              <div className="empty-icon"><ShieldCheck size={24} color="var(--text-dim)"/></div>
              <p className="text-sm font-medium text-secondary">No signals yet</p>
              <p className="text-xs text-dim mt-1">Train a model, then Analyze & Signal — or wait for Edge OK auto alerts.</p>
            </div>
          )}

          {sigs.map(s => {
            const prob = s.probability ?? s.calibrated_probability ?? 0;
            return (
              <div key={s.signal_id} className="signal-row">
                <div>
                  <div className="text-sm font-medium">
                    {s.symbol} &nbsp;
                    <span className={s.direction==='upper' ? 'text-cyan' : 'text-amber'}>
                      {s.direction === 'upper' ? '▲ Upper' : '▼ Lower'}
                    </span>
                    {!s.is_validated && <span className="badge badge-amber" style={{marginLeft:8}}>Forced / unvalidated</span>}
                  </div>
                  <div className="text-xs text-dim mt-1">{new Date(s.created_at).toLocaleString()}</div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-semibold text-primary">{(prob*100).toFixed(1)}%</span>
                  <span className={`badge ${s.status==='active'?'badge-green':'badge-dim'}`}>{s.status}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

// ── Train ─────────────────────────────────────────────────────────────────────
function TrainView() {
  const [info, setInfo]           = useState<DataInfo|null>(null);
  const [ts, setTs]               = useState<TrainStatus>({ status:'idle', progress:'' });
  const [loadingInfo, setLI]      = useState(true);
  const [symbol, setSymbol]       = useState('R_100');
  const [barrier, setBarrier]     = useState('0.09');
  const [dir, setDir]             = useState('both');
  const [dur, setDur]             = useState('540');
  const pollRef = useRef<ReturnType<typeof setInterval>|null>(null);

  const loadInfo = useCallback(async (silent = false) => {
    if (!silent) setLI(true);
    try { setInfo(await api<DataInfo>('/train/data-info')); } catch {}
    if (!silent) setLI(false);
  }, []);

  useEffect(() => { loadInfo(); }, [loadInfo]);

  // Live DB tick counts grow while the worker collects — refresh quietly
  useEffect(() => {
    const t = setInterval(() => { loadInfo(true); }, 10000);
    return () => clearInterval(t);
  }, [loadInfo]);

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await api<TrainStatus>('/train/status');
        setTs(s);
        if (s.status !== 'running') { clearInterval(pollRef.current!); pollRef.current = null; if (s.status==='done') loadInfo(); }
      } catch {}
    };
    if (ts.status === 'running' && !pollRef.current) { pollRef.current = setInterval(poll, 2000); }
    return () => { if (pollRef.current && ts.status !== 'running') { clearInterval(pollRef.current); pollRef.current=null; } };
  }, [ts.status, loadInfo]);

  const start = async () => {
    try {
      await api(`/train/start?${new URLSearchParams({symbol,barrier_distance:barrier,barrier_direction:dir,duration_seconds:dur})}`, {method:'POST'});
      setTs({ status:'running', progress:'Starting pipeline…' });
    } catch (e:any) { setTs({ status:'error', progress:'', error: e.message }); }
  };

  const symInfo  = info?.symbols?.find(s => s.symbol === symbol);
  const tickCount = symInfo?.tick_count ?? 0;
  const canTrain = tickCount >= 1000 && ts.status !== 'running';

  return (
    <>
      <div className="glass page-header">
        <div>
          <h1 className="page-title">Train ML Model</h1>
          <p className="page-subtitle">Train on ticks already saved by the live worker — no separate download needed.</p>
        </div>
      </div>

      {/* Data availability */}
      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Database size={16}/>Training Data (from live DB)</h3>
          <button id="btn-refresh-data" className="btn btn-ghost" onClick={() => loadInfo()}>
            {loadingInfo ? <Loader2 size={13} className="spin"/> : <RefreshCw size={13}/>} Refresh
          </button>
        </div>
        {loadingInfo && <div className="flex items-center gap-2 text-dim text-sm"><Loader2 size={14} className="spin"/>Checking database…</div>}
        {!loadingInfo && info && (info.has_data ? (
          info.symbols?.map(sym => (
            <div key={sym.symbol} className="data-row">
              <div>
                <div className="text-sm font-medium text-primary">{sym.symbol}</div>
                <div className="text-xs text-dim mt-1 font-mono">
                  {sym.oldest?.slice(0,16).replace('T',' ')} → {sym.newest?.slice(0,16).replace('T',' ')}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="text-right">
                  <div className="font-mono font-semibold text-sm">{sym.tick_count.toLocaleString()} ticks</div>
                  <div className="text-xs text-dim font-mono">
                    {sym.span_hours != null ? `${sym.span_hours}h span` : ''}
                    {sym.est_labels != null ? ` · ~${sym.est_labels} labels` : ''}
                  </div>
                </div>
                <span className={`badge ${sym.ready_to_train ? 'badge-green' : 'badge-amber'}`}>
                  {sym.ready_to_train
                    ? '✓ Ready'
                    : `Need ~${info.min_span_hours ?? sym.min_span_hours ?? 16}h span`}
                </span>
              </div>
            </div>
          ))
        ) : (
          <div className="alert alert-warning">
            <AlertTriangle size={14}/>
            {info.message ?? 'No tick data yet. Confirm Setup settings so the worker starts collecting, then wait for ticks to save.'}
          </div>
        ))}
        {!loadingInfo && info && info.has_data && (
          <div className="alert alert-info mt-2">
            <Info size={14}/>
            {info.note ??
              `Non-overlapping samples need ≥${info.min_labels_required ?? 100} labels ` +
              `(≈${info.min_span_hours ?? 16}h continuous coverage). Tick count alone is not enough.`}
          </div>
        )}
      </div>

      {/* Config + start */}
      <div className="glass">
        <div className="section-header mb-4">
          <h3 className="section-title"><Brain size={16}/>Training Configuration</h3>
        </div>
        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Symbol</label>
            <select id="train-symbol" className="form-select" value={symbol} onChange={e=>setSymbol(e.target.value)}>
              <option value="R_100">Volatility 100 Index (R_100)</option>
              <option value="1HZ100V">Volatility 100 (1s) Index (1HZ100V)</option>
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Direction</label>
            <select id="train-direction" className="form-select" value={dir} onChange={e=>setDir(e.target.value)}>
              <option value="both">Both (Upper + Lower)</option>
              <option value="upper">Upper Touch (+barrier)</option>
              <option value="lower">Lower Touch (−barrier)</option>
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Barrier Distance (price points)</label>
            <input id="train-barrier" type="number" step="0.01" className="form-input" value={barrier} onChange={e=>setBarrier(e.target.value)}/>
            <div className="form-hint">Relative offset from spot (e.g. 0.09 or 0.2), not percent. Must match Setup.</div>
          </div>
          <div className="form-group">
            <label className="form-label">Duration (seconds)</label>
            <input id="train-duration" type="number" className="form-input" value={dur} onChange={e=>setDur(e.target.value)}/>
          </div>
        </div>

        {ts.status === 'running' && <div className="train-progress"><Loader2 size={16} className="spin"/>{ts.progress || 'Training…'}</div>}
        {ts.status === 'error'   && <div className="alert alert-error"><XCircle size={14}/>{ts.error}</div>}
        {ts.status === 'done' && ts.result && <div className="alert alert-success"><CheckCircle size={14}/>Model saved · {ts.result.timestamp?.slice(0,15).replace('T',' ')}</div>}

        <button
          id="btn-start-training"
          className="btn btn-primary w-full"
          style={{marginTop:'0.25rem', justifyContent:'center', padding:'0.85rem'}}
          onClick={start}
          disabled={!canTrain}
        >
          {ts.status==='running'
            ? <><Loader2 size={16} className="spin"/>Training in progress…</>
            : <><Brain size={16}/>Train on {tickCount.toLocaleString()} DB ticks</>}
        </button>
        {!canTrain && ts.status !== 'running' && (
          <p className="text-xs text-dim mt-2" style={{textAlign:'center'}}>
            Need ≥ 1,000 ticks for "{symbol}" · currently {tickCount.toLocaleString()}.
            Confirm Setup and let the live worker collect more.
          </p>
        )}
      </div>

      {/* Results */}
      {ts.status === 'done' && ts.result && (
        <div className="glass">
          <div className="section-header">
            <h3 className="section-title"><BarChart2 size={16}/>Training Results</h3>
            <span className="badge badge-green">Complete</span>
          </div>

          <div className={`alert ${ts.result.has_demonstrated_edge ? 'alert-success' : 'alert-warning'} mb-4`}>
            {ts.result.has_demonstrated_edge
              ? <><CheckCircle size={14}/>Selected pipeline shows held-out edge</>
              : <><AlertTriangle size={14}/>No demonstrated edge — actionable alerts suppressed</>}
            <div className="text-xs mt-1">{ts.result.edge_description}</div>
          </div>

          <div className="grid-3 mb-4">
            {[
              { label:'Selected', val: ts.result.selected_pipeline ?? '—', color:'cyan' },
              { label:'Sel. AUC', val: (ts.result.selected?.auc_roc ?? ts.result.catboost?.auc_roc)?.toFixed(4) ?? '—', color:'green' },
              { label:'Touch Rate', val: ((ts.result.historical_frequency?.touch_rate??0)*100).toFixed(1)+'%', color:'amber' },
            ].map(m => (
              <div key={m.label} className="glass" style={{padding:'1rem', textAlign:'center'}}>
                <div className={`font-mono text-2xl font-bold glow-text-${m.color}`}>{m.val}</div>
                <div className="text-xs text-dim uppercase tracking-wide mt-1">{m.label}</div>
              </div>
            ))}
          </div>

          <div className="text-xs uppercase tracking-wide text-dim mb-2">Candidate comparison (test)</div>
          {[
            ['baseline', ts.result.baseline],
            ['xgboost', ts.result.xgboost],
            ['catboost', ts.result.catboost],
            ['lstm', ts.result.lstm],
            ['weighted', ts.result.weighted],
            ['stacking', ts.result.stacking],
          ].map(([name, m]) => {
            const metrics = m as ModelMetrics | undefined;
            const selected = ts.result?.selected_pipeline === name;
            return (
              <div key={String(name)} className="feature-row">
                <span className="text-sm flex-1">
                  {String(name)}{selected ? ' ★' : ''}
                  {selected && <span className="badge badge-green" style={{marginLeft:8}}>selected</span>}
                </span>
                <span className="text-xs text-dim font-mono">AUC {(metrics?.auc_roc??0).toFixed(3)}</span>
                <span className="text-xs text-dim font-mono" style={{width:90,textAlign:'right'}}>Brier {(metrics?.brier_score??0).toFixed(3)}</span>
              </div>
            );
          })}

          {ts.result.split_info && (
            <div className="text-xs text-dim font-mono mt-4 mb-2">
              Split — Train: {ts.result.split_info.train} · Val: {ts.result.split_info.val} · Cal: {ts.result.split_info.cal} · Test: {ts.result.split_info.test} · Purged: {ts.result.split_info.purged}
              {ts.result.split_info.unresolved_labels != null && <> · Unresolved: {ts.result.split_info.unresolved_labels}</>}
            </div>
          )}
          <div className="text-xs text-dim mb-3">
            Barrier unit: <span className="font-mono">{ts.result.barrier_unit ?? 'relative_price_points'}</span>
            {ts.result.quote_backtest_note && <div className="mt-1">{ts.result.quote_backtest_note}</div>}
          </div>
          {(ts.result.top_features ?? []).length > 0 && (
            <>
              <div className="divider"/>
              <div className="text-xs uppercase tracking-wide text-dim mb-3">Top CatBoost Features</div>
              {ts.result.top_features!.slice(0,8).map((f,i) => (
                <div key={f.feature} className="feature-row">
                  <span className="text-xs text-dim font-mono" style={{width:22}}>#{i+1}</span>
                  <span className="text-sm flex-1 truncate">{f.feature}</span>
                  <div className="feat-bar-track" style={{width:120}}>
                    <div className="feat-bar-fill" style={{width:`${Math.min(100,f.importance)}%`}}/>
                  </div>
                  <span className="text-xs text-dim font-mono" style={{width:38,textAlign:'right'}}>{f.importance.toFixed(1)}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </>
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────
function SetupView({ online }: { online: boolean }) {
  const [sym, setSym]       = useState('R_100');
  const [dur, setDur]       = useState('9');
  const [unit, setUnit]     = useState('m');
  const [barrier, setBar]   = useState('0.09');
  const [direction, setDir] = useState<'both'|'upper'|'lower'>('both');
  const [saving, setSaving] = useState(false);
  const [checking, setChk]  = useState(false);
  const [saveMsg, setSM]    = useState('');
  const [saveOk, setSok]    = useState(false);
  const [chkMsg, setCM]     = useState('');

  const checkApi = async () => {
    setChk(true); setCM('');
    try {
      const r = await api<{success:boolean;error?:string;quotes?:Array<{direction:string;barrier:string;ask_price?:number}>}>(
        `/setup/discover/quote?symbol=${sym}&barrier=${encodeURIComponent(barrier)}&duration=${dur}&duration_unit=${unit}&direction=${direction}`,
        {method:'POST'}
      );
      if (r.success && r.quotes?.length) {
        const parts = r.quotes.map(q => `${q.direction} ${q.barrier} (ask ${q.ask_price})`).join(' · ');
        setCM(`✓ Quote OK — ${parts}`);
      } else {
        setCM(r.success ? '✓ Quote received from Deriv — contract parameters are valid!' : `✗ ${r.error}`);
      }
    } catch(e:any) { setCM(`✗ ${e.message}`); }
    setChk(false);
  };

  const save = async () => {
    setSaving(true); setSM('');
    try {
      const ds = unit === 'm' ? parseInt(dur)*60 : parseInt(dur);
      await api('/setup/confirm', { method:'POST', body: JSON.stringify({
        symbol: sym, display_name: sym,
        duration_value: parseInt(dur), duration_unit: unit, duration_seconds: ds,
        barrier_input: barrier, barrier_type: 'relative',
        barrier_direction: direction,
      })});
      const dirLabel = direction === 'both' ? 'upper + lower' : direction;
      setSok(true); setSM(`✓ Settings saved (${dirLabel}) — worker will activate within 10 seconds.`);
    } catch(e:any) { setSok(false); setSM(`✗ ${e.message}`); }
    setSaving(false);
  };

  return (
    <div style={{maxWidth: 680}}>
      <div className="glass page-header" style={{marginBottom:'1.5rem'}}>
        <div>
          <h1 className="page-title">Configuration</h1>
          <p className="page-subtitle">Set the trading instrument and contract parameters for the signal engine.</p>
        </div>
      </div>

      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Settings size={16}/>Contract Parameters</h3>
          {!online && <span className="badge badge-red">Backend Offline</span>}
        </div>

        <div className="form-group">
          <label className="form-label">Instrument</label>
          <select id="setup-symbol" className="form-select" value={sym} onChange={e=>setSym(e.target.value)}>
            <option value="R_100">Volatility 100 Index (R_100)</option>
            <option value="1HZ100V">Volatility 100 (1s) Index (1HZ100V)</option>
          </select>
        </div>

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Duration</label>
            <input id="setup-duration" type="number" className="form-input" value={dur} onChange={e=>setDur(e.target.value)}/>
          </div>
          <div className="form-group">
            <label className="form-label">Unit</label>
            <select id="setup-unit" className="form-select" value={unit} onChange={e=>setUnit(e.target.value)}>
              <option value="m">Minutes</option>
              <option value="s">Seconds</option>
            </select>
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">Signal Direction</label>
          <select id="setup-direction" className="form-select" value={direction} onChange={e=>setDir(e.target.value as 'both'|'upper'|'lower')}>
            <option value="both">Both (Upper + Lower)</option>
            <option value="upper">Upper touch only (+barrier)</option>
            <option value="lower">Lower touch only (−barrier)</option>
          </select>
          <div className="form-hint">Both mode watches <span className="font-mono">+offset</span> and <span className="font-mono">−offset</span> and can alert either side.</div>
        </div>

        <div className="form-group">
          <label className="form-label">Barrier Offset</label>
          <input id="setup-barrier" type="text" className="form-input" value={barrier} onChange={e=>setBar(e.target.value)}/>
          <div className="form-hint">
            Relative <strong>price-point</strong> offset from spot (not percent), e.g. <span className="font-mono">0.09</span> or <span className="font-mono">0.2</span>.
            {direction === 'both'
              ? <> Engine uses <span className="font-mono">+{barrier.replace(/^[-+]/,'')}</span> and <span className="font-mono">−{barrier.replace(/^[-+]/,'')}</span>.</>
              : direction === 'upper'
                ? <> Engine uses <span className="font-mono">+{barrier.replace(/^[-+]/,'')}</span>.</>
                : <> Engine uses <span className="font-mono">−{barrier.replace(/^[-+]/,'')}</span>.</>}
            {' '}Confirm with a live quote before expecting alerts. Instrument: Volatility 100 (<span className="font-mono">R_100</span>).
          </div>
        </div>

        {chkMsg && (
          <div className={`alert ${chkMsg.startsWith('✓') ? 'alert-success' : 'alert-error'}`}>
            {chkMsg.startsWith('✓') ? <CheckCircle size={14}/> : <XCircle size={14}/>}
            {chkMsg}
          </div>
        )}
        {saveMsg && (
          <div className={`alert ${saveOk ? 'alert-success' : 'alert-error'}`}>
            {saveOk ? <CheckCircle size={14}/> : <XCircle size={14}/>}
            {saveMsg}
          </div>
        )}

        <div className="warn-box">
          <div className="warn-title"><AlertTriangle size={14}/>Read-Only Research System</div>
          <p className="warn-body">This system <strong>never</strong> executes trades automatically. All trades must be placed manually in your Deriv account. This is strictly a signal generation tool.</p>
        </div>

        <div className="divider"/>

        <div className="flex gap-3">
          <button
            id="btn-check-api"
            className="btn btn-ghost"
            style={{flex:1}}
            onClick={checkApi}
            disabled={checking || !online}
          >
            {checking ? <Loader2 size={14} className="spin"/> : <Wifi size={14}/>}
            {checking ? 'Checking…' : 'Check API'}
          </button>
          <button
            id="btn-save-settings"
            className="btn btn-primary"
            style={{flex:2, justifyContent:'center'}}
            onClick={save}
            disabled={saving || !online}
          >
            {saving ? <Loader2 size={14} className="spin"/> : <ShieldCheck size={14}/>}
            {saving ? 'Saving…' : 'Confirm & Save Settings'}
          </button>
        </div>
        {!online && (
          <p className="text-xs text-dim mt-2" style={{textAlign:'center'}}>
            Start Docker containers first — backend is offline.
          </p>
        )}
      </div>
    </div>
  );
}
