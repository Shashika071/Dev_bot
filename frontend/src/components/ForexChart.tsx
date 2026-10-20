import { useEffect, useRef, useState } from 'react';
import {
  CandlestickData, ColorType, IChartApi, IPriceLine, ISeriesApi, LineStyle,
  createChart,
} from 'lightweight-charts';
import { Activity, Loader2, Maximize2, Minimize2 } from 'lucide-react';
import { API, forexApi, type ForexChartPayload } from '../api/client';

const TIMEFRAMES = ['15m', '1h', '4h'] as const;
const HISTORY_COUNT: Record<(typeof TIMEFRAMES)[number], number> = {
  '15m': 1200,
  '1h': 900,
  '4h': 600,
};

type OverlayKey = 'sr' | 'fib' | 'supplyDemand' | 'liquidity' | 'patterns' | 'signals';

const OVERLAYS: { id: OverlayKey; label: string }[] = [
  { id: 'sr', label: 'Support / resistance' },
  { id: 'fib', label: 'Fibonacci' },
  { id: 'supplyDemand', label: 'Supply / demand' },
  { id: 'liquidity', label: 'Liquidity swings' },
  { id: 'patterns', label: 'Breakout / patterns' },
  { id: 'signals', label: 'Signals' },
];

function pct(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function MlIdea({ idea }: { idea?: ForexChartPayload['ml_idea'] }) {
  const rise = idea?.p_rise;
  const fall = idea?.p_fall;
  const call = idea?.direction || 'NO TRADE';
  const tone = call === 'RISE' ? 'positive' : call === 'FALL' ? 'negative' : 'neutral';
  return (
    <div className="fx-idea" aria-live="polite">
      <div className="fx-idea-head">
        <strong>ML idea · confidence</strong>
        <span className={`fx-tag fx-${tone}`}>{call}</span>
        <span className="fx-tag">{idea?.level ? `${idea.level} confidence` : 'waiting'}</span>
      </div>
      <div className="fx-idea-bar" role="img" aria-label={`Rise ${pct(rise)}, Fall ${pct(fall)}`}>
        <span className="fx-idea-rise" style={{ width: `${Math.max(0, Math.min(100, (rise ?? 0) * 100))}%` }} />
        <span className="fx-idea-fall" style={{ width: `${Math.max(0, Math.min(100, (fall ?? 0) * 100))}%` }} />
      </div>
      <div className="fx-idea-split">
        <span>Rise {pct(rise)}</span>
        <span>Fall {pct(fall)}</span>
      </div>
      <p>
        {idea?.reason || 'Scores the latest EURUSD model on closed 15m, 1h, and 4h bars. Near 50/50 stays NO TRADE.'}
        {idea?.held_out_accuracy != null ? ` Held-out hit rate ${(idea.held_out_accuracy * 100).toFixed(1)}%.` : ''}
        {' '}This does not place or block a demo trade.
      </p>
    </div>
  );
}

function asUnix(epoch: number): CandlestickData['time'] {
  return Math.floor(epoch) as CandlestickData['time'];
}

function minStep(candleTimes: number[]): number {
  if (candleTimes.length < 2) return 900;
  let best = Number.POSITIVE_INFINITY;
  for (let i = 1; i < candleTimes.length; i += 1) {
    const diff = Math.abs(candleTimes[i] - candleTimes[i - 1]);
    if (diff > 0 && diff < best) best = diff;
  }
  return Number.isFinite(best) ? best : 900;
}

/** Map an event onto the candle that contains it (open <= t < next open). */
function containingCandleTime(epoch: number, candleTimes: number[]): number | null {
  if (!candleTimes.length || !Number.isFinite(epoch)) return null;
  let chosen: number | null = null;
  for (const time of candleTimes) {
    if (time <= epoch) chosen = time;
    else break;
  }
  if (chosen == null) return null;
  const step = minStep(candleTimes);
  if (epoch - chosen >= step * 2.5) return null;
  return chosen;
}

function normalizeCandles(rows: ForexChartPayload['candles']): CandlestickData[] {
  const byTime = new Map<number, CandlestickData>();
  for (const row of rows) {
    const epoch = Math.floor(Number(row.epoch));
    if (!Number.isFinite(epoch) || epoch <= 0) continue;
    if (![row.open, row.high, row.low, row.close].every(Number.isFinite)) continue;
    byTime.set(epoch, {
      time: asUnix(epoch),
      open: row.open,
      high: row.high,
      low: row.low,
      close: row.close,
    });
  }
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, candle]) => candle);
}

function nearestLevels<T extends { price?: number; low?: number; high?: number }>(
  rows: T[],
  mid: number,
  limit: number,
): T[] {
  return [...rows]
    .map(row => {
      const price = Number(row.price ?? ((Number(row.low) + Number(row.high)) / 2));
      return { row, dist: Math.abs(price - mid) };
    })
    .filter(item => Number.isFinite(item.dist))
    .sort((a, b) => a.dist - b.dist)
    .slice(0, limit)
    .map(item => item.row);
}

export function ForexChart({
  symbol = 'frxEURUSD',
  online,
}: {
  symbol?: string;
  online: boolean;
}) {
  const panelRef = useRef<HTMLElement | null>(null);
  const host = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const fittedKey = useRef('');
  const stickToRight = useRef(true);
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>('15m');
  const [payload, setPayload] = useState<ForexChartPayload | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [overlays, setOverlays] = useState<Record<OverlayKey, boolean>>({
    sr: true, fib: true, supplyDemand: true, liquidity: false, patterns: true, signals: true,
  });
  const [overlayCounts, setOverlayCounts] = useState({ lines: 0, markers: 0 });

  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: '#0b1624' },
        textColor: '#9bb0c6',
      },
      grid: {
        vertLines: { color: '#152436' },
        horzLines: { color: '#152436' },
      },
      rightPriceScale: { borderColor: '#24384f', scaleMargins: { top: 0.08, bottom: 0.12 } },
      timeScale: {
        borderColor: '#24384f',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 6,
        minBarSpacing: 2,
        fixLeftEdge: true,
        lockVisibleTimeRangeOnResize: true,
        shiftVisibleRangeOnNewBar: true,
      },
      crosshair: { mode: 1 },
      handleScroll: { vertTouchDrag: false },
    });
    const series = chart.addCandlestickSeries({
      upColor: '#3dcf9a', downColor: '#ef6b7c', borderVisible: false,
      wickUpColor: '#3dcf9a', wickDownColor: '#ef6b7c',
      priceFormat: { type: 'price', precision: 5, minMove: 0.00001 },
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const onRange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const barsInfo = series.barsInLogicalRange(range);
      stickToRight.current = !barsInfo || barsInfo.barsAfter < 8;
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);

    const resize = () => {
      if (!host.current || !chartRef.current) return;
      const height = document.fullscreenElement
        ? Math.max(420, window.innerHeight - 170)
        : 420;
      chart.applyOptions({ width: host.current.clientWidth, height });
    };
    resize();
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(resize) : null;
    observer?.observe(host.current);
    window.addEventListener('resize', resize);
    const onFs = () => {
      setFullscreen(Boolean(document.fullscreenElement));
      requestAnimationFrame(resize);
    };
    document.addEventListener('fullscreenchange', onFs);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', resize);
      document.removeEventListener('fullscreenchange', onFs);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      priceLines.current = [];
    };
  }, []);

  useEffect(() => {
    fittedKey.current = '';
    stickToRight.current = true;
    setPayload(null);
    setLoading(true);
    setError('');
    const series = seriesRef.current;
    if (series) {
      series.setData([]);
      series.setMarkers([]);
      for (const line of priceLines.current) {
        try { series.removePriceLine(line); } catch { /* already removed */ }
      }
      priceLines.current = [];
    }
    setOverlayCounts({ lines: 0, markers: 0 });
  }, [symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const requestKey = `${symbol}:${timeframe}`;
    const pollMs = timeframe === '15m' ? 4000 : timeframe === '4h' ? 12000 : 8000;
    const load = async () => {
      if (!online) {
        setLoading(false);
        return;
      }
      try {
        const next = await forexApi.chart(symbol, timeframe, HISTORY_COUNT[timeframe]);
        if (!cancelled && `${next.symbol}:${next.timeframe}` === requestKey) {
          setPayload(next);
          setError('');
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Chart unavailable');
          setLoading(false);
        }
      }
      if (!cancelled) timer = setTimeout(load, pollMs);
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [symbol, timeframe, online]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || !payload?.candles?.length) return;

    const candles = normalizeCandles(payload.candles);
    if (!candles.length) {
      setError(`No ${timeframe} candles to display`);
      return;
    }
    series.setData(candles);

    for (const line of priceLines.current) {
      try { series.removePriceLine(line); } catch { /* already removed */ }
    }
    priceLines.current = [];

    const addPriceLine = (price: number, color: string, title: string) => {
      if (!Number.isFinite(price)) return;
      const line = series.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: title.slice(0, 18),
      });
      priceLines.current.push(line);
    };

    const mid = Number(candles[candles.length - 1]?.close) || 0;
    const levels = payload.levels || {};
    if (overlays.sr) {
      for (const zone of nearestLevels(levels.support_resistance || [], mid, 8)) {
        addPriceLine(
          Number(zone.price),
          zone.type === 'resistance' ? '#ef9a63' : '#6ec3ff',
          String(zone.type || 'S/R'),
        );
      }
    }
    if (overlays.fib && levels.fibonacci?.available) {
      for (const level of levels.fibonacci.levels || []) {
        addPriceLine(Number(level.price), '#c9a0ff', `Fib ${Number(level.ratio).toFixed(3)}`);
      }
    }
    if (overlays.supplyDemand) {
      for (const zone of nearestLevels(levels.supply_demand || [], mid, 6)) {
        const color = zone.type === 'demand' ? '#4fd1a5' : '#f07178';
        addPriceLine(Number(zone.low), color, `${zone.type || 'zone'} L`);
        addPriceLine(Number(zone.high), color, `${zone.type || 'zone'} H`);
      }
    }
    if (overlays.liquidity) {
      for (const point of nearestLevels(levels.liquidity || [], mid, 6)) {
        addPriceLine(Number(point.price), '#8fa3b8', String(point.type || 'swing').replace(/_/g, ' '));
      }
    }

    const candleTimes = candles.map(c => Number(c.time));
    const markers: Array<{
      time: CandlestickData['time'];
      position: 'aboveBar' | 'belowBar';
      color: string;
      shape: 'arrowDown' | 'arrowUp' | 'circle';
      text: string;
    }> = [];

    if (payload.symbol !== symbol || payload.timeframe !== timeframe) return;

    if (overlays.patterns && Array.isArray(payload.events)) {
      for (const event of payload.events) {
        const snapped = containingCandleTime(Number(event.epoch), candleTimes);
        if (snapped == null) continue;
        const direction = String(event.direction || '').toUpperCase();
        const label = String(event.type || event.name || 'pattern');
        markers.push({
          time: asUnix(snapped),
          position: direction === 'FALL' || direction === 'BEARISH' ? 'aboveBar' : 'belowBar',
          color: '#d4b36a',
          shape: 'circle',
          text: label.slice(0, 10),
        });
      }
    }

    if (overlays.signals && payload.signals?.length) {
      for (const signal of payload.signals) {
        if (!signal.created_epoch) continue;
        const snapped = containingCandleTime(Number(signal.created_epoch), candleTimes);
        if (snapped == null) continue;
        markers.push({
          time: asUnix(snapped),
          position: signal.direction === 'FALL' ? 'aboveBar' : 'belowBar',
          color: signal.direction === 'FALL' ? '#ef6b7c' : '#3dcf9a',
          shape: signal.direction === 'FALL' ? 'arrowDown' : 'arrowUp',
          text: String(signal.direction || 'SIG'),
        });
      }
    }

    const unique = new Map<number, typeof markers[number]>();
    for (const marker of markers.sort((a, b) => Number(a.time) - Number(b.time))) {
      unique.set(Number(marker.time), marker);
    }
    series.setMarkers([...unique.values()]);
    setOverlayCounts({ lines: priceLines.current.length, markers: unique.size });

    const fitKey = `${symbol}:${timeframe}`;
    if (fittedKey.current !== fitKey) {
      chart.timeScale().fitContent();
      // Show a readable recent window first; user can scroll left for older history.
      const visible = Math.min(candles.length, timeframe === '4h' ? 120 : timeframe === '1h' ? 180 : 220);
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candles.length - visible),
        to: candles.length + 4,
      });
      fittedKey.current = fitKey;
      stickToRight.current = true;
    } else if (stickToRight.current) {
      chart.timeScale().scrollToRealTime();
    }
  }, [payload, overlays, symbol, timeframe]);

  const toggleFullscreen = async () => {
    const node = panelRef.current;
    if (!node) return;
    try {
      if (!document.fullscreenElement) await node.requestFullscreen();
      else await document.exitFullscreen();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fullscreen unavailable');
    }
  };

  const age = payload?.last_candle_age_seconds;
  const ageLabel = age == null ? ''
    : age < 60 ? 'just updated'
      : age < 3600 ? `${Math.round(age / 60)}m behind live close`
        : `${Math.round(age / 3600)}h behind (market may be closed)`;
  const span = payload?.span_days;
  const bars = payload?.bar_count ?? payload?.candles?.length ?? 0;

  return (
    <section ref={panelRef} className={`fx-panel fx-chart-panel${fullscreen ? ' fx-chart-fullscreen' : ''}`} aria-label="Live market chart">
      <div className="fx-panel-heading fx-chart-heading">
        <div>
          <h2><Activity size={18} /> Live market · {symbol.replace('frx', '').replace(/(.{3})(.{3})/, '$1/$2')}</h2>
          <p className="fx-muted fx-heading-note">
            {bars} bars{span != null ? ` · ~${span} days` : ''}
            {payload?.source === 'aggregated_1h' ? ' · 4h built from 1h' : ''}
            {ageLabel ? ` · ${ageLabel}` : ''}
            {overlayCounts.lines || overlayCounts.markers
              ? ` · ${overlayCounts.lines} levels / ${overlayCounts.markers} markers`
              : ''}
          </p>
        </div>
        <div className="fx-chart-toolbar">
          <div className="fx-chart-tfs" role="group" aria-label="Chart timeframe">
            {TIMEFRAMES.map(tf => (
              <button key={tf} type="button" className={`fx-chip ${timeframe === tf ? 'active' : ''}`}
                onClick={() => setTimeframe(tf)}>{tf}</button>
            ))}
          </div>
          <button type="button" className="fx-chip fx-fullscreen-btn" onClick={() => void toggleFullscreen()}
            aria-label={fullscreen ? 'Exit fullscreen chart' : 'Fullscreen chart'}>
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            {fullscreen ? 'Exit' : 'Full'}
          </button>
        </div>
      </div>
      <div className="fx-overlay-toggles">
        {OVERLAYS.map(item => (
          <label key={item.id} className="fx-overlay-check">
            <input type="checkbox" checked={overlays[item.id]}
              onChange={event => setOverlays(prev => ({ ...prev, [item.id]: event.target.checked }))} />
            {item.label}
          </label>
        ))}
      </div>
      {symbol === 'frxEURUSD' && <MlIdea idea={payload?.ml_idea} />}
      {error && <p className="fx-inline-error">{error}</p>}
      {loading && !payload && <p className="fx-muted"><Loader2 size={14} className="spin" /> Loading {timeframe} history…</p>}
      <div ref={host} className="fx-chart-host" />
      <p className="fx-footnote">
        API {API.replace(/\/$/, '')}/forex/chart · Scroll left for older bars · Fullscreen for a larger workspace.
      </p>
    </section>
  );
}
