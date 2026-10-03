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
  train_defaults?: { min_calibration_samples: number; min_edge_selected: number };
  train_prefs?: { min_calibration_samples: number; min_edge_selected: number; auto_raise?: boolean };
  train_recommended?: {
    min_calibration_samples: number;
    min_edge_selected: number;
    label: string;
    span_hours: number;
  };
  env_is_default_only?: boolean;
  symbols?: Array<{
    symbol: string;
    tick_count: number;
    oldest?: string;
    newest?: string;
    ready_to_train: boolean;
    est_labels?: number;
    min_labels_required?: number;
    span_hours?: number;
    min_span_hours?: number;
  }>;
}
interface DownloadStatus {
  status: 'idle' | 'running' | 'done' | 'error';
  progress: string;
  ticks_downloaded?: number;
  error?: string | null;
  target_ticks?: number | null;
}
interface DailyStatus { signals_today: number; max_signals: number; cooldown_active: boolean; timezone: string; }
type Tab = 'dashboard' | 'train' | 'setup';

function hoursBetween(oldest?: string, newest?: string): number | null {
  if (!oldest || !newest) return null;
  const a = Date.parse(oldest);
  const b = Date.parse(newest);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return null;
  return (b - a) / 3_600_000;
}

/** Presets shown in Train UI — keep cal/edge paired for clear guidance. */
const STRICTNESS_LEVELS = [
  {
    id: 'starter',
    cal: 20,
    edge: 15,
    minHours: 16,
    minLabels: 100,
    title: 'Starter',
    when: 'First train / about 1 day of ticks',
    happens: [
      'Training is more likely to finish with ~16–24h of data.',
      'Calibration uses fewer held-out samples (still valid, less stable).',
      '“Demonstrated edge” is harder to claim — alerts stay cautious.',
    ],
    risk: 'Model can save, but probabilities/edge are less trustworthy.',
  },
  {
    id: 'balanced',
    cal: 30,
    edge: 20,
    minHours: 24,
    minLabels: 140,
    title: 'Balanced',
    when: '1+ full day of continuous coverage',
    happens: [
      'Needs a larger calibration slice — may fail if span is still ~16h.',
      'Edge check needs more selected test signals.',
      'Better probability calibration than Starter.',
    ],
    risk: 'If data is short, train may error on calibration — lower level or wait.',
  },
  {
    id: 'medium',
    cal: 40,
    edge: 25,
    minHours: 48,
    minLabels: 280,
    title: 'Medium-strict',
    when: 'About 2+ days of ticks',
    happens: [
      'Stricter gates: more cal samples + more edge evidence required.',
      'Fewer weak models get “edge” status.',
      'Good step-up after you have collected a second day.',
    ],
    risk: 'Too early → calibration/edge failures. Retrain later when meters fill.',
  },
  {
    id: 'strict',
    cal: 50,
    edge: 30,
    minHours: 72,
    minLabels: 420,
    title: 'Strict (best)',
    when: '3+ days continuous (recommended long-term)',
    happens: [
      'Strongest calibration requirement (closer to production hardening).',
      'Edge needs solid selected-test count before alerts can trust it.',
      'Best quality when the DB has multi-day history.',
    ],
    risk: 'Will often fail on day-1 data. Use only after span meters show enough.',
  },
] as const;

function matchStrictnessLevel(cal: number, edge: number) {
  return STRICTNESS_LEVELS.find(l => l.cal === cal && l.edge === edge)
    ?? STRICTNESS_LEVELS.find(l => l.cal === cal)
    ?? null;
}

function explainTrainError(err?: string | null): { title: string; steps: string[] } | null {
  if (!err) return null;
  const e = err.toLowerCase();
  if (e.includes('insufficient resolved labeled')) {
    return {
      title: 'Not enough labeled examples yet',
      steps: [
        'Training builds 1 label every contract duration (default 9 minutes), not every tick.',
        'You need about 100 labels ≈ 16–20 hours of continuous tick coverage.',
        'Confirm Setup so the worker keeps collecting, or use “Fill history (~24h)” below.',
        'Then train again. Tick count alone is not enough — time span matters.',
      ],
    };
  }
  if (e.includes('insufficient calibration samples')) {
    return {
      title: 'Calibration split was too small',
      steps: [
        'Data is split into train / validate / calibrate / test, then some rows are purged to prevent leakage.',
        'With ~1 day of data the calibration slice can fall below the minimum.',
        'In this Train page pick Starter (cal 20 / edge 15), or wait for 2–3 more days — no .env / VPS edit needed.',
        'Training can still finish without “demonstrated edge”; alerts stay strict until edge is proven.',
      ],
    };
  }
  if (e.includes('not enough ticks')) {
    return {
      title: 'Database still has too few ticks',
      steps: [
        'Confirm Setup (same symbol/barrier/duration you will train).',
        'Wait for the live worker to save ticks, or use “Fill history (~24h)”.',
        'Minimum to start the pipeline: 1,000 ticks. Comfortable: 25,000+ with 16h+ span.',
      ],
    };
  }
  if (e.includes('dataset split failed') || e.includes('empty set after purging')) {
    return {
      title: 'Split failed after purge gaps',
      steps: [
        'Contract windows overlap the next split, so many samples were removed.',
        'Collect a longer continuous span (ideally 1+ full day), then retrain.',
      ],
    };
  }
  if (e.includes('only one label') || e.includes('all the same class') || e.includes('single_class')) {
    return {
      title: 'Labels not mixed enough (almost all touch or all miss)',
      steps: [
        'With your barrier/duration, nearly every sample had the same outcome (often all touches).',
        'ML needs both wins and losses in train/calibration to score and calibrate.',
        'Collect more days of ticks, or try a slightly harder barrier in Setup + Train, then retrain.',
      ],
    };
  }
  return {
    title: 'Training stopped',
    steps: [
      'Read the technical message above.',
      'Usually: more continuous ticks, matching Setup barrier/duration, then retry.',
      'You do not need the VPS for normal train/download — use this page.',
    ],
  };
}

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
  const [analyzeWatching, setAnalyzeWatching] = useState(false);
  const [watchMode, setWatchMode] = useState<'standard' | 'force_model_candles' | null>(null);
  const [analyzeMsg, setAnalyzeMsg] = useState('');
  const [analyzeOk, setAnalyzeOk] = useState(false);
  const [forceMinP, setForceMinP] = useState(0.80);
  const [tradeResultMsg, setTradeResultMsg] = useState('');
  const [analysisRows, setAnalysisRows] = useState<Array<{
    direction: string; ok?: boolean; calibrated_probability?: number;
    breakeven_probability?: number; margin_over_breakeven?: number;
    meets_confidence?: boolean; confluence_met?: boolean;
    candle_confirm_met?: boolean; candle_confirm_score?: number;
    selected_pipeline?: string; reason?: string;
  }>>([]);
  const [perf, setPerf] = useState<{
    alerts_paused?: boolean; pause_reason?: string|null;
    performance?: { resolved?: number; win_rate?: number|null; ci_lower?: number|null; mean_breakeven?: number|null };
  }|null>(null);
  const watchPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
    api<{ force_min_probability?: number }>('/setup/trade-prefs')
      .then(r => {
        if (r.force_min_probability != null) setForceMinP(Number(r.force_min_probability));
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    loadChart();
    const id = setInterval(loadChart, tf === 'tick' ? 2500 : 5000);
    return () => clearInterval(id);
  }, [loadChart, tf]);

  type WatchStatus = {
    running?: boolean;
    want_running?: boolean;
    mode?: string;
    ok?: boolean;
    message?: string;
    reason?: string;
    analysis?: typeof analysisRows;
    signal?: SigData & { calibrated_probability?: number; direction?: string; signal_id?: string };
    trade?: { ok?: boolean; skipped?: boolean; contract_id?: number|string; error?: string; stake?: number };
  };

  const applyWatchStatus = useCallback(async (st: WatchStatus, { reloadOnSignal = true } = {}) => {
    const mode = (st.mode === 'force_model_candles' ? 'force_model_candles' : 'standard') as
      'standard' | 'force_model_candles';
    setAnalyzeMsg(st.message || st.reason || '');
    setAnalyzeOk(!!st.ok);
    setAnalysisRows(st.analysis || []);
    if (st.trade && !st.trade.skipped) {
      if (st.trade.ok) {
        setTradeResultMsg(`Auto-trade OK · contract ${st.trade.contract_id} · stake ${st.trade.stake}`);
      } else if (st.trade.error) {
        setTradeResultMsg(`Auto-trade failed: ${st.trade.error}`);
      }
    }
    if (st.running) {
      setAnalyzeWatching(true);
      setAnalyzeBusy(true);
      setWatchMode(mode);
    } else {
      setAnalyzeWatching(false);
      setAnalyzeBusy(false);
      setWatchMode(null);
      if (watchPollRef.current) {
        clearInterval(watchPollRef.current);
        watchPollRef.current = null;
      }
      if (reloadOnSignal && st.ok && st.signal) {
        await load();
      }
    }
  }, [load]);

  const pollWatchStatus = useCallback(() => {
    if (watchPollRef.current) clearInterval(watchPollRef.current);
    watchPollRef.current = setInterval(async () => {
      try {
        const st = await api<WatchStatus>('/signals/watch/status');
        await applyWatchStatus(st);
      } catch { /* ignore transient */ }
    }, 1000);
  }, [applyWatchStatus]);

  const stopAnalyzeWatch = async () => {
    try {
      const st = await api<WatchStatus>('/signals/watch/stop', { method: 'POST', body: '{}' });
      await applyWatchStatus(st, { reloadOnSignal: false });
    } catch {
      setAnalyzeWatching(false);
      setWatchMode(null);
      setAnalyzeBusy(false);
      setAnalyzeMsg('Watching stopped.');
    }
  };

  const analyzeGenerate = async (mode: 'standard' | 'force_model_candles' = 'standard') => {
    // Server-side watch — survives tab close / re-login
    setAnalyzeBusy(true);
    setAnalyzeWatching(true);
    setWatchMode(mode);
    setAnalyzeMsg('Starting server watch…');
    setAnalyzeOk(false);
    setAnalysisRows([]);
    setTradeResultMsg('');
    try {
      const body =
        mode === 'force_model_candles'
          ? { mode: 'force_model_candles', min_probability: forceMinP }
          : { mode: 'standard' };
      const st = await api<WatchStatus>('/signals/watch/start', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      await applyWatchStatus(st, { reloadOnSignal: false });
      pollWatchStatus();
    } catch (e: any) {
      setAnalyzeOk(false);
      setAnalyzeMsg(e.message || 'Failed to start watch');
      setAnalyzeWatching(false);
      setWatchMode(null);
      setAnalyzeBusy(false);
    }
  };

  // Reconnect to server watch after refresh / re-login
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const st = await api<WatchStatus>('/signals/watch/status');
        if (cancelled) return;
        await applyWatchStatus(st, { reloadOnSignal: false });
        if (st.running) pollWatchStatus();
      } catch { /* ignore */ }
    })();
    return () => {
      cancelled = true;
      if (watchPollRef.current) {
        clearInterval(watchPollRef.current);
        watchPollRef.current = null;
      }
    };
  }, [applyWatchStatus, pollWatchStatus]);

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
            onClick={() => analyzeGenerate('standard')}
            disabled={analyzeBusy || !models?.trained}
            title="Keeps checking until full gates pass, then creates signal (and auto-trade if enabled)"
          >
            {watchMode === 'standard' ? <Loader2 size={14} className="spin"/> : <Zap size={14}/>}
            {watchMode === 'standard' ? 'Watching Analyze…' : 'Analyze & Signal'}
          </button>
          <button
            id="btn-force-signal"
            className="btn btn-ghost"
            onClick={() => analyzeGenerate('force_model_candles')}
            disabled={analyzeBusy || !models?.trained}
            title={`Keeps checking until candle confirm + p≥${(forceMinP * 100).toFixed(0)}%, then signal/trade`}
          >
            {watchMode === 'force_model_candles' ? <Loader2 size={14} className="spin"/> : <Zap size={14}/>}
            {watchMode === 'force_model_candles'
              ? 'Watching Force…'
              : `Force (${(forceMinP * 100).toFixed(0)}% + candles)`}
          </button>
          {analyzeWatching && (
            <button id="btn-stop-analyze-watch" className="btn btn-ghost" onClick={stopAnalyzeWatch}>
              <XCircle size={14}/> Stop
            </button>
          )}
          <button id="btn-refresh-signals" className="btn btn-ghost" onClick={load} disabled={analyzeWatching}>
            {loading ? <Loader2 size={14} className="spin"/> : <RefreshCw size={14}/>} Refresh
          </button>
        </div>
      </div>

      {(analyzeMsg || analysisRows.length > 0 || tradeResultMsg || analyzeWatching) && (
        <div className={`analyze-status-box ${analyzeOk ? 'alert-success' : 'alert-warning'}`}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }}/>
          <div>
            <div className="analyze-status-msg" title={analyzeMsg}>{analyzeMsg || (analyzeWatching ? 'Watching…' : '')}</div>
            {tradeResultMsg && (
              <div className="analyze-status-trade text-xs font-mono" title={tradeResultMsg}>{tradeResultMsg}</div>
            )}
            <div className="analyze-grid">
              {(analysisRows.length > 0 ? analysisRows : [
                { direction: 'upper', ok: false, reason: '—' },
                { direction: 'lower', ok: false, reason: '—' },
              ]).slice(0, 2).map(a => (
                <div key={a.direction} className="analyze-row font-mono text-xs" title={
                  a.ok
                    ? `p=${((a.calibrated_probability ?? 0) * 100).toFixed(1)}%`
                    : (a.reason || '')
                }>
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
                      {a.candle_confirm_met
                        ? <span className="text-green"> · candles ✓{a.candle_confirm_score != null ? ` (${a.candle_confirm_score.toFixed(1)})` : ''}</span>
                        : <span className="text-dim"> · no candle confirm</span>}
                      {a.meets_confidence
                        ? <span className="text-green"> · READY</span>
                        : <span className="text-dim"> · below bar</span>}
                      {a.selected_pipeline ? ` · ${a.selected_pipeline}` : ''}
                    </span>
                  )}
                </div>
              ))}
            </div>
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
              Auto alerts need <span className="text-cyan">Edge OK</span> + tick confluence + candle confirm (1m/5m) + EV gates.
              <span className="text-amber"> Analyze & Signal</span> uses the same gates with a confidence floor (default ≥95%).
              <span className="text-amber"> Analyze / Force</span> run on the server (~1s) until gates pass — keeps going
              if you close the tab. Stop cancels. Auto-trade if enabled. Max 3/day Asia/Colombo.
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
  const [dl, setDl]               = useState<DownloadStatus>({ status:'idle', progress:'' });
  const [loadingInfo, setLI]      = useState(true);
  const [guideOpen, setGuideOpen] = useState(true);
  const [symbol, setSymbol]       = useState('R_100');
  const [barrier, setBarrier]     = useState('0.09');
  const [dir, setDir]             = useState('both');
  const [dur, setDur]             = useState('540');
  const [minCal, setMinCal]       = useState('20');
  const [minEdge, setMinEdge]     = useState('15');
  const [autoStrict, setAutoStrict] = useState(true);
  const [autoBusy, setAutoBusy] = useState(false);
  const [autoMsg, setAutoMsg] = useState('');
  const prefsLoaded = useRef(false);
  const pollRef = useRef<ReturnType<typeof setInterval>|null>(null);
  const dlPollRef = useRef<ReturnType<typeof setInterval>|null>(null);

  const persistPrefs = useCallback(async (cal: string, edge: string, auto: boolean) => {
    try {
      await api('/train/prefs', {
        method: 'PUT',
        body: JSON.stringify({
          min_calibration_samples: Number(cal),
          min_edge_selected: Number(edge),
          auto_raise: auto,
        }),
      });
    } catch {}
  }, []);

  const loadInfo = useCallback(async (silent = false) => {
    if (!silent) setLI(true);
    try {
      const d = await api<DataInfo>('/train/data-info');
      setInfo(d);
      if (!prefsLoaded.current && d.train_prefs) {
        prefsLoaded.current = true;
        setMinCal(String(d.train_prefs.min_calibration_samples));
        setMinEdge(String(d.train_prefs.min_edge_selected));
        setAutoStrict(d.train_prefs.auto_raise !== false);
      }
      if (autoStrict && d.train_recommended) {
        const c = String(d.train_recommended.min_calibration_samples);
        const e = String(d.train_recommended.min_edge_selected);
        setMinCal(c);
        setMinEdge(e);
        await persistPrefs(c, e, true);
      }
    } catch {}
    if (!silent) setLI(false);
  }, [autoStrict, persistPrefs]);

  useEffect(() => {
    loadInfo();
    api<TrainStatus>('/train/status').then(setTs).catch(() => {});
    api<DownloadStatus>('/train/download-status').then(setDl).catch(() => {});
  }, [loadInfo]);

  useEffect(() => {
    const t = setInterval(() => { loadInfo(true); }, 10000);
    return () => clearInterval(t);
  }, [loadInfo]);

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await api<TrainStatus>('/train/status');
        setTs(s);
        if (s.status !== 'running') {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          if (s.status === 'done') loadInfo();
        }
      } catch {}
    };
    if (ts.status === 'running' && !pollRef.current) pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current && ts.status !== 'running') {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [ts.status, loadInfo]);

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await api<DownloadStatus>('/train/download-status');
        setDl(s);
        if (s.status !== 'running') {
          clearInterval(dlPollRef.current!);
          dlPollRef.current = null;
          if (s.status === 'done') loadInfo();
        }
      } catch {}
    };
    if (dl.status === 'running' && !dlPollRef.current) dlPollRef.current = setInterval(poll, 2000);
    return () => {
      if (dlPollRef.current && dl.status !== 'running') {
        clearInterval(dlPollRef.current);
        dlPollRef.current = null;
      }
    };
  }, [dl.status, loadInfo]);

  const start = async () => {
    try {
      await persistPrefs(minCal, minEdge, autoStrict);
      await api(`/train/start?${new URLSearchParams({
        symbol,
        barrier_distance: barrier,
        barrier_direction: dir,
        duration_seconds: dur,
        min_calibration_samples: minCal,
        min_edge_selected: minEdge,
        auto_raise: autoStrict ? 'true' : 'false',
      })}`, { method: 'POST' });
      setTs({ status: 'running', progress: `Starting pipeline (cal≥${minCal}, edge≥${minEdge})…` });
    } catch (e: any) {
      setTs({ status: 'error', progress: '', error: e.message });
    }
  };

  const applyRecommended = () => {
    const rec = info?.train_recommended;
    if (!rec) return;
    const c = String(rec.min_calibration_samples);
    const e = String(rec.min_edge_selected);
    setMinCal(c);
    setMinEdge(e);
    void persistPrefs(c, e, autoStrict);
  };

  const waitDownloadDone = async (timeoutMs = 180_000) => {
    const startAt = Date.now();
    while (Date.now() - startAt < timeoutMs) {
      const s = await api<DownloadStatus>('/train/download-status');
      setDl(s);
      if (s.status === 'done' || s.status === 'error' || s.status === 'idle') return s;
      await new Promise(r => setTimeout(r, 2000));
    }
    return await api<DownloadStatus>('/train/download-status');
  };

  const autoBestAll = async () => {
    if (autoBusy || busy) return;
    setAutoBusy(true);
    setAutoMsg('');
    const steps: string[] = [];
    try {
      setAutoMsg('1/4 Refreshing data coverage…');
      const d = await api<DataInfo>('/train/data-info');
      setInfo(d);
      const rec = d.train_recommended ?? { min_calibration_samples: 20, min_edge_selected: 15, label: 'starter', span_hours: 0 };
      const c = String(rec.min_calibration_samples);
      const e = String(rec.min_edge_selected);
      setMinCal(c);
      setMinEdge(e);
      setAutoStrict(true);
      await persistPrefs(c, e, true);
      steps.push(`Train strictness → cal ${c} / edge ${e} (${rec.label})`);

      setAutoMsg('2/4 Saving best live bot gates…');
      // Production-safe gates; only ease live cal sample floor slightly on short data.
      const spanH = Number(rec.span_hours || 0);
      const liveCal = spanH >= 72 ? 50 : spanH >= 48 ? 40 : spanH >= 24 ? 35 : 30;
      await api('/setup/ops-prefs', {
        method: 'PUT',
        body: JSON.stringify({
          max_signals_per_day: 3,
          signal_cooldown_seconds: 540,
          manual_min_confidence: 0.95,
          manual_min_margin_over_breakeven: 0.03,
          min_ev_margin: 0.02,
          min_calibration_samples: liveCal,
          require_touch_confluence: true,
          confluence_min_score: 5,
          confluence_min_gap: 1.5,
          require_candle_confirm: true,
          candle_confirm_min_score: 4,
          candle_confirm_min_gap: 1,
          auto_pause_enabled: true,
          auto_pause_min_resolved: 20,
          auto_pause_ci_margin: 0,
        }),
      });
      steps.push(`Bot gates → Analyze 95%, confluence + candle confirm on, live cal samples ${liveCal}`);

      const sym = d.symbols?.find(s => s.symbol === symbol);
      const sh = sym?.span_hours ?? hoursBetween(sym?.oldest, sym?.newest) ?? 0;
      const needSpan = d.min_span_hours ?? 16;
      if (sh < needSpan * 0.95) {
        setAutoMsg('3/4 Filling history (~24h) because span is still short…');
        try {
          await api(`/train/download?${new URLSearchParams({
            symbol,
            days_back: '1',
            target_ticks: '0',
          })}`, { method: 'POST' });
          setDl({ status: 'running', progress: 'Auto history fill…', ticks_downloaded: 0 });
          const dlDone = await waitDownloadDone();
          steps.push(
            dlDone.status === 'done'
              ? `History fill done (+${(dlDone.ticks_downloaded ?? 0).toLocaleString()} new ticks)`
              : `History fill: ${dlDone.status}${dlDone.error ? ` — ${dlDone.error}` : ''}`
          );
        } catch (e: any) {
          steps.push(`History fill skipped: ${e.message}`);
        }
      } else {
        steps.push(`History fill skipped — span already ${sh.toFixed(1)}h (≥ ${needSpan}h)`);
      }

      await loadInfo(true);
      const d2 = await api<DataInfo>('/train/data-info');
      setInfo(d2);
      const sym2 = d2.symbols?.find(s => s.symbol === symbol);
      const ticks2 = sym2?.tick_count ?? 0;

      if (ticks2 >= 1000) {
        setAutoMsg('4/4 Starting training with best settings…');
        await api(`/train/start?${new URLSearchParams({
          symbol,
          barrier_distance: barrier,
          barrier_direction: dir,
          duration_seconds: dur,
          min_calibration_samples: c,
          min_edge_selected: e,
          auto_raise: 'true',
        })}`, { method: 'POST' });
        setTs({ status: 'running', progress: `Auto-train (cal≥${c}, edge≥${e})…` });
        steps.push('Training started');
      } else {
        steps.push(`Training not started — only ${ticks2} ticks (need ≥1000). Confirm Setup and wait.`);
      }

      setAutoMsg(`Auto best complete:\n• ${steps.join('\n• ')}`);
    } catch (e: any) {
      setAutoMsg(`Auto best failed: ${e.message}${steps.length ? `\n• ${steps.join('\n• ')}` : ''}`);
    }
    setAutoBusy(false);
  };

  const startDownload = async () => {
    try {
      await api(`/train/download?${new URLSearchParams({
        symbol,
        days_back: '1',
        target_ticks: '0',
      })}`, { method: 'POST' });
      setDl({ status: 'running', progress: 'Queued history download…', ticks_downloaded: 0 });
    } catch (e: any) {
      setDl({ status: 'error', progress: '', error: e.message });
    }
  };

  const durSec = Math.max(60, parseInt(dur, 10) || 540);
  const minLabels = info?.min_labels_required ?? 100;
  const minSpanHours = info?.min_span_hours ?? ((minLabels * durSec + 600 + durSec) / 3600);
  const symInfo = info?.symbols?.find(s => s.symbol === symbol);
  const tickCount = symInfo?.tick_count ?? 0;
  const spanHours = symInfo?.span_hours ?? hoursBetween(symInfo?.oldest, symInfo?.newest) ?? 0;
  const estLabels = symInfo?.est_labels ?? (
    spanHours > 0 ? Math.max(0, Math.floor(((spanHours * 3600) - 600 - durSec) / durSec)) : 0
  );
  const readyByApi = Boolean(symInfo?.ready_to_train);
  const readyByLocal = tickCount >= 1000 && estLabels >= minLabels && spanHours >= minSpanHours * 0.95;
  const dataReady = readyByApi || readyByLocal;
  const busy = ts.status === 'running' || dl.status === 'running';
  const canTrain = tickCount >= 1000 && !busy;
  const spanPct = Math.min(100, Math.round((spanHours / Math.max(minSpanHours, 0.1)) * 100));
  const labelPct = Math.min(100, Math.round((estLabels / minLabels) * 100));
  const errGuide = explainTrainError(ts.error);
  const durHours = (durSec / 60).toFixed(durSec % 60 === 0 ? 0 : 1);

  return (
    <>
      <div className="glass page-header">
        <div>
          <h1 className="page-title">Train ML Model</h1>
          <p className="page-subtitle">
            Everything here runs in the browser — fill history, check readiness, train, and change strictness.
            After deploy you do <strong>not</strong> need to edit .env on the VPS for training options.
          </p>
        </div>
      </div>

      <div className="alert alert-success">
        <CheckCircle size={14}/>
        <div>
          <strong>UI owns training settings.</strong> Calibration / edge levels are saved on the server from this page.
          .env values are first-boot defaults only. When more data arrives: pick a higher level (or Auto-raise) → Train.
        </div>
      </div>

      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Zap size={16}/>One-click best setup</h3>
        </div>
        <p className="text-xs text-dim mb-3">
          Automatically picks the best train strictness for your current span, saves safe live bot gates,
          fills history if span is short, then starts training. No VPS / .env edits.
        </p>
        {autoMsg && (
          <div className={`alert ${autoMsg.startsWith('Auto best failed') ? 'alert-error' : 'alert-info'} mb-3`}>
            <Info size={14}/>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: '0.82rem' }}>{autoMsg}</pre>
          </div>
        )}
        <button
          id="btn-auto-best"
          className="btn btn-primary w-full"
          style={{ justifyContent: 'center', padding: '0.9rem' }}
          onClick={autoBestAll}
          disabled={autoBusy || busy}
        >
          {autoBusy
            ? <><Loader2 size={16} className="spin"/>Auto-setting data &amp; training…</>
            : <><Zap size={16}/>Auto best: data + settings + train</>}
        </button>
      </div>

      {/* Full guide */}
      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Info size={16}/>How training works (read this)</h3>
          <button className="btn btn-ghost" onClick={() => setGuideOpen(v => !v)}>
            {guideOpen ? 'Hide' : 'Show'} guide
          </button>
        </div>
        {guideOpen && (
          <div className="train-guide">
            <ol className="train-guide-list">
              <li>
                <strong>Live ticks first.</strong> Confirm Setup (same symbol, barrier, duration).
                The worker saves ticks automatically while the stack is running.
              </li>
              <li>
                <strong>Time span matters more than tick count.</strong> The model creates
                <em> one labeled example every {durHours} minutes</em> (non-overlapping).
                You need ≥{minLabels} labels ≈ <strong>~{minSpanHours.toFixed(0)} hours</strong> continuous coverage.
                5,000 ticks in 1 hour is still not enough.
              </li>
              <li>
                <strong>Optional history fill.</strong> Use “Fill history (~24h)” to pull public Deriv history
                into the DB. If download says <code>new_saved=0</code>, those ticks are already stored — that is OK.
              </li>
              <li>
                <strong>When you change barrier / duration / direction.</strong> Train again with the
                <em> same values as Setup</em>. A model trained on 0.09 / 540s will not match Setup 0.20 / 9m
                (“No compatible model”). Changing duration also changes how many labels fit in the same span.
              </li>
              <li>
                <strong>What training does.</strong> Labels → features → chronological split
                (train / validate / calibrate / test) → purge gaps → train models → calibrate → final test.
                Alerts stay off unless the model shows held-out edge and live gates pass (≥95% Analyze, confluence, EV).
              </li>
              <li>
                <strong>Common errors.</strong> “Insufficient resolved labeled data” = span too short.
                “Insufficient calibration samples” = lower <em>Min calibration samples</em> below
                (or wait for more days). When you have 2–3+ days, raise calibration/edge (or leave
                “Auto-raise” on). No VPS needed.
              </li>
            </ol>
            <div className="train-guide-grid">
              <div className="train-guide-card">
                <div className="text-xs text-dim uppercase tracking-wide">Minimum to train</div>
                <div className="font-mono text-sm mt-1">~{minSpanHours.toFixed(0)}h span · ≥{minLabels} labels · ≥1,000 ticks</div>
              </div>
              <div className="train-guide-card">
                <div className="text-xs text-dim uppercase tracking-wide">Comfortable</div>
                <div className="font-mono text-sm mt-1">1 full day (~24h) · 25k–45k ticks on R_100</div>
              </div>
              <div className="train-guide-card">
                <div className="text-xs text-dim uppercase tracking-wide">Stronger later</div>
                <div className="font-mono text-sm mt-1">2–3+ days continuous · better calibration & edge</div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Data readiness */}
      <div className="glass">
        <div className="section-header">
          <h3 className="section-title"><Database size={16}/>Training data readiness</h3>
          <button id="btn-refresh-data" className="btn btn-ghost" onClick={() => loadInfo()}>
            {loadingInfo ? <Loader2 size={13} className="spin"/> : <RefreshCw size={13}/>} Refresh
          </button>
        </div>

        {loadingInfo && (
          <div className="flex items-center gap-2 text-dim text-sm">
            <Loader2 size={14} className="spin"/>Checking database…
          </div>
        )}

        {!loadingInfo && info && !info.has_data && (
          <div className="alert alert-warning">
            <AlertTriangle size={14}/>
            {info.message ?? 'No tick data yet. Confirm Setup, then wait or fill history below.'}
          </div>
        )}

        {!loadingInfo && info?.has_data && (
          <>
            {(info.symbols ?? []).map(sym => {
              const sh = sym.span_hours ?? hoursBetween(sym.oldest, sym.newest) ?? 0;
              const el = sym.est_labels ?? Math.max(0, Math.floor(((sh * 3600) - 600 - durSec) / durSec));
              const ready = sym.symbol === symbol ? dataReady : (sym.ready_to_train || (el >= minLabels && sh >= minSpanHours * 0.95));
              return (
                <div key={sym.symbol} className="data-row">
                  <div>
                    <div className="text-sm font-medium text-primary">{sym.symbol}</div>
                    <div className="text-xs text-dim mt-1 font-mono">
                      {sym.oldest?.slice(0, 16).replace('T', ' ')} → {sym.newest?.slice(0, 16).replace('T', ' ')}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <div className="font-mono font-semibold text-sm">{sym.tick_count.toLocaleString()} ticks</div>
                      <div className="text-xs text-dim font-mono">
                        {sh.toFixed(1)}h span · ~{el} labels
                      </div>
                    </div>
                    <span className={`badge ${ready ? 'badge-green' : 'badge-amber'}`}>
                      {ready ? '✓ Ready to train' : `Need ~${minSpanHours.toFixed(0)}h span`}
                    </span>
                  </div>
                </div>
              );
            })}

            {symInfo && (
              <div className="train-meters mt-3">
                <div className="train-meter">
                  <div className="train-meter-head">
                    <span>Time span</span>
                    <span className="font-mono">{spanHours.toFixed(1)}h / {minSpanHours.toFixed(0)}h</span>
                  </div>
                  <div className="feat-bar-track"><div className="feat-bar-fill" style={{ width: `${spanPct}%` }}/></div>
                </div>
                <div className="train-meter">
                  <div className="train-meter-head">
                    <span>Est. labels</span>
                    <span className="font-mono">~{estLabels} / {minLabels}</span>
                  </div>
                  <div className="feat-bar-track"><div className="feat-bar-fill" style={{ width: `${labelPct}%` }}/></div>
                </div>
              </div>
            )}

            <div className={`alert ${dataReady ? 'alert-success' : 'alert-warning'} mt-3`}>
              {dataReady
                ? <><CheckCircle size={14}/>Coverage looks enough for training with current settings. You can train below.</>
                : <><AlertTriangle size={14}/>Not ready yet — keep collecting or fill history. Selected symbol has {spanHours.toFixed(1)}h span and ~{estLabels} labels (need ~{minSpanHours.toFixed(0)}h / {minLabels}).</>}
            </div>
          </>
        )}

        <div className="divider"/>
        <div className="section-header mb-2">
          <h3 className="section-title" style={{ fontSize: '0.95rem' }}>Fill history from Deriv (optional)</h3>
        </div>
        <p className="text-xs text-dim mb-3">
          Pulls the public ~24h tick history into your DB. Safe to run even if ticks already exist
          (duplicates are skipped). No VPS commands needed.
        </p>
        {dl.status === 'running' && (
          <div className="train-progress"><Loader2 size={16} className="spin"/>{dl.progress || 'Downloading…'}</div>
        )}
        {dl.status === 'error' && (
          <div className="alert alert-error"><XCircle size={14}/>{dl.error}</div>
        )}
        {dl.status === 'done' && (
          <div className="alert alert-success">
            <CheckCircle size={14}/>
            History fill finished · {(dl.ticks_downloaded ?? 0).toLocaleString()} new ticks saved.
            If that number is low, the DB already had most of the public history.
          </div>
        )}
        <button
          id="btn-fill-history"
          className="btn btn-ghost w-full"
          style={{ justifyContent: 'center' }}
          onClick={startDownload}
          disabled={busy}
        >
          {dl.status === 'running'
            ? <><Loader2 size={16} className="spin"/>Filling history…</>
            : <><Database size={16}/>Fill history (~24h) for {symbol}</>}
        </button>
      </div>

      {/* Config + start */}
      <div className="glass">
        <div className="section-header mb-4">
          <h3 className="section-title"><Brain size={16}/>Training configuration</h3>
        </div>
        <div className="alert alert-info">
          <Info size={14}/>
          <div>
            These must match <strong>Setup</strong>. If you change barrier or duration here, change Setup the same way
            before Analyze / live alerts, then retrain.
          </div>
        </div>
        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Symbol</label>
            <select id="train-symbol" className="form-select" value={symbol} onChange={e => setSymbol(e.target.value)}>
              <option value="R_100">Volatility 100 Index (R_100)</option>
              <option value="1HZ100V">Volatility 100 (1s) Index (1HZ100V)</option>
            </select>
            <div className="form-hint">Must match the symbol the worker is collecting.</div>
          </div>
          <div className="form-group">
            <label className="form-label">Direction</label>
            <select id="train-direction" className="form-select" value={dir} onChange={e => setDir(e.target.value)}>
              <option value="both">Both (Upper + Lower)</option>
              <option value="upper">Upper Touch (+barrier)</option>
              <option value="lower">Lower Touch (−barrier)</option>
            </select>
            <div className="form-hint">“Both” trains two models (upper then lower) in one job.</div>
          </div>
          <div className="form-group">
            <label className="form-label">Barrier Distance (price points)</label>
            <input id="train-barrier" type="number" step="0.01" className="form-input" value={barrier} onChange={e => setBarrier(e.target.value)}/>
            <div className="form-hint">
              Relative offset from spot (e.g. 0.09), not percent. Changing this means old models won’t match — retrain required.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Duration (seconds)</label>
            <input id="train-duration" type="number" className="form-input" value={dur} onChange={e => setDur(e.target.value)}/>
            <div className="form-hint">
              Default 540 = 9 minutes. Longer duration → fewer labels in the same span (harder to reach {minLabels}).
            </div>
          </div>
        </div>

        <div className="divider"/>
        <div className="section-header mb-2">
          <h3 className="section-title" style={{ fontSize: '0.95rem' }}>Strictness (raise when you have more data)</h3>
        </div>
        <div className="alert alert-info">
          <Info size={14}/>
          <div>
            Pick a level that matches your <strong>time span</strong> (not only tick count).
            Changing these does <em>not</em> retrain by itself — click Train after you change them.
            <div className="mt-1 text-xs">
              <strong>Calibration</strong> = min held-out samples to trust probability scores (too high + short data → train fails).{' '}
              <strong>Edge</strong> = min selected test signals to claim “demonstrated edge” (model can still save if edge fails).
            </div>
            {info?.train_recommended && (
              <div className="mt-1 text-xs">
                Suggested for your {Number(info.train_recommended.span_hours).toFixed(1)}h span:{' '}
                <span className="font-mono">cal={info.train_recommended.min_calibration_samples}</span>,{' '}
                <span className="font-mono">edge={info.train_recommended.min_edge_selected}</span>
                {' '}({info.train_recommended.label})
              </div>
            )}
          </div>
        </div>

        <div className="strict-levels mb-3">
          {STRICTNESS_LEVELS.map(level => {
            const selected = Number(minCal) === level.cal && Number(minEdge) === level.edge;
            const spanOk = spanHours >= level.minHours * 0.95;
            const labelsOk = estLabels >= level.minLabels;
            const dataOk = spanOk && labelsOk && tickCount >= 1000;
            return (
              <button
                key={level.id}
                type="button"
                className={`strict-level-card ${selected ? 'is-selected' : ''} ${dataOk ? 'is-ready' : 'is-short'}`}
                onClick={() => {
                  setMinCal(String(level.cal));
                  setMinEdge(String(level.edge));
                  setAutoStrict(false);
                  void persistPrefs(String(level.cal), String(level.edge), false);
                }}
              >
                <div className="strict-level-top">
                  <span className="strict-level-title">{level.title}</span>
                  <span className={`badge ${dataOk ? 'badge-green' : 'badge-amber'}`}>
                    {dataOk ? 'Your data OK' : 'Need more data'}
                  </span>
                </div>
                <div className="font-mono text-xs text-dim mt-1">
                  cal≥{level.cal} · edge≥{level.edge}
                </div>
                <div className="text-xs mt-2">
                  <strong>Min data:</strong> ~{level.minHours}h span · ≥{level.minLabels} labels
                </div>
                <div className="text-xs text-dim mt-1">You have: {spanHours.toFixed(1)}h · ~{estLabels} labels</div>
                <div className="text-xs text-dim mt-1">{level.when}</div>
              </button>
            );
          })}
        </div>

        {(() => {
          const level = matchStrictnessLevel(Number(minCal), Number(minEdge));
          const needH = level?.minHours ?? Math.max(16, Math.ceil(Number(minCal) * 0.8));
          const needL = level?.minLabels ?? Math.max(100, Number(minCal) * 5);
          const ok = spanHours >= needH * 0.95 && estLabels >= needL && tickCount >= 1000;
          return (
            <div className={`alert ${ok ? 'alert-success' : 'alert-warning'} mb-3`}>
              {ok ? <CheckCircle size={14}/> : <AlertTriangle size={14}/>}
              <div>
                <div className="font-medium">
                  {level ? `${level.title} selected` : `Custom (cal={minCal}, edge={minEdge})`}
                  {ok ? ' — your current data meets the minimum for this level.' : ' — your current data is below this level’s minimum.'}
                </div>
                <div className="text-xs mt-1">
                  <strong>Minimum for this choice:</strong> ~{needH}h continuous span and ≥{needL} labels
                  (you have {spanHours.toFixed(1)}h / ~{estLabels} labels).
                </div>
                {level && (
                  <>
                    <div className="text-xs mt-2"><strong>What happens if you train with this:</strong></div>
                    <ul className="train-guide-list" style={{ marginTop: 4, marginBottom: 0 }}>
                      {level.happens.map((h, i) => <li key={i}>{h}</li>)}
                    </ul>
                    <div className="text-xs mt-2"><strong>Trade-off:</strong> {level.risk}</div>
                  </>
                )}
                {!ok && (
                  <div className="text-xs mt-2">
                    Wait for more live ticks, use Fill history, or pick a lower level (Starter) until the green “Your data OK” badge appears.
                  </div>
                )}
              </div>
            </div>
          );
        })()}

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Min calibration samples</label>
            <select
              id="train-min-cal"
              className="form-select"
              value={minCal}
              onChange={e => {
                const v = e.target.value;
                setMinCal(v);
                setAutoStrict(false);
                void persistPrefs(v, minEdge, false);
              }}
            >
              <option value="20">20 — starter (~16–24h)</option>
              <option value="30">30 — balanced (~24h+)</option>
              <option value="40">40 — medium-strict (~48h+)</option>
              <option value="50">50 — strict (~72h+)</option>
            </select>
            <div className="form-hint">
              Raise this when you have more days. If train fails “Insufficient calibration samples”, lower it or wait.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Min edge selected signals</label>
            <select
              id="train-min-edge"
              className="form-select"
              value={minEdge}
              onChange={e => {
                const v = e.target.value;
                setMinEdge(v);
                setAutoStrict(false);
                void persistPrefs(minCal, v, false);
              }}
            >
              <option value="15">15 — starter</option>
              <option value="20">20 — balanced</option>
              <option value="25">25 — medium-strict</option>
              <option value="30">30 — strict</option>
            </select>
            <div className="form-hint">
              Higher = harder to unlock “demonstrated edge”. Does not block saving the model.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 mb-3" style={{ flexWrap: 'wrap' }}>
          <label className="text-sm text-secondary flex items-center gap-2" style={{ cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoStrict}
              onChange={e => {
                const on = e.target.checked;
                setAutoStrict(on);
                if (on) applyRecommended();
                else void persistPrefs(minCal, minEdge, false);
              }}
            />
            Auto-raise level as more data arrives
          </label>
          <button type="button" className="btn btn-ghost" onClick={applyRecommended} disabled={!info?.train_recommended}>
            Use suggested for current data
          </button>
        </div>

        {ts.status === 'running' && (
          <div className="train-progress"><Loader2 size={16} className="spin"/>{ts.progress || 'Training…'}</div>
        )}
        {ts.status === 'error' && (
          <>
            <div className="alert alert-error"><XCircle size={14}/><div><div className="font-medium">{errGuide?.title ?? 'Training error'}</div><div className="text-xs mt-1" style={{ opacity: 0.9 }}>{ts.error}</div></div></div>
            {errGuide && (
              <div className="alert alert-info">
                <Info size={14}/>
                <div>
                  <div className="font-medium mb-1">What to do</div>
                  <ol className="train-guide-list" style={{ margin: 0 }}>
                    {errGuide.steps.map((s, i) => <li key={i}>{s}</li>)}
                  </ol>
                </div>
              </div>
            )}
          </>
        )}
        {ts.status === 'done' && ts.result && (
          <div className="alert alert-success">
            <CheckCircle size={14}/>
            Model saved · {ts.result.timestamp?.slice(0, 15).replace('T', ' ')}
            {ts.result.has_demonstrated_edge
              ? ' · held-out edge found'
              : ' · no demonstrated edge yet (alerts stay cautious)'}
          </div>
        )}

        {!dataReady && canTrain && (
          <div className="alert alert-warning">
            <AlertTriangle size={14}/>
            You can still start training, but it will likely fail until span/labels reach the bars above.
            Prefer waiting or filling history first.
          </div>
        )}

        <button
          id="btn-start-training"
          className="btn btn-primary w-full"
          style={{ marginTop: '0.25rem', justifyContent: 'center', padding: '0.85rem' }}
          onClick={start}
          disabled={!canTrain}
        >
          {ts.status === 'running'
            ? <><Loader2 size={16} className="spin"/>Training in progress…</>
            : <><Brain size={16}/>Train on {tickCount.toLocaleString()} DB ticks ({spanHours.toFixed(1)}h)</>}
        </button>
        {!canTrain && ts.status !== 'running' && (
          <p className="text-xs text-dim mt-2" style={{ textAlign: 'center' }}>
            {busy
              ? 'Wait for the current download/training job to finish.'
              : `Need ≥ 1,000 ticks for “${symbol}” · currently ${tickCount.toLocaleString()}. Confirm Setup or fill history.`}
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
type OpsPrefs = {
  max_signals_per_day: number;
  signal_cooldown_seconds: number;
  manual_min_confidence: number;
  manual_min_margin_over_breakeven: number;
  min_ev_margin: number;
  min_calibration_samples: number;
  require_touch_confluence: boolean;
  confluence_min_score: number;
  confluence_min_gap: number;
  require_candle_confirm: boolean;
  candle_confirm_min_score: number;
  candle_confirm_min_gap: number;
  auto_pause_enabled: boolean;
  auto_pause_min_resolved: number;
  auto_pause_ci_margin: number;
  guide?: Record<string, string>;
};

const DEFAULT_OPS: OpsPrefs = {
  max_signals_per_day: 3,
  signal_cooldown_seconds: 540,
  manual_min_confidence: 0.95,
  manual_min_margin_over_breakeven: 0.03,
  min_ev_margin: 0.02,
  min_calibration_samples: 50,
  require_touch_confluence: true,
  confluence_min_score: 5,
  confluence_min_gap: 1.5,
  require_candle_confirm: true,
  candle_confirm_min_score: 4,
  candle_confirm_min_gap: 1,
  auto_pause_enabled: true,
  auto_pause_min_resolved: 20,
  auto_pause_ci_margin: 0,
};

type TradePrefs = {
  auto_trade_enabled: boolean;
  trade_stake: number;
  force_min_probability: number;
  trade_currency: string;
  token_configured?: boolean;
  token_mask?: string | null;
};

type TradeAccount = {
  ok?: boolean;
  error?: string;
  loginid?: string;
  currency?: string;
  balance?: number;
  is_virtual?: boolean;
  account_type?: string;
  email?: string;
  fullname?: string;
  today_profit?: number;
  recent_profit?: number;
  recent_trades?: number;
  recent_wins?: number;
  recent_losses?: number;
  token_configured?: boolean;
};

const DEFAULT_TRADE: TradePrefs = {
  auto_trade_enabled: false,
  trade_stake: 1,
  force_min_probability: 0.8,
  trade_currency: 'USD',
  token_configured: false,
  token_mask: null,
};

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
  const [ops, setOps]       = useState<OpsPrefs>(DEFAULT_OPS);
  const [opsGuide, setOpsGuide] = useState<Record<string, string>>({});
  const [opsSaving, setOpsSaving] = useState(false);
  const [opsMsg, setOpsMsg] = useState('');
  const [opsOk, setOpsOk]   = useState(false);
  const [trade, setTrade]   = useState<TradePrefs>(DEFAULT_TRADE);
  const [tradeToken, setTradeToken] = useState('');
  const [tradeSaving, setTradeSaving] = useState(false);
  const [tradeMsg, setTradeMsg] = useState('');
  const [tradeOk, setTradeOk] = useState(false);
  const [tradeAccount, setTradeAccount] = useState<TradeAccount | null>(null);
  const [accountLoading, setAccountLoading] = useState(false);

  const loadTradeAccount = async (refresh = true) => {
    setAccountLoading(true);
    try {
      const a = await api<TradeAccount>(`/setup/trade-account?refresh=${refresh ? 'true' : 'false'}`);
      setTradeAccount(a);
      if (a.ok && a.currency) {
        setTrade(prev => ({ ...prev, trade_currency: a.currency || prev.trade_currency }));
      }
    } catch {
      setTradeAccount(null);
    }
    setAccountLoading(false);
  };

  useEffect(() => {
    api<OpsPrefs & { guide?: Record<string, string> }>('/setup/ops-prefs')
      .then(r => {
        setOps({
          max_signals_per_day: r.max_signals_per_day,
          signal_cooldown_seconds: r.signal_cooldown_seconds,
          manual_min_confidence: r.manual_min_confidence,
          manual_min_margin_over_breakeven: r.manual_min_margin_over_breakeven,
          min_ev_margin: r.min_ev_margin,
          min_calibration_samples: r.min_calibration_samples,
          require_touch_confluence: r.require_touch_confluence,
          confluence_min_score: r.confluence_min_score,
          confluence_min_gap: r.confluence_min_gap,
          require_candle_confirm: r.require_candle_confirm !== false,
          candle_confirm_min_score: r.candle_confirm_min_score ?? 4,
          candle_confirm_min_gap: r.candle_confirm_min_gap ?? 1,
          auto_pause_enabled: r.auto_pause_enabled,
          auto_pause_min_resolved: r.auto_pause_min_resolved,
          auto_pause_ci_margin: r.auto_pause_ci_margin,
        });
        if (r.guide) setOpsGuide(r.guide);
      })
      .catch(() => {});
    api<TradePrefs>('/setup/trade-prefs')
      .then(r => {
        setTrade({
          auto_trade_enabled: !!r.auto_trade_enabled,
          trade_stake: Number(r.trade_stake ?? 1),
          force_min_probability: Number(r.force_min_probability ?? 0.8),
          trade_currency: r.trade_currency || 'USD',
          token_configured: !!r.token_configured,
          token_mask: r.token_mask ?? null,
        });
        if (r.token_configured) loadTradeAccount(true);
      })
      .catch(() => {});
  }, []);

  const setOpsField = <K extends keyof OpsPrefs>(key: K, value: OpsPrefs[K]) => {
    setOps(prev => ({ ...prev, [key]: value }));
  };

  const saveOps = async () => {
    setOpsSaving(true); setOpsMsg('');
    try {
      await api('/setup/ops-prefs', { method: 'PUT', body: JSON.stringify(ops) });
      setOpsOk(true);
      setOpsMsg('✓ Bot gates saved. No .env / VPS edit needed — takes effect on the next signal check.');
    } catch (e: any) {
      setOpsOk(false);
      setOpsMsg(`✗ ${e.message}`);
    }
    setOpsSaving(false);
  };

  const saveTradePrefs = async () => {
    setTradeSaving(true); setTradeMsg('');
    try {
      const r = await api<TradePrefs>('/setup/trade-prefs', {
        method: 'PUT',
        body: JSON.stringify({
          auto_trade_enabled: trade.auto_trade_enabled,
          trade_stake: trade.trade_stake,
          force_min_probability: trade.force_min_probability,
          trade_currency: trade.trade_currency,
        }),
      });
      setTrade(prev => ({
        ...prev,
        auto_trade_enabled: !!r.auto_trade_enabled,
        trade_stake: Number(r.trade_stake ?? prev.trade_stake),
        force_min_probability: Number(r.force_min_probability ?? prev.force_min_probability),
        trade_currency: r.trade_currency || prev.trade_currency,
        token_configured: r.token_configured ?? prev.token_configured,
        token_mask: r.token_mask ?? prev.token_mask,
      }));
      setTradeOk(true);
      setTradeMsg('✓ Trade prefs saved.');
    } catch (e: any) {
      setTradeOk(false);
      setTradeMsg(`✗ ${e.message}`);
    }
    setTradeSaving(false);
  };

  const saveTradeToken = async () => {
    setTradeSaving(true); setTradeMsg('');
    try {
      const r = await api<{
        token_configured?: boolean;
        token_mask?: string;
        account?: TradeAccount;
      }>('/setup/trade-token', {
        method: 'PUT',
        body: JSON.stringify({ token: tradeToken }),
      });
      setTrade(prev => ({
        ...prev,
        token_configured: !!r.token_configured,
        token_mask: r.token_mask ?? null,
        trade_currency: r.account?.currency || prev.trade_currency,
      }));
      if (r.account) setTradeAccount(r.account);
      setTradeToken('');
      setTradeOk(true);
      if (r.account?.ok) {
        setTradeMsg(
          `✓ Token saved · ${r.account.account_type?.toUpperCase()} ${r.account.loginid} · ` +
          `balance ${r.account.balance} ${r.account.currency}`
        );
      } else {
        setTradeMsg(`✓ Token saved${r.account?.error ? ` · account lookup: ${r.account.error}` : ''}`);
      }
    } catch (e: any) {
      setTradeOk(false);
      setTradeMsg(`✗ ${e.message}`);
    }
    setTradeSaving(false);
  };

  const clearTradeToken = async () => {
    setTradeSaving(true); setTradeMsg('');
    try {
      await api('/setup/trade-token', { method: 'DELETE' });
      setTrade(prev => ({ ...prev, token_configured: false, token_mask: null, auto_trade_enabled: false }));
      setTradeAccount(null);
      setTradeOk(true);
      setTradeMsg('✓ Token cleared. Auto-trade should stay off until you set a new token.');
    } catch (e: any) {
      setTradeOk(false);
      setTradeMsg(`✗ ${e.message}`);
    }
    setTradeSaving(false);
  };

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
    <div style={{maxWidth: 760}}>
      <div className="glass page-header" style={{marginBottom:'1.5rem'}}>
        <div>
          <h1 className="page-title">Configuration</h1>
          <p className="page-subtitle">
            Contract setup + day-to-day bot gates. After deploy, change gates here — not in .env.prod.
          </p>
        </div>
      </div>

      <div className="alert alert-info" style={{ marginBottom: '1.25rem' }}>
        <Info size={14}/>
        <div>
          <strong>What stays in .env (deploy only):</strong> DB password, SECRET_KEY, URLs, HTTPS port, CORS.
          <br/>
          <strong>What you change in UI:</strong> signals/day, cooldown, Analyze 95% gate, EV, confluence, auto-pause,
          and Train strictness (on Train tab).
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
          <div className="warn-title"><AlertTriangle size={14}/>Signal-first · optional auto-trade</div>
          <p className="warn-body">
            By default this bot only generates signals. Auto-trade is <strong>off</strong> until you enable it below
            with a Deriv API token (trade scope). Demo and real accounts are both allowed — real money risk is yours.
          </p>
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

      <div className="glass" style={{ marginTop: '1.25rem' }}>
        <div className="section-header">
          <h3 className="section-title"><ShieldCheck size={16}/>Bot gates (replaces .env day-to-day knobs)</h3>
        </div>
        <p className="text-xs text-dim mb-3">
          These used to live only in <span className="font-mono">.env.prod</span>. Now the UI owns them after deploy.
          Lower confidence / EV / confluence = more signals (looser). Higher = stricter / fewer signals.
        </p>

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Max signals / day</label>
            <input type="number" className="form-input" min={0} max={10} value={ops.max_signals_per_day}
              onChange={e => setOpsField('max_signals_per_day', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.max_signals_per_day || 'Default 3 (Asia/Colombo day).'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Cooldown cooldown (seconds)</label>
            <input type="number" className="form-input" min={0} max={7200} value={ops.signal_cooldown_seconds}
              onChange={e => setOpsField('signal_cooldown_seconds', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.signal_cooldown_seconds || 'Default 540 = 9 minutes.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Analyze min confidence</label>
            <input type="number" step="0.01" min={0.5} max={0.99} className="form-input" value={ops.manual_min_confidence}
              onChange={e => setOpsField('manual_min_confidence', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.manual_min_confidence || '0.95 = need 95% calibrated probability.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Analyze margin over breakeven</label>
            <input type="number" step="0.01" min={0} max={0.5} className="form-input" value={ops.manual_min_margin_over_breakeven}
              onChange={e => setOpsField('manual_min_margin_over_breakeven', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.manual_min_margin_over_breakeven || 'Extra edge vs quote breakeven for Analyze.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Live EV margin</label>
            <input type="number" step="0.01" min={0} max={0.5} className="form-input" value={ops.min_ev_margin}
              onChange={e => setOpsField('min_ev_margin', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.min_ev_margin || 'Auto-alert EV gate.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Live calibration samples</label>
            <input type="number" min={5} max={200} className="form-input" value={ops.min_calibration_samples}
              onChange={e => setOpsField('min_calibration_samples', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.min_calibration_samples || 'Nearby cal samples needed for live EV.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Confluence min score</label>
            <input type="number" step="0.1" min={0} max={50} className="form-input" value={ops.confluence_min_score}
              onChange={e => setOpsField('confluence_min_score', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.confluence_min_score || 'Higher = harder for confluence to fire.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Confluence min gap</label>
            <input type="number" step="0.1" min={0} max={20} className="form-input" value={ops.confluence_min_gap}
              onChange={e => setOpsField('confluence_min_gap', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.confluence_min_gap || 'Gap between upper vs lower scores.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Candle confirm min score</label>
            <input type="number" step="0.1" min={0} max={20} className="form-input" value={ops.candle_confirm_min_score}
              onChange={e => setOpsField('candle_confirm_min_score', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.candle_confirm_min_score || '1m/5m/15m + patterns (engulfing, pin, soldiers…) default 4.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Candle confirm min gap</label>
            <input type="number" step="0.1" min={0} max={10} className="form-input" value={ops.candle_confirm_min_gap}
              onChange={e => setOpsField('candle_confirm_min_gap', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.candle_confirm_min_gap || 'Score gap vs opposite candle direction.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Auto-pause min resolved</label>
            <input type="number" min={5} max={200} className="form-input" value={ops.auto_pause_min_resolved}
              onChange={e => setOpsField('auto_pause_min_resolved', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.auto_pause_min_resolved || 'Resolved live signals before pause can fire.'}</div>
          </div>
          <div className="form-group">
            <label className="form-label">Auto-pause CI margin</label>
            <input type="number" step="0.01" min={-0.1} max={0.1} className="form-input" value={ops.auto_pause_ci_margin}
              onChange={e => setOpsField('auto_pause_ci_margin', Number(e.target.value))}/>
            <div className="form-hint">{opsGuide.auto_pause_ci_margin || 'Usually 0.0.'}</div>
          </div>
        </div>

        <div className="flex items-center gap-4 mb-3" style={{ flexWrap: 'wrap' }}>
          <label className="text-sm flex items-center gap-2" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={ops.require_touch_confluence}
              onChange={e => setOpsField('require_touch_confluence', e.target.checked)}/>
            Require touch confluence
          </label>
          <label className="text-sm flex items-center gap-2" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={ops.require_candle_confirm}
              onChange={e => setOpsField('require_candle_confirm', e.target.checked)}/>
            Require candle confirm (1m/5m)
          </label>
          <label className="text-sm flex items-center gap-2" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={ops.auto_pause_enabled}
              onChange={e => setOpsField('auto_pause_enabled', e.target.checked)}/>
            Auto-pause on live underperformance
          </label>
        </div>

        {opsMsg && (
          <div className={`alert ${opsOk ? 'alert-success' : 'alert-error'}`}>
            {opsOk ? <CheckCircle size={14}/> : <XCircle size={14}/>}
            {opsMsg}
          </div>
        )}

        <button
          id="btn-save-ops"
          className="btn btn-primary w-full"
          style={{ justifyContent: 'center' }}
          onClick={saveOps}
          disabled={opsSaving || !online}
        >
          {opsSaving ? <Loader2 size={14} className="spin"/> : <ShieldCheck size={14}/>}
          {opsSaving ? 'Saving gates…' : 'Save bot gates'}
        </button>
      </div>

      <div className="glass" style={{ marginTop: '1.25rem' }}>
        <div className="section-header">
          <h3 className="section-title"><AlertTriangle size={16}/>Auto-trade (optional)</h3>
          {trade.token_configured
            ? <span className="badge badge-green">Token {trade.token_mask || 'set'}</span>
            : <span className="badge badge-dim">No token</span>}
        </div>
        <div className="alert alert-warning" style={{ marginBottom: '1rem' }}>
          <AlertTriangle size={14}/>
          <div>
            Enabling auto-trade can place <strong>real or demo</strong> One-Touch buys after Analyze / Force.
            Use a token with trade scope. Stake is taken from the field below — not from quote display price.
          </div>
        </div>

        <label className="text-sm flex items-center gap-2 mb-3" style={{ cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={trade.auto_trade_enabled}
            onChange={e => setTrade(prev => ({ ...prev, auto_trade_enabled: e.target.checked }))}
          />
          Enable auto-trade after Analyze / Force signal
        </label>

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Stake</label>
            <input
              type="number"
              step="0.01"
              min={0.35}
              className="form-input"
              value={trade.trade_stake}
              onChange={e => setTrade(prev => ({ ...prev, trade_stake: Number(e.target.value) }))}
            />
            <div className="form-hint">Amount sent in proposal/buy (account currency).</div>
          </div>
          <div className="form-group">
            <label className="form-label">Force min probability</label>
            <select
              className="form-select"
              value={String(trade.force_min_probability)}
              onChange={e => setTrade(prev => ({ ...prev, force_min_probability: Number(e.target.value) }))}
            >
              <option value="0.8">80%</option>
              <option value="0.85">85%</option>
              <option value="0.9">90%</option>
              <option value="0.95">95%</option>
            </select>
            <div className="form-hint">Force watch: that direction needs candle confirm + this min p.</div>
          </div>
          <div className="form-group">
            <label className="form-label">Currency</label>
            <input
              type="text"
              className="form-input"
              value={trade.trade_currency}
              onChange={e => setTrade(prev => ({ ...prev, trade_currency: e.target.value.toUpperCase() }))}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Custom force min (0.50–0.99)</label>
            <input
              type="number"
              step="0.01"
              min={0.5}
              max={0.99}
              className="form-input"
              value={trade.force_min_probability}
              onChange={e => setTrade(prev => ({ ...prev, force_min_probability: Number(e.target.value) }))}
            />
          </div>
        </div>

        <div className="form-group">
          <label className="form-label">Deriv API token (trade scope)</label>
          <input
            type="password"
            className="form-input"
            placeholder={trade.token_configured ? `Configured ${trade.token_mask || ''}` : 'Paste token — never shown again'}
            value={tradeToken}
            onChange={e => setTradeToken(e.target.value)}
            autoComplete="off"
          />
          <div className="form-hint">Stored encrypted on the server. UI only shows last-4 mask.</div>
        </div>

        {trade.token_configured && (
          <div className="alert alert-info" style={{ marginBottom: '1rem' }}>
            <Database size={14}/>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="flex items-center gap-2" style={{ flexWrap: 'wrap', marginBottom: 6 }}>
                <strong>Linked account</strong>
                {accountLoading && <Loader2 size={12} className="spin"/>}
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ padding: '2px 8px', fontSize: 12 }}
                  onClick={() => loadTradeAccount(true)}
                  disabled={accountLoading || !online}
                >
                  Refresh
                </button>
              </div>
              {tradeAccount?.ok ? (
                <div className="font-mono text-xs" style={{ display: 'grid', gap: 4 }}>
                  <div>
                    {tradeAccount.account_type === 'demo'
                      ? <span className="badge badge-amber">DEMO</span>
                      : <span className="badge badge-green">REAL</span>}
                    {' '}{tradeAccount.loginid}
                    {tradeAccount.fullname ? ` · ${tradeAccount.fullname}` : ''}
                  </div>
                  <div>
                    Balance:{' '}
                    <strong className="text-primary">
                      {(tradeAccount.balance ?? 0).toFixed(2)} {tradeAccount.currency || ''}
                    </strong>
                  </div>
                  <div>
                    Today P/L:{' '}
                    <strong style={{ color: (tradeAccount.today_profit ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {(tradeAccount.today_profit ?? 0) >= 0 ? '+' : ''}
                      {(tradeAccount.today_profit ?? 0).toFixed(2)} {tradeAccount.currency || ''}
                    </strong>
                    {' · '}Recent P/L:{' '}
                    <strong style={{ color: (tradeAccount.recent_profit ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {(tradeAccount.recent_profit ?? 0) >= 0 ? '+' : ''}
                      {(tradeAccount.recent_profit ?? 0).toFixed(2)}
                    </strong>
                    {' · '}trades {tradeAccount.recent_trades ?? 0}
                    {' '}(W{tradeAccount.recent_wins ?? 0}/L{tradeAccount.recent_losses ?? 0})
                  </div>
                  {tradeAccount.email && <div className="text-dim">{tradeAccount.email}</div>}
                </div>
              ) : (
                <div className="text-xs">
                  {tradeAccount?.error
                    ? `Could not load account: ${tradeAccount.error}`
                    : 'Save a valid trade-scope token to see balance and profit.'}
                </div>
              )}
            </div>
          </div>
        )}

        {tradeMsg && (
          <div className={`alert ${tradeOk ? 'alert-success' : 'alert-error'}`}>
            {tradeOk ? <CheckCircle size={14}/> : <XCircle size={14}/>}
            {tradeMsg}
          </div>
        )}

        <div className="flex gap-3" style={{ flexWrap: 'wrap' }}>
          <button
            id="btn-save-trade-prefs"
            className="btn btn-primary"
            onClick={saveTradePrefs}
            disabled={tradeSaving || !online}
          >
            {tradeSaving ? <Loader2 size={14} className="spin"/> : <ShieldCheck size={14}/>}
            Save trade prefs
          </button>
          <button
            id="btn-save-trade-token"
            className="btn btn-ghost"
            onClick={saveTradeToken}
            disabled={tradeSaving || !online || tradeToken.trim().length < 8}
          >
            Save token
          </button>
          <button
            id="btn-clear-trade-token"
            className="btn btn-ghost"
            onClick={clearTradeToken}
            disabled={tradeSaving || !online || !trade.token_configured}
          >
            Clear token
          </button>
        </div>
      </div>
    </div>
  );
}
