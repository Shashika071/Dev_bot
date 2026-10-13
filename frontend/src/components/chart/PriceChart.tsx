import { useEffect, useRef } from 'react';
import {
  CandlestickData,
  ColorType,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  UTCTimestamp,
  createChart,
} from 'lightweight-charts';

export type Bar = { epoch: number; open: number; high: number; low: number; close: number };
export type ChartMarker = { epoch: number; direction: string; text: string };
export type LevelLine = { id: string; price: number; color: string; title: string };
export type Segment = { id: string; time1: number; price1: number; time2: number; price2: number; color: string };

type Props = {
  candles: Bar[];
  line?: { epoch: number; quote: number }[];
  markers: ChartMarker[];
  levels: LevelLine[];
  segments: Segment[];
  ema: { epoch: number; value: number }[];
  ema20: { epoch: number; value: number }[];
  ema50: { epoch: number; value: number }[];
  upper: { epoch: number; value: number }[];
  lower: { epoch: number; value: number }[];
  showEma: boolean;
  showBands: boolean;
  onPick?: (pick: { epoch: number | null; price: number | null }) => void;
  onReachStart?: () => void;
};

function stamp(epoch: number): UTCTimestamp {
  return epoch as UTCTimestamp;
}

export function PriceChart(props: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const extras = useRef<ISeriesApi<'Line'>[]>([]);
  const priceLines = useRef<IPriceLine[]>([]);
  const painted = useRef<CandlestickData[]>([]);
  const applying = useRef(false);
  const pick = useRef(props.onPick);
  const reachStart = useRef(props.onReachStart);
  pick.current = props.onPick;
  reachStart.current = props.onReachStart;

  useEffect(() => {
    if (!host.current) return;
    const next = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0e0e0e' },
        textColor: '#b2b5be',
      },
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0.45)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.45)' },
      },
      rightPriceScale: { borderColor: '#2a2e39' },
      timeScale: { borderColor: '#2a2e39', timeVisible: true, secondsVisible: false, rightOffset: 6 },
      crosshair: { mode: 0 },
    });
    const series = next.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });
    next.subscribeClick((param) => {
      const price = param.point ? series.coordinateToPrice(param.point.y) : null;
      const epoch = typeof param.time === 'number' ? param.time : null;
      pick.current?.({ epoch, price });
    });
    next.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (applying.current || !range || range.from >= 40) return;
      reachStart.current?.();
    });
    chart.current = next;
    candles.current = series;
    return () => {
      extras.current = [];
      painted.current = [];
      next.remove();
      chart.current = null;
      candles.current = null;
    };
  }, []);

  useEffect(() => {
    const series = candles.current;
    const view = chart.current;
    if (!series || !view) return;
    const mapped: CandlestickData[] = props.line && props.line.length
      ? props.line.map((point) => ({
          time: stamp(point.epoch),
          open: point.quote,
          high: point.quote,
          low: point.quote,
          close: point.quote,
        }))
      : props.candles.map((bar) => ({
          time: stamp(bar.epoch),
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        }));
    mapped.sort((a, b) => Number(a.time) - Number(b.time));
    const bars = mapped.filter((bar, index, all) => index === 0 || bar.time !== all[index - 1].time);
    if (!bars.length) return;
    const previous = painted.current;
    const sameStart = previous.length > 0 && previous[0].time === bars[0].time;
    const prepended = previous.length > 0 && Number(bars[0].time) < Number(previous[0].time);
    const appended = sameStart && bars.length === previous.length + 1
      && previous[previous.length - 1].time === bars[bars.length - 2].time;
    const sameLast = sameStart && bars.length === previous.length
      && previous[previous.length - 1].time === bars[bars.length - 1].time;
    if (prepended) {
      const first = Number(previous[0].time);
      const added = bars.filter((bar) => Number(bar.time) < first).length;
      const logical = view.timeScale().getVisibleLogicalRange();
      const nextRange = logical && added > 0
        ? { from: logical.from + added, to: logical.to + added }
        : null;
      applying.current = true;
      series.setData(bars);
      if (nextRange) view.timeScale().setVisibleLogicalRange(nextRange);
      requestAnimationFrame(() => {
        if (!chart.current) {
          applying.current = false;
          return;
        }
        if (nextRange) chart.current.timeScale().setVisibleLogicalRange(nextRange);
        applying.current = false;
        const range = chart.current.timeScale().getVisibleLogicalRange();
        if (range && range.from < 40) reachStart.current?.();
      });
    } else if (!sameStart) {
      series.setData(bars);
      const nextRange = { from: Math.max(0, bars.length - 120), to: bars.length + 6 };
      view.timeScale().setVisibleLogicalRange(nextRange);
    } else if (appended || sameLast) {
      const last = bars[bars.length - 1];
      const prior = previous[previous.length - 1];
      if (!sameLast || last.open !== prior.open || last.high !== prior.high || last.low !== prior.low || last.close !== prior.close) {
        series.update(last);
      }
    } else {
      const logical = view.timeScale().getVisibleLogicalRange();
      applying.current = true;
      series.setData(bars);
      if (logical) view.timeScale().setVisibleLogicalRange(logical);
      requestAnimationFrame(() => {
        if (logical && chart.current) chart.current.timeScale().setVisibleLogicalRange(logical);
        applying.current = false;
      });
    }
    painted.current = bars;
    const last = bars[bars.length - 1];
    const prior = previous[previous.length - 1];
    const followLive = appended || (sameLast && !!prior && (
      last.open !== prior.open || last.high !== prior.high || last.low !== prior.low || last.close !== prior.close
    ));
    if (followLive) {
      const logical = view.timeScale().getVisibleLogicalRange();
      if (!logical || logical.to >= bars.length - 3) view.timeScale().scrollToRealTime();
    }
    const markers = [...props.markers]
      .filter((marker) => Number.isFinite(marker.epoch))
      .sort((a, b) => a.epoch - b.epoch)
      .filter((marker, index, all) => index === 0 || marker.epoch !== all[index - 1].epoch);
    series.setMarkers(markers.map((marker) => ({
      time: stamp(marker.epoch),
      position: marker.direction === 'lower' ? 'aboveBar' : 'belowBar',
      color: marker.direction === 'lower' ? '#ff4d6d' : marker.direction === 'upper' ? '#00e5a0' : '#ffaa00',
      shape: marker.direction === 'lower' ? 'arrowDown' : 'arrowUp',
      text: marker.text,
    })));
    priceLines.current.forEach((line) => series.removePriceLine(line));
    priceLines.current = props.levels.map((level) => series.createPriceLine({
      price: level.price,
      color: level.color,
      lineStyle: LineStyle.Dashed,
      title: level.title,
      lineWidth: 1,
    }));
    extras.current.forEach((item) => view.removeSeries(item));
    extras.current = [];
    const addLine = (points: { epoch: number; value: number }[], color: string) => {
      if (points.length < 2) return;
      const line = view.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      const ordered = [...points].sort((a, b) => a.epoch - b.epoch);
      line.setData(ordered.filter((point, index, all) => index === 0 || point.epoch !== all[index - 1].epoch).map((point) => ({ time: stamp(point.epoch), value: point.value })));
      extras.current.push(line);
    };
    if (props.showEma) {
      addLine(props.ema20, '#38bdf8');
      addLine(props.ema50, '#c084fc');
      addLine(props.ema, '#00d4ff');
    }
    if (props.showBands) {
      addLine(props.upper, 'rgba(255,170,0,0.8)');
      addLine(props.lower, 'rgba(255,170,0,0.8)');
    }
    props.segments.forEach((segment) => {
      const line = view.addLineSeries({
        color: segment.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        autoscaleInfoProvider: () => null,
      });
      line.setData([
        { time: stamp(segment.time1), value: segment.price1 },
        { time: stamp(Math.max(segment.time2, segment.time1 + 1)), value: segment.price2 },
      ]);
      extras.current.push(line);
    });
  }, [props]);

  return <div ref={host} className="analysis-chart" />;
}
