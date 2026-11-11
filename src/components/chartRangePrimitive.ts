import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts'

export interface ChartRange {
  startTime: number
  endTime: number
  high: number
  low: number
}

type Rectangle = { left: number; right: number; top: number; bottom: number }

/** A descriptive recent-range backdrop. It never expands the price autoscale. */
export class ChartRangePrimitive implements ISeriesPrimitive<Time> {
  private range: ChartRange | null = null
  private attachment: SeriesAttachedParameter<Time> | null = null
  private rectangle: Rectangle | null = null
  private readonly view: IPrimitivePaneView
  private readonly views: readonly IPrimitivePaneView[]

  constructor(range: ChartRange | null = null) {
    const renderer: IPrimitivePaneRenderer = {
      draw: target => {
        const rectangle = this.rectangle
        if (!rectangle) return
        target.useBitmapCoordinateSpace(scope => {
          const { context, bitmapSize, horizontalPixelRatio: hx, verticalPixelRatio: vy } = scope
          const left = Math.max(0, Math.min(bitmapSize.width, rectangle.left * hx))
          const right = Math.max(0, Math.min(bitmapSize.width, rectangle.right * hx))
          const top = Math.max(0, Math.min(bitmapSize.height, rectangle.top * vy))
          const bottom = Math.max(0, Math.min(bitmapSize.height, rectangle.bottom * vy))
          if (right <= left || bottom <= top) return
          context.save()
          context.fillStyle = 'rgba(56, 189, 248, 0.09)'
          context.fillRect(left, top, right - left, bottom - top)
          context.restore()
        })
      },
    }
    this.view = { zOrder: () => 'bottom', renderer: () => renderer }
    this.views = [this.view]
    this.setRange(range)
  }

  setRange(range: ChartRange | null): void {
    this.range = range
      && [range.startTime, range.endTime, range.high, range.low].every(Number.isFinite)
      && range.endTime >= range.startTime && range.high > range.low && range.low > 0
      ? { ...range }
      : null
    this.updateAllViews()
    this.attachment?.requestUpdate()
  }

  attached(attachment: SeriesAttachedParameter<Time>): void {
    this.attachment = attachment
    this.updateAllViews()
    attachment.requestUpdate()
  }

  detached(): void {
    this.attachment = null
    this.rectangle = null
  }

  updateAllViews(): void {
    this.rectangle = null
    if (!this.range || !this.attachment) return
    const { chart, series } = this.attachment
    const timeScale = chart.timeScale()
    const start = timeScale.timeToCoordinate(this.range.startTime as Time)
    const end = timeScale.timeToCoordinate(this.range.endTime as Time)
    const high = series.priceToCoordinate(this.range.high)
    const low = series.priceToCoordinate(this.range.low)
    if (start == null || end == null || high == null || low == null
      || ![start, end, high, low].every(Number.isFinite)) return
    const halfBar = timeScale.options().barSpacing / 2
    const left = Math.min(start, end) - halfBar
    const right = Math.max(start, end) + halfBar
    if (right <= 0 || left >= timeScale.width()) return
    this.rectangle = { left, right, top: Math.min(high, low), bottom: Math.max(high, low) }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views
  }
}
