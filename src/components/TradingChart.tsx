import { useEffect, useMemo, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type Time,
  ColorType,
  LineStyle,
} from 'lightweight-charts'
import type { Candle, SignalEvaluation } from '../lib/types'
import { isValidCandle } from '../lib/deriv/candles'
import {
  computeDynamicSRv2,
  DYNAMIC_SR_V2_DEFAULTS,
} from '../lib/indicators/dynamicSRv2'

interface Props {
  candles: Candle[]
  evaluations: SignalEvaluation[]
  lastPrice: number | null
}

function lineStyleFromSetting(style: 'Solid' | 'Dashed' | 'Dotted'): LineStyle {
  if (style === 'Dashed') return LineStyle.Dashed
  if (style === 'Dotted') return LineStyle.Dotted
  return LineStyle.Solid
}

function horizSegment(fromTime: number, toTime: number, price: number) {
  const start = Math.min(fromTime, toTime)
  const end = Math.max(fromTime, toTime)
  return [
    { time: start as Time, value: price },
    { time: end as Time, value: price },
  ]
}

export function TradingChart({ candles, lastPrice, evaluations }: Props) {
  const mainRef = useRef<HTMLDivElement>(null)
  const fittedLen = useRef(0)
  const charts = useRef<{
    main?: IChartApi
    candle?: ISeriesApi<'Candlestick'>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    markers?: any
    priceLine?: IPriceLine
    srPriceLines: IPriceLine[]
    srSeries: ISeriesApi<'Line'>[]
  }>({ srPriceLines: [], srSeries: [] })

  const sr = useMemo(
    () => computeDynamicSRv2(candles, DYNAMIC_SR_V2_DEFAULTS),
    [candles],
  )
  const contexts = useMemo(() => candles.map((bar, i) => {
    const prior = candles.slice(Math.max(0, i - 20), i)
    if (!bar.isClosed || !isValidCandle(bar) || prior.length !== 20 || prior.some((p, j) => !p.isClosed || !isValidCandle(p) || p.time !== bar.time - (20 - j) * 60)) return null
    const high = Math.max(...prior.map(p => p.high))
    const low = Math.min(...prior.map(p => p.low))
    const label = bar.close > high ? 'Up breakout' : bar.close < low ? 'Down breakout'
      : bar.high > high && bar.low < low ? 'Both wicks rejected'
        : bar.high > high ? 'Upper rejection' : bar.low < low ? 'Lower rejection' : 'Inside range'
    return { high, low, label, time: bar.time }
  }), [candles])
  const latestContext = contexts.at(-1) ?? contexts.at(-2)

  useEffect(() => {
    if (!mainRef.current) return

    const main = createChart(mainRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0f1419' },
        textColor: '#9aa4b2',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1a222d' },
        horzLines: { color: '#1a222d' },
      },
      rightPriceScale: { borderColor: '#2a3544' },
      timeScale: { borderColor: '#2a3544', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 as const },
      width: mainRef.current.clientWidth,
      height: mainRef.current.clientHeight,
    })

    const candle = main.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderUpColor: '#26a69a',
      borderDownColor: '#ef5350',
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    })
    const markers = createSeriesMarkers(candle, [])
    charts.current = { main, candle, markers, srPriceLines: [], srSeries: [] }

    const ro = new ResizeObserver(() => {
      if (mainRef.current) {
        main.applyOptions({
          width: mainRef.current.clientWidth,
          height: mainRef.current.clientHeight,
        })
      }
    })
    ro.observe(mainRef.current)

    return () => {
      ro.disconnect()
      main.remove()
    }
  }, [])

  useEffect(() => {
    const c = charts.current
    if (!c.main || !c.candle || candles.length === 0) return

    c.candle.setData(
      candles.map((x) => ({
        time: x.time as Time,
        open: x.open,
        high: x.high,
        low: x.low,
        close: x.close,
      })),
    )
    if (candles.length >= fittedLen.current + 100 || fittedLen.current === 0) {
      c.main.timeScale().fitContent()
      fittedLen.current = candles.length
    }

    const byTime = new Map(evaluations.map((e) => [e.time, e]))
    const markers = []
    for (let i = 0; i < candles.length; i++) {
      const e = byTime.get(candles[i].time) as SignalEvaluation | undefined
      if (!e?.isQualified) continue
      if (e.final === 'RISE') {
        markers.push({
          time: candles[i].time as Time,
          position: 'belowBar' as const,
          color: '#00e676',
          shape: 'arrowUp' as const,
          text: `RISE${contexts[i] ? ' · ' + contexts[i]!.label : ''}`,
          size: 2,
        })
      } else if (e.final === 'FALL') {
        markers.push({
          time: candles[i].time as Time,
          position: 'aboveBar' as const,
          color: '#ff1744',
          shape: 'arrowDown' as const,
          text: `FALL${contexts[i] ? ' · ' + contexts[i]!.label : ''}`,
          size: 2,
        })
      }
    }
    if (DYNAMIC_SR_V2_DEFAULTS.showPivotPoints) {
      for (const p of sr.pivots) {
        markers.push({
          time: p.time as Time,
          position: (p.kind === 'H' ? 'aboveBar' : 'belowBar') as 'aboveBar' | 'belowBar',
          color: p.kind === 'H' ? DYNAMIC_SR_V2_DEFAULTS.resistanceColor : DYNAMIC_SR_V2_DEFAULTS.supportColor,
          shape: (p.kind === 'H' ? 'arrowDown' : 'arrowUp') as 'arrowDown' | 'arrowUp',
          text: p.kind,
          size: 1,
        })
      }
    }
    c.markers?.setMarkers(markers)

    // Clear previous S/R drawings
    for (const line of c.srPriceLines) c.candle.removePriceLine(line)
    for (const series of c.srSeries) c.main.removeSeries(series)
    c.srPriceLines = []
    c.srSeries = []

    // Each point uses only the preceding 20 closed minutes, never future bars.
    for (const boundary of ['high', 'low'] as const) {
      const channel = c.main.addSeries(LineSeries, {
        color: '#64b5f6', lineWidth: 1,
        lineStyle: boundary === 'high' ? LineStyle.Solid : LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: true,
        title: boundary === 'high' ? 'Prior 20 high' : 'Prior 20 low',
      })
      channel.setData(candles.map((bar, i) => contexts[i]
        ? { time: bar.time as Time, value: contexts[i]![boundary] }
        : { time: bar.time as Time }))
      c.srSeries.push(channel)
    }

    const endTime = candles[candles.length - 1].time
    const style = lineStyleFromSetting(DYNAMIC_SR_V2_DEFAULTS.lineStyle)
    const width = DYNAMIC_SR_V2_DEFAULTS.lineWidth as 1 | 2 | 3 | 4

    // Dynamic v2 channel mids — TV-style segments from channel start → now
    for (const lvl of sr.levels) {
      const series = c.main.addSeries(LineSeries, {
        color: lvl.color,
        lineWidth: width,
        lineStyle: style,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
        title: `${lvl.type === 'resistance' ? 'R' : 'S'}${lvl.strength}`,
      })
      series.setData(horizSegment(lvl.fromTime, endTime, lvl.price))
      c.srSeries.push(series)
      c.srPriceLines.push(
        c.candle.createPriceLine({
          price: lvl.price,
          color: lvl.color,
          lineWidth: width,
          lineStyle: style,
          axisLabelVisible: true,
          title: `${lvl.type === 'resistance' ? 'R' : 'S'}${lvl.strength}`,
        }),
      )
    }

    // Active swing high / low rays (like your TradingView screenshot)
    for (const ray of sr.swingRays) {
      const series = c.main.addSeries(LineSeries, {
        color: ray.color,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
        title: ray.type === 'resistance' ? 'R' : 'S',
      })
      series.setData(horizSegment(ray.fromTime, endTime, ray.price))
      c.srSeries.push(series)
      c.srPriceLines.push(
        c.candle.createPriceLine({
          price: ray.price,
          color: ray.color,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: ray.type === 'resistance' ? 'R' : 'S',
        }),
      )
    }

    if (lastPrice != null) {
      if (c.priceLine) c.candle.removePriceLine(c.priceLine)
      c.priceLine = c.candle.createPriceLine({
        price: lastPrice,
        color: '#ffca28',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: 'Last',
      })
    }
  }, [candles, lastPrice, evaluations, sr, contexts])

  return <div className="flex h-full min-h-0 w-full flex-col">
    <div className="flex flex-wrap gap-x-4 gap-y-1 px-3 py-1 text-xs text-slate-300" aria-live="polite">
      <span>Chart context · {latestContext?.label ?? 'Waiting for 20 completed minutes'}</span>
      {latestContext && <span>Last closed candle · {new Date((latestContext.time + 60) * 1000).toISOString().slice(11, 16)} UTC</span>}
      <span>Blue: prior 20 high (solid) / low (dashed) · descriptive only</span>
    </div>
    <div ref={mainRef} className="min-h-0 w-full flex-1" />
  </div>
}
