import { useEffect, useRef, useState } from 'react';
import { Activity, Maximize2, Minimize2, RefreshCw } from 'lucide-react';
import { CandlestickData, ColorType, IChartApi, ISeriesApi, LineData, LineStyle, SeriesMarker, createChart } from 'lightweight-charts';
import { syntheticApi, SyntheticChartPayload, SyntheticConfidence } from '../api/synthetic';
import { appendTick, candleRefresh, chartSignalMarkers, freshTick, pollResource, reconcileCandles, Tick, ChartSignal } from './syntheticChartData';

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d'] as const;
const STEP: Record<(typeof TIMEFRAMES)[number], number> = {
  '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '1d': 86400,
};

const OVERLAYS = [
  { key: 'frama', label: 'FRAMA', color: '#5de0b7' },
  { key: 'frama_upper', label: 'Upper band', color: '#738ab1' },
  { key: 'frama_lower', label: 'Lower band', color: '#738ab1' },
  { key: 'range_filter', label: 'Range Filter', color: '#edbd71' },
] as const;

function percent(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function LiveConfidence({ reading, stale, error }: { reading: SyntheticConfidence | null; stale: boolean; error: string }) {
  const rise = reading?.combined_p_rise ?? reading?.p_rise;
  const fall = reading?.combined_p_fall ?? reading?.p_fall;
  const call = reading?.direction || 'NO TRADE';
  const tone = call === 'RISE' ? 'positive' : call === 'FALL' ? 'negative' : 'neutral';
  const checks = reading?.checks || [];
  return (
    <div className="sy-confidence" aria-live="polite">
      <div className="sy-confidence-head">
        <strong>Signal confidence</strong>
        <span className={`sy-tag sy-${stale ? 'neutral' : tone}`}>{stale ? 'LAST READING' : call}</span>
        <span className="sy-tag">{percent(reading?.combined_confidence ?? reading?.confidence)}</span>
      </div>
      <div className="sy-confidence-bar" role="img" aria-label={`Rise ${percent(rise)}, Fall ${percent(fall)}`}>
        <span className="sy-confidence-rise" style={{ width: `${Math.max(0, Math.min(100, (rise ?? 0) * 100))}%` }} />
        <span className="sy-confidence-fall" style={{ width: `${Math.max(0, Math.min(100, (fall ?? 0) * 100))}%` }} />
      </div>
      <div className="sy-confidence-split">
        <span>Rise {percent(rise)}</span>
        <span>Fall {percent(fall)}</span>
      </div>
      {checks.length > 0 && <div className="sy-confidence-checks">{checks.map(check => <span key={check.id} className={`sy-confidence-check ${check.status.toLowerCase()}`} title={check.detail}>{check.name} {check.side === 'PASS' || check.side === 'BLOCK' || check.side === 'WAIT' ? check.status : check.side}</span>)}</div>}
      {rise == null && fall == null && <p className="sy-muted">{reading?.reason || 'Waiting for the closed-candle signals'}</p>}
      {rise != null && reading?.reason && <p className="sy-muted">{reading.reason}</p>}
      {rise != null && <p className="sy-muted">This bar is the share of FRAMA, Range Filter, 5m, 15m and momentum. It does not place a trade.</p>}
      {(stale || error) && <p className="sy-muted" role="status">{error || 'Confidence is stale. Waiting for a fresh closed-candle reading.'}</p>}
      {reading?.decision_epoch != null && <p className="sy-footnote">Closed-candle decision: {new Date(reading.decision_epoch * 1000).toLocaleTimeString('en-GB', { timeZone: 'UTC' })} UTC</p>}
    </div>
  );
}

export function SyntheticChart({ online, signals = [], liveSignal }: { online: boolean; signals?: ChartSignal[]; liveSignal?: ChartSignal }) {
  const panel = useRef<HTMLElement | null>(null);
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const price = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const lines = useRef<Partial<Record<typeof OVERLAYS[number]['key'], ISeriesApi<'Line'>>>>({});
  const fitted = useRef(false);
  const drawn = useRef<CandlestickData[]>([]);
  const drawnTf = useRef('');
  const lastTick = useRef<Tick | null>(null);
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('1m');
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [confidence, setConfidence] = useState<SyntheticConfidence | null>(null);
  const [confidenceError, setConfidenceError] = useState('');
  const [confidenceReceived, setConfidenceReceived] = useState(0);
  const [tickReceived, setTickReceived] = useState(0);
  const [tickAge, setTickAge] = useState(0);
  const [feedError, setFeedError] = useState('');
  const [now, setNow] = useState(Date.now());
  const [payload, setPayload] = useState<SyntheticChartPayload | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const full = fullscreen || expanded;
  const quoteAge = tickReceived ? tickAge + Math.max(0, (now - tickReceived) / 1000) : null;
  const feedLive = online && !feedError && quoteAge != null && quoteAge <= 8;
  const confidenceStale = !online || !!confidence?.stale || (!!confidenceReceived && now - confidenceReceived > 15000);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const sync = () => setFullscreen(document.fullscreenElement === panel.current);
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); };
    document.addEventListener('fullscreenchange', sync);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  useEffect(() => {
    if (!host.current || !chart.current) return;
    const apply = () => {
      if (!host.current || !chart.current) return;
      chart.current.applyOptions({
        width: host.current.clientWidth,
        height: full ? Math.max(240, host.current.clientHeight) : 410,
      });
    };
    const frame = requestAnimationFrame(() => requestAnimationFrame(apply));
    return () => cancelAnimationFrame(frame);
  }, [full]);

  async function toggleFullscreen() {
    const node = panel.current;
    if (!node) return;
    if (document.fullscreenElement === node || expanded) {
      if (document.fullscreenElement === node) await document.exitFullscreen().catch(() => undefined);
      setExpanded(false);
      return;
    }
    try {
      await node.requestFullscreen();
    } catch {
      setExpanded(true);
      return;
    }
    if (document.fullscreenElement !== node) setExpanded(true);
  }

  useEffect(() => {
    if (!host.current) return;
    const current = createChart(host.current, {
      width: host.current.clientWidth, height: 410,
      layout: { background: { type: ColorType.Solid, color: '#0b1420' }, textColor: '#8fa2bb' },
      grid: { vertLines: { color: '#142234' }, horzLines: { color: '#142234' } },
      rightPriceScale: { borderColor: '#233449', scaleMargins: { top: .12, bottom: .12 } },
      timeScale: { borderColor: '#233449', timeVisible: true, secondsVisible: false, rightOffset: 5 },
      handleScroll: { vertTouchDrag: false },
    });
    chart.current = current;
    price.current = current.addCandlestickSeries({
      upColor: '#54d5aa', downColor: '#ef7b8b', wickUpColor: '#54d5aa', wickDownColor: '#ef7b8b',
      borderVisible: false, priceFormat: { type: 'price', precision: 2, minMove: .01 },
    });
    for (const overlay of OVERLAYS) {
      lines.current[overlay.key] = current.addLineSeries({
        color: overlay.color, lineWidth: overlay.key.includes('frama_') ? 1 : 2,
        lineStyle: overlay.key.includes('frama_') ? LineStyle.Dashed : LineStyle.Solid,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
      });
    }
    const resize = new ResizeObserver(() => {
      if (!host.current) return;
      const full = document.fullscreenElement === host.current.parentElement || host.current.parentElement?.classList.contains('sy-chart-expanded');
      current.applyOptions({
        width: host.current.clientWidth,
        height: full ? Math.max(240, host.current.clientHeight) : 410,
      });
    });
    resize.observe(host.current);
    return () => { resize.disconnect(); current.remove(); chart.current = null; price.current = null; lines.current = {}; drawn.current = []; drawnTf.current = ''; fitted.current = false; };
  }, []);

  useEffect(() => {
    fitted.current = false;
    drawn.current = [];
    drawnTf.current = timeframe;
    setPayload(null);
    setError('');
    price.current?.setData([]);
    Object.values(lines.current).forEach(series => series?.setData([]));
    price.current?.setMarkers([]);
  }, [timeframe]);

  useEffect(() => {
    if (!online) { setLoading(false); return; }
    return pollResource({
      request: signal => syntheticApi.chart(timeframe, signal),
      success: next => { setPayload(next); setError(next.error || ''); },
      failure: error => setError(error instanceof Error ? error.message : 'Chart unavailable; reconnecting.'),
      busy: setLoading, intervalMs: 15000, timeoutMs: 25000,
    });
  }, [timeframe, online]);

  useEffect(() => {
    if (!payload || payload.timeframe !== timeframe || !price.current || !chart.current) return;
    const step = STEP[timeframe];
    const rows = reconcileCandles(payload.candles || [], drawn.current, lastTick.current, step, payload.snapshot_epoch ?? payload.server_time ?? Infinity);
    if (!rows.length) return;
    const series = price.current;
    const refresh = drawnTf.current === timeframe ? candleRefresh(drawn.current, rows) : { mode: 'replace' as const, from: 0 };
    if (refresh.mode === 'replace') {
      series.setData(rows);
      if (!fitted.current) {
        chart.current.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 120), to: rows.length + 2 });
        fitted.current = true;
      }
    } else {
      for (let index = refresh.from; index < rows.length; index += 1) series.update(rows[index]);
    }
    drawn.current = rows;
    drawnTf.current = timeframe;
    for (const overlay of OVERLAYS) {
      const unique = new Map<number, LineData>();
      for (const point of payload.overlays?.[overlay.key] || []) {
        if (!Number.isFinite(point.epoch) || !Number.isFinite(point.value) || point.epoch <= 0) continue;
        unique.set(Math.floor(point.epoch), { time: Math.floor(point.epoch) as LineData['time'], value: point.value });
      }
      const lineRows = [...unique.entries()].sort(([a], [b]) => a - b).map(([, point]) => point);
      const line = lines.current[overlay.key];
      if (!line) continue;
      if (refresh.mode === 'replace') line.setData(lineRows);
      else if (lineRows.length) line.update(lineRows[lineRows.length - 1]);
    }
    const generated = chartSignalMarkers(
      liveSignal ? [...signals, liveSignal] : signals,
      rows.map(row => Number(row.time)),
      step,
    );
    const times = new Set(rows.map(row => Number(row.time)));
    const taken = new Set(generated.map(mark => mark.time));
    const markers: SeriesMarker<CandlestickData['time']>[] = [];
    for (const mark of generated) {
      const rise = mark.direction === 'RISE';
      markers.push({
        time: mark.time as CandlestickData['time'],
        position: rise ? 'belowBar' : 'aboveBar',
        color: rise ? '#3dd68c' : '#ff5d73',
        shape: rise ? 'arrowUp' : 'arrowDown',
        size: 2,
        text: mark.direction,
      });
    }
    for (const mark of payload.overlays?.signals || []) {
      const epoch = Math.floor(mark.epoch);
      if (!times.has(epoch) || taken.has(epoch)) continue;
      const buy = mark.side === 'BUY';
      const frama = mark.source === 'FRAMA';
      markers.push({
        time: epoch as CandlestickData['time'],
        position: buy ? 'belowBar' : 'aboveBar',
        color: frama ? (buy ? '#2f6bff' : '#000000') : (buy ? '#ffe14a' : '#ffffff'),
        shape: buy ? 'arrowUp' : 'arrowDown',
        size: 2,
      });
    }
    for (const mark of payload.trend_system?.signals || []) {
      const epoch = Math.floor(mark.epoch);
      if (!times.has(epoch)) continue;
      const buy = mark.side === 'BUY';
      markers.push({
        time: epoch as CandlestickData['time'],
        position: buy ? 'belowBar' : 'aboveBar',
        color: buy ? '#00c853' : '#ff1744',
        shape: buy ? 'arrowUp' : 'arrowDown',
        size: 2,
      });
    }
    series.setMarkers(markers.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [payload, timeframe, signals, liveSignal]);

  useEffect(() => {
    if (!online) return;
    const step = STEP[timeframe];
    return pollResource({
      request: signal => syntheticApi.quote(signal),
      success: reading => {
        const tick = freshTick(reading, lastTick.current);
        if (!tick) { setFeedError(reading.error || 'Price feed is stale or reconnecting.'); return; }
        const changed = tick.epoch !== lastTick.current?.epoch || tick.quote !== lastTick.current?.quote;
        lastTick.current = tick;
        setLivePrice(tick.quote);
        setTickReceived(Date.now());
        setTickAge(reading.age_seconds ?? 0);
        setFeedError('');
        const series = price.current;
        const bars = drawn.current;
        if (!series || !bars.length || drawnTf.current !== timeframe || !changed) return;
        const next = appendTick(bars, tick, step);
        if (next === bars) return;
        // Periodically trim the chart's own storage as well as our local buffer.
        if (next.length === bars.length && Number(next[0].time) !== Number(bars[0].time)) series.setData(next);
        else series.update(next[next.length - 1]);
        drawn.current = next;
      },
      failure: () => setFeedError('Price feed disconnected; reconnecting.'),
      intervalMs: 500, timeoutMs: 5000,
    });
  }, [online, timeframe]);

  useEffect(() => {
    if (!online) return;
    return pollResource({
      request: signal => syntheticApi.confidence(signal),
      success: next => { setConfidence(next); setConfidenceError(next.error || ''); setConfidenceReceived(Date.now()); },
      failure: () => setConfidenceError('Confidence refresh failed; reconnecting.'),
      intervalMs: 2000, timeoutMs: 10000,
    });
  }, [online]);

  return <section ref={panel} className={`sy-panel sy-chart${expanded ? ' sy-chart-expanded' : ''}`} aria-label="Volatility 100 chart">
    <div className="sy-panel-title">
      <div><h2><Activity size={17} /> Volatility 100</h2><p>R_100 · {timeframe} · {livePrice == null ? 'Waiting for ticks' : `${feedLive ? 'Live' : 'Last price'} ${livePrice.toFixed(2)}`} · Chart times UTC</p></div>
      <div className="sy-chart-tools">
        <div className="sy-chart-tabs" role="group" aria-label="Chart timeframe">
          {TIMEFRAMES.map(tf => <button type="button" key={tf} className={tf === timeframe ? 'selected' : ''} aria-pressed={tf === timeframe} onClick={() => setTimeframe(tf)}>{tf}</button>)}
        </div>
        <button type="button" className="sy-chart-full" onClick={() => chart.current?.timeScale().scrollToRealTime()}>Go to latest</button>
        <button type="button" className="sy-chart-full" aria-pressed={full} onClick={() => void toggleFullscreen()}>
          {full ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          {full ? 'Exit full screen' : 'Full screen'}
        </button>
      </div>
    </div>
    <div className="sy-chart-legend">{OVERLAYS.map(overlay => <span key={overlay.key}><i style={{ background: overlay.color }} />{overlay.label}</span>)}<span><i style={{ background: '#3dd68c' }} />Generated signal</span><span className="sy-chart-count">{payload?.candles?.length ?? 0} candles {loading && <RefreshCw size={11} className="sy-spin" />}</span></div>
    <div className="sy-trend-dash" aria-label="Trend confirmation">
      <strong>Trend confirmation</strong>
      <span>Structure <b>{payload?.trend_system?.dashboard?.trend || 'Waiting'}</b></span>
      <span>ROC <b>{payload?.trend_system?.dashboard?.roc || 'Waiting'}</b></span>
      <span>TSI <b>{payload?.trend_system?.dashboard?.tsi || 'Waiting'}</b></span>
    </div>
    <LiveConfidence reading={confidence} stale={confidenceStale} error={confidenceError} />
    <p className="sy-footnote" role="status">{!online ? 'Analysis service offline. Reconnecting.' : feedLive ? `Price feed live · tick ${Math.floor(quoteAge ?? 0)}s ago` : feedError || 'Connecting to the live price feed…'}</p>
    {error && <p className="sy-error" role="alert">{error}</p>}
    {!online && <p className="sy-muted">Waiting for the analysis service.</p>}
    {online && !loading && payload && !payload.candles?.length && <p className="sy-muted">No completed candles available yet.</p>}
    <div ref={host} className="sy-chart-host" />
    <p className="sy-footnote">Green Rise and red Fall arrows are generated signals. A demo trade still needs FRAMA and the Range Filter to agree.</p>
  </section>;
}
