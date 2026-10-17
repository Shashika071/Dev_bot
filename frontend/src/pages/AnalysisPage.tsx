import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, wsUrl } from '../api/client';
import { PriceChart } from '../components/chart/PriceChart';
import { CandleBar, LiveSignal, OverlayKey, accuracyFeatureGroupLabel, accuracyProfileLabel, asAccuracySignal, asLiveSignal, coverChart, defaultOverlays, indicatorOverlay, liveSignalMarkers, mergeCandles, mergeLiveSignals, signalOutcomeLabel, toggleOverlay } from '../chart/state';

type Interval = 'tick' | '1m' | '5m' | '15m' | '30m' | '1h' | '1d';
type DrawMode = 'none' | 'hline' | 'label' | 'trendline' | 'rectangle';

type AccuracyChartStatus = {
  running: boolean;
  feed_fresh: boolean;
  history_loading?: boolean;
  feature_profile?: string;
  feature_groups?: string[];
  signals_today: number;
  max_per_day: number;
  reason: string;
  error: string | null;
  signals: Record<string, unknown>[];
};

type Snapshot = {
  interval?: string;
  fresh: boolean;
  stale_reason?: string | null;
  regime: string;
  trend?: { label?: string; strength?: string; direction?: string; adx?: number | null; ema20?: number | null; ema50?: number | null };
  structure: string;
  volatility?: { state?: string; atr?: number | null; ratio?: number | null };
  strength?: { label?: string; adx?: number | null };
  noise_filter?: { filtered_swings?: number; detail?: string };
  volume_note: string;
  timeframes: Record<string, { score: number; state: string }>;
  indicators: Record<string, number | null>;
  series: Record<string, { epoch: number; value: number }[]>;
  events: Array<Record<string, any>>;
  strategies: Array<Record<string, any>>;
  confluence: Record<string, any>;
  checklist?: Array<{ name: string; ok: boolean; detail: string }>;
  signal: Record<string, any> | null;
  buy_signals?: Array<{ epoch: number; direction: string; label: string; reason?: string; price?: number | null }>;
  sell_signals?: Array<{ epoch: number; direction: string; label: string; reason?: string; price?: number | null }>;
  candles: Array<{ epoch: number; open: number; high: number; low: number; close: number }>;
  ml: { note?: string; heads?: Record<string, any> };
};

const INTERVALS: Interval[] = ['tick', '1m', '5m', '15m', '30m', '1h', '1d'];

export function AnalysisPage({
  symbol = 'R_100',
  sidebarOpen = true,
  onToggleSidebar,
}: {
  symbol?: string;
  sidebarOpen?: boolean;
  onToggleSidebar?: () => void;
}) {
  const [interval, setInterval] = useState<Interval>('1m');
  const [showReadings, setShowReadings] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [ticks, setTicks] = useState<Array<{ epoch: number; quote: number }>>([]);
  const [overlays, setOverlays] = useState(defaultOverlays);
  const [error, setError] = useState('');
  const [drawMode, setDrawMode] = useState<DrawMode>('none');
  const [draft, setDraft] = useState<{ epoch: number | null; price: number } | null>(null);
  const [drawings, setDrawings] = useState<Array<Record<string, any>>>([]);
  const [execution, setExecution] = useState<Record<string, any> | null>(null);
  const [older, setOlder] = useState<CandleBar[]>([]);
  const [liveSignals, setLiveSignals] = useState<LiveSignal[]>([]);
  const [accuracySignals, setAccuracySignals] = useState<LiveSignal[]>([]);
  const [accuracyStatus, setAccuracyStatus] = useState<AccuracyChartStatus | null>(null);
  const [accuracyError, setAccuracyError] = useState('');
  const historyLock = useRef(false);
  const historyExhausted = useRef(false);
  const historyRetryAt = useRef(0);
  const historyRequest = useRef(0);
  const historyPage = useRef({ interval, symbol });
  historyPage.current = { interval, symbol };

  const load = useCallback(async () => {
    try {
      if (interval === 'tick') {
        const payload = await api<{ points: Array<{ epoch: number; quote: number }> }>(
          `/market/ticks?symbol=${encodeURIComponent(symbol)}&limit=400`,
        );
        setTicks(payload.points || []);
      }
      const next = await api<Snapshot>(
        `/analysis/snapshot?symbol=${encodeURIComponent(symbol)}&interval=${interval === 'tick' ? '1m' : interval}`,
      );
      setSnapshot((current) => keepLiveCandle(current, next));
      const saved = await api<Array<Record<string, any>>>(
        `/analysis/annotations?symbol=${encodeURIComponent(symbol)}&interval=${interval === 'tick' ? '1m' : interval}`,
      );
      setDrawings(saved);
      setExecution(await api('/setup/execution-status'));
      try {
        const history = await api<Array<Record<string, unknown>>>('/signals/?limit=20');
        setLiveSignals((current) => mergeLiveSignals(current,
          history
            .map(asLiveSignal)
            .filter((row): row is LiveSignal => row !== null && (!row.symbol || row.symbol === symbol)),
        ));
      } catch {
        /* chart still works if signal history is unavailable */
      }
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Market snapshot failed');
    }
  }, [interval, symbol]);

  const chartInterval = interval === 'tick' ? '1m' : interval;
  const chartCandles = useMemo(
    () => mergeCandles(older, snapshot?.candles || []),
    [older, snapshot?.candles],
  );

  const loadOlder = useCallback(async () => {
    if (interval === 'tick' || historyLock.current || historyExhausted.current) return;
    if (Date.now() < historyRetryAt.current) return;
    if (snapshot?.interval && snapshot.interval !== chartInterval) return;
    if (chartCandles.length >= 6000) {
      historyExhausted.current = true;
      return;
    }
    const oldest = older[0]?.epoch ?? snapshot?.candles?.[0]?.epoch;
    if (!oldest) return;
    const page = { interval, symbol };
    const requestId = historyRequest.current + 1;
    historyRequest.current = requestId;
    historyLock.current = true;
    try {
      const payload = await api<{ candles?: CandleBar[]; exhausted?: boolean; error?: string }>(
        `/market/history?symbol=${encodeURIComponent(symbol)}&interval=${chartInterval}&before=${oldest}&limit=1000`,
      );
      if (requestId !== historyRequest.current) return;
      if (historyPage.current.interval !== page.interval || historyPage.current.symbol !== page.symbol) return;
      const incoming = (payload.candles || []).filter((bar) => bar.epoch < oldest);
      if (payload.error) {
        historyRetryAt.current = Date.now() + 8000;
        return;
      }
      if (incoming.length) setOlder((current) => mergeCandles(incoming, current));
      if (payload.exhausted || !incoming.length) historyExhausted.current = true;
    } catch {
      historyRetryAt.current = Date.now() + 8000;
    } finally {
      if (requestId === historyRequest.current) historyLock.current = false;
    }
  }, [chartCandles.length, chartInterval, interval, older, snapshot?.candles, snapshot?.interval, symbol]);

  useEffect(() => {
    setOlder([]);
    historyExhausted.current = false;
    historyLock.current = false;
    historyRetryAt.current = 0;
    historyRequest.current += 1;
  }, [interval, symbol]);

  useEffect(() => {
    if (interval === 'tick' || older.length || !snapshot?.candles?.length) return;
    if (snapshot.interval && snapshot.interval !== chartInterval) return;
    void loadOlder();
  }, [chartInterval, interval, loadOlder, older.length, snapshot?.candles?.length, snapshot?.interval]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  // Hydrate independently of chart snapshots, including after a missed socket event.
  useEffect(() => {
    if (symbol !== 'R_100') return;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 10000);
      try {
        const status = await api<AccuracyChartStatus>('/accuracy/status', { signal: controller.signal });
        if (stopped) return;
        const signals = (status.signals || []).map(asAccuracySignal)
          .filter((row): row is LiveSignal => row !== null && row.symbol === symbol);
        setAccuracySignals((current) => mergeLiveSignals(current, signals));
        setAccuracyStatus(status);
        setAccuracyError('');
      } catch {
        if (!stopped) setAccuracyError('Touch Accuracy feed unavailable — reconnecting');
      } finally {
        window.clearTimeout(timeout);
        if (!stopped) timer = window.setTimeout(refresh, 5000);
      }
    };
    void refresh();
    return () => { stopped = true; window.clearTimeout(timer); controller?.abort(); };
  }, [symbol]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    const connect = () => {
      socket = new WebSocket(wsUrl('/ws/market'));
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'market') {
            const tick = message.data?.last_tick;
            if (tick && Number.isFinite(Number(tick.epoch)) && Number.isFinite(Number(tick.quote))) {
              setSnapshot((current) => applyLiveTick(current, interval, Number(tick.epoch), Number(tick.quote)));
            }
          } else if (message.type === 'signal_alert') {
            const incoming = asLiveSignal(message.data || {});
            if (incoming && (!incoming.symbol || incoming.symbol === symbol)) {
              setLiveSignals((current) => mergeLiveSignals(current, [incoming]));
            }
            load();
          } else if (message.type === 'accuracy_signal' || message.type === 'accuracy_outcome') {
            const incoming = asAccuracySignal(message.data || {});
            if (incoming?.symbol === symbol) {
              setAccuracySignals((current) => mergeLiveSignals(current, [incoming]));
            }
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        if (!stopped) window.setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      stopped = true;
      socket?.close();
    };
  }, [load]);

  const markers = useMemo(() => {
    if (!snapshot) return [];
    const rows: Array<{ epoch: number; direction: string; text: string }> = [];
    if (overlays.patterns) {
      const latest = new Map<string, Record<string, any>>();
      const swings: Array<{ epoch: number; direction: string; text: string }> = [];
      snapshot.events.forEach((event) => {
        if (event.type === 'support' || event.type === 'resistance' || event.status === 'filtered') return;
        if (String(event.type).startsWith('swing_')) {
          const label = event.geometry?.label ? String(event.geometry.label) : String(event.type);
          swings.push({
            epoch: Number(event.epoch),
            direction: String(event.direction || 'neutral'),
            text: label,
          });
          return;
        }
        latest.set(String(event.type), event);
      });
      swings.slice(-8).forEach((row) => rows.push(row));
      latest.forEach((event) => {
        const label = event.geometry?.label
          ? String(event.geometry.label)
          : String(event.type).split('_').join(' ');
        rows.push({
          epoch: Number(event.end_epoch || event.epoch),
          direction: String(event.direction || 'neutral'),
          text: label,
        });
      });
    }
    if (!overlays.patterns && snapshot) {
      const best = bestChartPattern(snapshot.events || []);
      if (best) {
        rows.push({
          epoch: Number(best.end_epoch || best.epoch),
          direction: String(best.direction || 'neutral'),
          text: patternTitle(String(best.type)),
        });
      }
    }
    const merged = new Map<number, { epoch: number; direction: string; text: string }>();
    rows.forEach((row) => {
      const prior = merged.get(row.epoch);
      if (!prior) merged.set(row.epoch, row);
      else prior.text = `${prior.text} · ${row.text}`;
    });
    if (overlays.signals) {
      const paint = (items: Array<{ epoch: number; label?: string; direction?: string }> | undefined, label: string, direction: string) => {
        (items || []).forEach((item) => {
          const epoch = Number(item.epoch);
          const prior = merged.get(epoch);
          const text = prior && !prior.text.startsWith('BUY') && !prior.text.startsWith('SELL')
            ? `${label} · ${prior.text}`
            : label;
          merged.set(epoch, { epoch, direction, text });
        });
      };
      paint(snapshot.buy_signals, 'BUY', 'upper');
      paint(snapshot.sell_signals, 'SELL', 'lower');
    }
    const barTimes = (interval === 'tick' ? ticks.map((point) => point.epoch) : chartCandles.map((bar) => bar.epoch))
      .filter((epoch) => Number.isFinite(epoch))
      .sort((a, b) => a - b);
    const signals = [...liveSignals, ...accuracySignals].filter((row) => !row.symbol || row.symbol === symbol);
    liveSignalMarkers(signals, barTimes).forEach((marker) => {
      merged.set(marker.epoch, marker);
    });
    return [...merged.values()];
  }, [accuracySignals, chartCandles, interval, liveSignals, overlays.patterns, overlays.signals, snapshot, symbol, ticks]);

  const chartOverlay = useMemo(
    () => indicatorOverlay(interval === 'tick'
      ? ticks.map((point) => ({ epoch: point.epoch, open: point.quote, high: point.quote, low: point.quote, close: point.quote }))
      : chartCandles),
    [chartCandles, interval, ticks],
  );

  const levels = useMemo(() => {
    const buys = snapshot?.buy_signals || [];
    const sells = snapshot?.sell_signals || [];
    const latestBuy = buys.length ? buys[buys.length - 1] : undefined;
    const latestSell = sells.length ? sells[sells.length - 1] : undefined;
    const latestCross = liveSignals.find((row) => !row.symbol || row.symbol === symbol);
    const crossLine = latestCross && latestCross.resolved_barrier != null && Number.isFinite(latestCross.resolved_barrier)
      ? [{
          id: 'cross-barrier',
          price: Number(latestCross.resolved_barrier),
          color: latestCross.direction === 'lower' ? '#ff4d6d' : '#00e5a0',
          title: latestCross.direction === 'lower' ? 'LOWER' : 'UPPER',
        }]
      : [];
    const sideLines = overlays.signals
      ? [
          latestBuy && Number.isFinite(Number(latestBuy.price))
            ? { id: 'buy-signal', price: Number(latestBuy.price), color: '#00e5a0', title: 'BUY' }
            : null,
          latestSell && Number.isFinite(Number(latestSell.price))
            ? { id: 'sell-signal', price: Number(latestSell.price), color: '#ff4d6d', title: 'SELL' }
            : null,
        ].filter((item): item is { id: string; price: number; color: string; title: string } => item !== null)
      : [];
    const drawn = drawings.filter((item) => item.kind === 'hline').map((item) => ({
      id: item.id,
      price: Number(item.geometry?.price),
      color: '#e2eaf8',
      title: item.label || 'line',
    }));
    if (!snapshot || !overlays.levels) {
      return [...sideLines, ...crossLine, ...drawn].filter((item) => Number.isFinite(item.price));
    }
    const fromEvents = snapshot.events
      .filter((event) => event.type === 'support' || event.type === 'resistance' || event.type === 'sideways_market')
      .slice(-8)
      .flatMap((event) => {
        if (event.type === 'sideways_market') {
          return [
            { id: `${event.id}-high`, price: Number(event.geometry?.high), color: '#ffaa00', title: 'Range high' },
            { id: `${event.id}-low`, price: Number(event.geometry?.low), color: '#ffaa00', title: 'Range low' },
          ];
        }
        return [{
          id: String(event.id),
          price: Number(event.geometry?.price),
          color: event.type === 'support' ? '#00e5a0' : '#ff4d6d',
          title: event.type,
        }];
      });
    const atr = Number(snapshot.indicators?.atr);
    const close = Number(snapshot.indicators?.close);
    const atrLines = Number.isFinite(atr) && Number.isFinite(close) && atr > 0
      ? [
          { id: 'atr-up', price: close + atr, color: '#94a3b8', title: '+ATR' },
          { id: 'atr-down', price: close - atr, color: '#94a3b8', title: '-ATR' },
        ]
      : [];
    return [...fromEvents, ...atrLines, ...sideLines, ...crossLine, ...drawn].filter((item) => Number.isFinite(item.price));
  }, [drawings, liveSignals, overlays.levels, overlays.signals, snapshot, symbol]);

  async function onPick(pick: { epoch: number | null; price: number | null }) {
    if (drawMode === 'none' || pick.price == null) return;
    const chartInterval = interval === 'tick' ? '1m' : interval;
    if ((drawMode === 'trendline' || drawMode === 'rectangle') && !draft) {
      setDraft({ epoch: pick.epoch, price: pick.price });
      return;
    }
    const geometry = draft
      ? { time1: draft.epoch, price1: draft.price, time2: pick.epoch, price2: pick.price, price: pick.price }
      : { price: pick.price, epoch: pick.epoch };
    const created = await api('/analysis/annotations', {
      method: 'POST',
      body: JSON.stringify({
        symbol,
        interval: chartInterval,
        kind: drawMode === 'label' ? 'label' : drawMode === 'rectangle' ? 'rectangle' : drawMode,
        geometry,
        label: drawMode === 'label' ? 'note' : drawMode,
      }),
    });
    setDrawings((current) => [...current, created as Record<string, any>]);
    setDraft(null);
    setDrawMode('none');
  }

  const heads = snapshot?.ml?.heads || {};
  const latestAccuracy = accuracySignals.find((row) => row.symbol === symbol);
  const accuracyLive = !accuracyError && accuracyStatus?.running && accuracyStatus.feed_fresh && !accuracyStatus.history_loading;
  const accuracyState = accuracyError ? 'Reconnecting' : !accuracyStatus ? 'Connecting'
    : accuracyStatus.running ? accuracyStatus.history_loading ? 'Loading model history' : accuracyStatus.feed_fresh ? 'Watching live' : 'Waiting for ticks' : 'Stopped';
  return (
    <section className="analysis-workspace">
      <div className="analysis-toolbar">
        <button type="button" className={sidebarOpen ? 'active' : ''} onClick={onToggleSidebar} title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}>sidebar</button>
        <strong>{symbol}</strong>
        {INTERVALS.map((item) => (
          <button key={item} className={item === interval ? 'active' : ''} onClick={() => setInterval(item)}>{item}</button>
        ))}
        {(Object.keys(defaultOverlays) as OverlayKey[]).map((key) => (
          <button key={key} className={overlays[key] ? 'active' : ''} onClick={() => setOverlays((current) => toggleOverlay(current, key))}>
            {key}
          </button>
        ))}
        <button className={showReadings ? 'active' : ''} onClick={() => setShowReadings((open) => !open)}>readings</button>
      </div>
      {symbol === 'R_100' && <div className="chart-touch-status" aria-label="Touch Accuracy signals">
        <div className="chart-touch-heading">
          <strong>Touch Accuracy</strong>
          <span className={`chart-touch-state${accuracyLive ? ' is-live' : ''}`}>{accuracyState}</span>
          {accuracyStatus && <span>{accuracyStatus.signals_today} / {accuracyStatus.max_per_day} today</span>}
          {accuracyStatus && <span className="chart-touch-note" title={(accuracyStatus.feature_groups || []).map(accuracyFeatureGroupLabel).join(', ')}>
            {accuracyProfileLabel(accuracyStatus.feature_profile)}
            {!!accuracyStatus.feature_groups?.length && ` · ${accuracyStatus.feature_groups.length} input groups`}
          </span>}
        </div>
        {latestAccuracy ? <div className="chart-touch-signal" aria-live="polite">
          <span>Latest</span>
          <strong className={`chart-touch-${latestAccuracy.direction}`}>
            {latestAccuracy.direction === 'upper' ? '↑ UPPER' : '↓ LOWER'} {latestAccuracy.barrier_input}
          </strong>
          <span>{latestAccuracy.probability == null ? '—' : `${(latestAccuracy.probability * 100).toFixed(2)}%`} estimated · 9 min</span>
          <span className={`chart-touch-outcome ${latestAccuracy.resolved ? latestAccuracy.outcome === true ? 'touched' : latestAccuracy.outcome === false ? 'missed' : '' : ''}`}>
            {signalOutcomeLabel(latestAccuracy)}
          </span>
          <time dateTime={new Date(latestAccuracy.epoch * 1000).toISOString()}>
            {new Date(latestAccuracy.epoch * 1000).toLocaleString('en-GB', { timeZone: 'Asia/Colombo', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })} Colombo
          </time>
          <span className="chart-touch-note">Simulated outcome</span>
          <span className="chart-touch-note" title={latestAccuracy.feature_groups?.map(accuracyFeatureGroupLabel).join(', ')}>
            {accuracyProfileLabel(latestAccuracy.feature_profile)}
          </span>
        </div> : <span className="chart-touch-note">
          {accuracyStatus?.running ? 'Waiting for a qualifying signal — arrows appear automatically.' : 'Start signals from the Touch Accuracy tab.'}
        </span>}
        {(accuracyError || accuracyStatus?.error) && <span className="chart-touch-error">{accuracyError || accuracyStatus?.error}</span>}
      </div>}
      {error && <div className="alert alert-warning">{error}</div>}
      <div className={`analysis-stage${showReadings ? ' readings-open' : ''}`}>
        <div className="analysis-tools">
          <button title="Horizontal line" className={drawMode === 'hline' ? 'active' : ''} onClick={() => { setDraft(null); setDrawMode(drawMode === 'hline' ? 'none' : 'hline'); }}>—</button>
          <button title="Trendline" className={drawMode === 'trendline' ? 'active' : ''} onClick={() => { setDraft(null); setDrawMode(drawMode === 'trendline' ? 'none' : 'trendline'); }}>/</button>
          <button title="Zone" className={drawMode === 'rectangle' ? 'active' : ''} onClick={() => { setDraft(null); setDrawMode(drawMode === 'rectangle' ? 'none' : 'rectangle'); }}>▢</button>
          <button title="Label" className={drawMode === 'label' ? 'active' : ''} onClick={() => { setDraft(null); setDrawMode(drawMode === 'label' ? 'none' : 'label'); }}>T</button>
        </div>
        <PriceChart
          candles={chartCandles}
          line={interval === 'tick' ? ticks : undefined}
          onReachStart={loadOlder}
          markers={markers}
          levels={levels}
          segments={chartSegments(
            snapshot?.events || [],
            drawings,
            overlays.levels,
            chartCandles.length > 1
              ? { start: chartCandles[0].epoch, end: chartCandles[chartCandles.length - 1].epoch }
              : undefined,
          )}
          ema={overlays.ema ? chartOverlay.ema8 : []}
          ema20={overlays.ema ? chartOverlay.ema20 : []}
          ema50={overlays.ema ? chartOverlay.ema50 : []}
          upper={overlays.bands ? chartOverlay.upper : []}
          lower={overlays.bands ? chartOverlay.lower : []}
          showEma={overlays.ema}
          showBands={overlays.bands}
          onPick={onPick}
        />
        {showReadings && <aside className="analysis-side">
          <h3>Market</h3>
          <p>{snapshot?.fresh ? 'Live' : 'Stale'} · {snapshot?.trend?.label || '…'} · {snapshot?.trend?.strength || '…'} trend</p>
          <p>Structure {snapshot?.structure || '…'} · Volatility {snapshot?.volatility?.state || '…'}</p>
          {snapshot?.stale_reason && <p className="text-xs text-dim">{snapshot.stale_reason}</p>}
          <p className="text-xs text-dim">Filtered swings: {snapshot?.noise_filter?.filtered_swings ?? 0}</p>
          <h3>1m · 5m · 15m · 1h</h3>
          {Object.entries(snapshot?.timeframes || {}).map(([name, value]) => (
            <div key={name} className="analysis-row"><span>{name}</span><b>{labelState(value.state)}</b><span>{value.score}</span></div>
          ))}
          <h3>Indicators</h3>
          <div className="analysis-row"><span>EMA 20</span><b>{fmt(snapshot?.indicators?.ema20)}</b></div>
          <div className="analysis-row"><span>EMA 50</span><b>{fmt(snapshot?.indicators?.ema50)}</b></div>
          <div className="analysis-row"><span>ATR</span><b>{fmt(snapshot?.volatility?.atr ?? snapshot?.indicators?.atr)}</b></div>
          <div className="analysis-row"><span>RSI</span><b>{fmt(snapshot?.indicators?.rsi)}</b></div>
          <div className="analysis-row"><span>MACD</span><b>{fmt(snapshot?.indicators?.macd_hist)}</b></div>
          <div className="analysis-row"><span>ADX</span><b>{fmt(snapshot?.indicators?.adx)}</b></div>
          <div className="analysis-row"><span>Stoch</span><b>{fmt(snapshot?.indicators?.stoch_k)}</b></div>
          <h3>Entry confirmation</h3>
          <p>{snapshot?.confluence?.checks_passed ?? 0}/12 checks · {(snapshot?.buy_signals || []).length} BUY · {(snapshot?.sell_signals || []).length} SELL</p>
          {(snapshot?.checklist || []).map((item) => (
            <div key={item.name} className="analysis-row">
              <span>{item.ok ? '✓' : '·'} {item.name}</span>
              <b className="text-xs">{item.detail}</b>
            </div>
          ))}
          <h3>Strategies</h3>
          {(snapshot?.strategies || []).map((item) => (
            <div key={`${item.name}-${item.direction}`} className="analysis-row">
              <span>{item.name}</span><b>{item.direction}</b><span>{item.score}</span>
            </div>
          ))}
          <h3>Model probabilities</h3>
          <p className="text-xs text-dim">{snapshot?.ml?.note}</p>
          {Object.entries(heads).map(([name, head]) => (
            <div key={name} className="analysis-row">
              <span>{name}</span>
              <b>{head?.available ? prob(head) : 'not active'}</b>
            </div>
          ))}
          <h3>Execution</h3>
          <p className="text-xs">
            {execution?.allowed
              ? 'Optional auto-trade is unlocked. Orders are still only sent after a legacy signal.'
              : `Locked: ${execution?.reason || 'server_execution_disabled'}. Change this in Configuration.`}
          </p>
        </aside>}
      </div>
    </section>
  );
}

function chartSegments(
  events: Array<Record<string, any>>,
  drawings: Array<Record<string, any>>,
  showLevels: boolean,
  range?: { start: number; end: number },
) {
  const isChannel = (event: Record<string, any>) =>
    event.type === 'support_trendline' || event.type === 'resistance_trendline';
  const channel = events.filter(isChannel);
  const patterns = events.filter((event) => CHART_PATTERNS.has(String(event.type)));
  const rest = [
    ...(showLevels ? events.filter((event) => !isChannel(event) && !CHART_PATTERNS.has(String(event.type))) : []),
    ...drawings.filter((item) => item.kind === 'trendline' || item.kind === 'rectangle' || item.kind === 'ray'),
  ].filter((event) => event.geometry?.time1 != null).slice(-8);
  const toSegment = (id: string, line: Record<string, any>, color: string) => ({
    id,
    time1: Number(line.time1),
    price1: Number(line.price1),
    time2: Number(line.time2 ?? line.time1),
    price2: Number(line.price2 ?? line.price1),
    color,
  });
  const drawn = [
    ...channel.map((event) => {
      const segment = toSegment(String(event.id), event.geometry || {}, '#d1d4dc');
      return range ? coverChart(segment, range.start, range.end) : segment;
    }),
    ...patterns.flatMap((event) => {
      const color = event.direction === 'lower' ? '#ef5350' : event.direction === 'upper' ? '#26a69a' : '#ffaa00';
      const lines = Array.isArray(event.geometry?.lines) ? event.geometry.lines : [];
      return lines.map((line: Record<string, any>, index: number) => {
        const segment = toSegment(`${event.id}:${index}`, line, color);
        return range ? coverChart(segment, range.start, range.end) : segment;
      });
    }),
    ...rest.map((event) => toSegment(
      String(event.id),
      event.geometry || {},
      event.kind === 'rectangle' ? '#ffaa00' : '#3b82f6',
    )),
  ];
  return drawn.filter((item) => Number.isFinite(item.time1) && Number.isFinite(item.price1) && Number.isFinite(item.price2));
}

const CHART_PATTERNS = new Set([
  'double_top', 'double_bottom', 'head_and_shoulders', 'inverse_head_and_shoulders',
  'triangle', 'falling_wedge', 'rising_wedge', 'bull_flag', 'bear_flag', 'range_breakout',
]);

function patternTitle(type: string): string {
  return type.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function bestChartPattern(events: Array<Record<string, any>>): Record<string, any> | null {
  const found = events.filter((event) => CHART_PATTERNS.has(String(event.type)));
  if (!found.length) return null;
  return found.reduce((best, event) => Number(event.confidence) > Number(best.confidence) ? event : best);
}

const STEP_SECONDS: Record<string, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '1h': 3600,
  '1d': 86400,
};

function keepLiveCandle<T extends { candles?: Array<{ epoch: number; open: number; high: number; low: number; close: number }> }>(
  current: T | null,
  next: T,
): T {
  const liveBars = current?.candles;
  const nextBars = next.candles;
  if (!liveBars?.length || !nextBars?.length) return next;
  const live = liveBars[liveBars.length - 1];
  const incoming = nextBars[nextBars.length - 1];
  if (live.epoch < incoming.epoch) return next;
  const candles = nextBars.slice();
  if (live.epoch === incoming.epoch) {
    candles[candles.length - 1] = {
      ...incoming,
      high: Math.max(incoming.high, live.high),
      low: Math.min(incoming.low, live.low),
      close: live.close,
    };
  } else {
    candles.push(live);
  }
  return { ...next, candles };
}

function applyLiveTick<T extends { candles: Array<{ epoch: number; open: number; high: number; low: number; close: number }> }>(
  current: T | null,
  interval: string,
  epoch: number,
  price: number,
): T | null {
  if (!current?.candles?.length) return current;
  const step = STEP_SECONDS[interval];
  if (!step) return current;
  const bucket = Math.floor(epoch / step) * step;
  const candles = current.candles.slice();
  const last = candles[candles.length - 1];
  if (epoch < last.epoch) return current;
  if (last.epoch === bucket) {
    candles[candles.length - 1] = {
      ...last,
      high: Math.max(last.high, price),
      low: Math.min(last.low, price),
      close: price,
    };
  } else if (epoch >= last.epoch + step) {
    candles.push({ epoch: bucket, open: price, high: price, low: price, close: price });
  } else {
    return current;
  }
  return { ...current, candles };
}

function labelState(state: string): string {
  if (state === 'upper') return 'Bullish';
  if (state === 'lower') return 'Bearish';
  return 'Sideways';
}

function fmt(value: number | null | undefined): string {
  return value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(2);
}

function prob(head: Record<string, number | null>): string {
  if (head.up == null && head.down == null) return 'active';
  return `U ${pct(head.up)} D ${pct(head.down)} N ${pct(head.neutral)}`;
}

function pct(value: number | null): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}
