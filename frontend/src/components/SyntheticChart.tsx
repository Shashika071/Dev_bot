import { useEffect, useRef, useState } from 'react';
import { Activity, Maximize2, Minimize2, RefreshCw } from 'lucide-react';
import { CandlestickData, ColorType, IChartApi, ISeriesApi, LineData, LineStyle, SeriesMarker, createChart } from 'lightweight-charts';
import { syntheticApi, SyntheticChartPayload } from '../api/synthetic';

const TIMEFRAMES = ['1m', '5m', '15m', '1h'] as const;

const OVERLAYS = [
  { key: 'frama', label: 'FRAMA', color: '#5de0b7' },
  { key: 'frama_upper', label: 'Upper band', color: '#738ab1' },
  { key: 'frama_lower', label: 'Lower band', color: '#738ab1' },
  { key: 'range_filter', label: 'Range Filter', color: '#edbd71' },
] as const;

function candles(rows: SyntheticChartPayload['candles']): CandlestickData[] {
  const unique = new Map<number, CandlestickData>();
  for (const row of rows) {
    if (![row.epoch, row.open, row.high, row.low, row.close].every(Number.isFinite) || row.epoch <= 0) continue;
    const epoch = Math.floor(row.epoch);
    unique.set(epoch, { time: epoch as CandlestickData['time'], open: row.open, high: row.high, low: row.low, close: row.close });
  }
  return [...unique.entries()].sort(([a], [b]) => a - b).map(([, value]) => value);
}

export function SyntheticChart({ online }: { online: boolean }) {
  const panel = useRef<HTMLElement | null>(null);
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const price = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const lines = useRef<Partial<Record<typeof OVERLAYS[number]['key'], ISeriesApi<'Line'>>>>({});
  const fitted = useRef(false);
  const drawn = useRef<CandlestickData[]>([]);
  const drawnTf = useRef('');
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('1m');
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [payload, setPayload] = useState<SyntheticChartPayload | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const full = fullscreen || expanded;

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
    return () => { resize.disconnect(); current.remove(); chart.current = null; price.current = null; lines.current = {}; };
  }, []);

  useEffect(() => {
    fitted.current = false;
    setPayload(null);
    price.current?.setData([]);
    Object.values(lines.current).forEach(series => series?.setData([]));
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    const pollMs = 15000;
    async function load() {
      if (!online) return;
      controller = new AbortController();
      const abortTimer = setTimeout(() => controller?.abort(), 20000);
      setLoading(true);
      try {
        const next = await syntheticApi.chart(timeframe, controller.signal);
        if (!cancelled) { setPayload(next); setError(next.error || ''); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Chart unavailable');
      } finally {
        clearTimeout(abortTimer);
        if (!cancelled) { setLoading(false); timeout = setTimeout(() => void load(), pollMs); }
      }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timeout); controller?.abort(); };
  }, [timeframe, online]);

  useEffect(() => {
    if (!payload || !price.current || !chart.current) return;
    const rows = candles(payload.candles || []);
    if (!rows.length) return;
    const series = price.current;
    const sameChart = drawnTf.current === timeframe && drawn.current.length > 0;
    const historyShifted = !sameChart || Math.abs(rows.length - drawn.current.length) > 3;
    if (historyShifted) {
      series.setData(rows);
      drawn.current = rows;
      drawnTf.current = timeframe;
      for (const overlay of OVERLAYS) {
        const unique = new Map<number, LineData>();
        for (const point of payload.overlays?.[overlay.key] || []) {
          if (!Number.isFinite(point.epoch) || !Number.isFinite(point.value) || point.epoch <= 0) continue;
          unique.set(Math.floor(point.epoch), { time: Math.floor(point.epoch) as LineData['time'], value: point.value });
        }
        lines.current[overlay.key]?.setData([...unique.entries()].sort(([a], [b]) => a - b).map(([, point]) => point));
      }
      const times = new Set(rows.map(row => Number(row.time)));
      const markers: SeriesMarker<CandlestickData['time']>[] = [];
      for (const mark of payload.overlays?.signals || []) {
        const epoch = Math.floor(mark.epoch);
        if (!times.has(epoch)) continue;
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
      if (!fitted.current) {
        chart.current.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 120), to: rows.length + 2 });
        fitted.current = true;
      }
      return;
    }
    const last = rows[rows.length - 1];
    const held = drawn.current[drawn.current.length - 1];
    if (Number(last.time) === Number(held.time)) {
      const next = { ...last, high: Math.max(last.high, held.high), low: Math.min(last.low, held.low) };
      series.update(next);
      drawn.current[drawn.current.length - 1] = next;
    } else if (Number(last.time) > Number(held.time)) {
      series.update(last);
      drawn.current.push(last);
    }
  }, [payload, timeframe]);

  useEffect(() => {
    if (!online) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const step = timeframe === '1m' ? 60 : timeframe === '5m' ? 300 : timeframe === '15m' ? 900 : 3600;
    async function follow() {
      try {
        const tick = await syntheticApi.quote();
        if (cancelled || tick.quote == null || tick.epoch == null || (tick.age_seconds ?? 99) > 5) return;
        const series = price.current;
        const bars = drawn.current;
        const last = bars[bars.length - 1];
        if (!series || !last) return;
        const bucket = Math.floor(tick.epoch / step) * step;
        const priceValue = tick.quote;
        setLivePrice(priceValue);
        if (bucket === Number(last.time)) {
          const next = { ...last, high: Math.max(last.high, priceValue), low: Math.min(last.low, priceValue), close: priceValue };
          series.update(next);
          bars[bars.length - 1] = next;
        } else if (bucket > Number(last.time)) {
          const next = { time: bucket as CandlestickData['time'], open: priceValue, high: priceValue, low: priceValue, close: priceValue };
          series.update(next);
          bars.push(next);
        }
      } catch {
        /* The candle history stays on screen if a tick is missed. */
      } finally {
        if (!cancelled) timer = setTimeout(() => void follow(), 400);
      }
    }
    void follow();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [online, timeframe]);

  return <section ref={panel} className={`sy-panel sy-chart${expanded ? ' sy-chart-expanded' : ''}`} aria-label="Volatility 100 chart">
    <div className="sy-panel-title">
      <div><h2><Activity size={17} /> Volatility 100</h2><p>R_100 · {timeframe} · {livePrice == null ? 'Waiting for ticks' : `Live ${livePrice.toFixed(2)}`} · Chart times UTC</p></div>
      <div className="sy-chart-tools">
        <div className="sy-chart-tabs" role="group" aria-label="Chart timeframe">
          {TIMEFRAMES.map(tf => <button type="button" key={tf} className={tf === timeframe ? 'selected' : ''} aria-pressed={tf === timeframe} onClick={() => { fitted.current = false; drawn.current = []; drawnTf.current = ''; setTimeframe(tf); }}>{tf}</button>)}
        </div>
        <button type="button" className="sy-chart-full" aria-pressed={full} onClick={() => void toggleFullscreen()}>
          {full ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          {full ? 'Exit full screen' : 'Full screen'}
        </button>
      </div>
    </div>
    <div className="sy-chart-legend">{OVERLAYS.map(overlay => <span key={overlay.key}><i style={{ background: overlay.color }} />{overlay.label}</span>)}<span className="sy-chart-count">{payload?.candles?.length ?? 0} candles {loading && <RefreshCw size={11} className="sy-spin" />}</span></div>
    <div className="sy-trend-dash" aria-label="Trend confirmation">
      <strong>Trend confirmation</strong>
      <span>Structure <b>{payload?.trend_system?.dashboard?.trend || 'Waiting'}</b></span>
      <span>ROC <b>{payload?.trend_system?.dashboard?.roc || 'Waiting'}</b></span>
      <span>TSI <b>{payload?.trend_system?.dashboard?.tsi || 'Waiting'}</b></span>
    </div>
    {error && <p className="sy-error" role="alert">{error}</p>}
    {!online && <p className="sy-muted">Waiting for the analysis service.</p>}
    {online && !loading && payload && !payload.candles?.length && <p className="sy-muted">No completed candles available yet.</p>}
    <div ref={host} className="sy-chart-host" />
    <p className="sy-footnote">A demo trade still needs FRAMA and the Range Filter to agree.</p>
  </section>;
}
