import { createElement, type ComponentProps } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LiveEntryPanel } from './LiveEntryPanel'
import { computeLiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { EntryDisplay } from '../lib/strategy/entryDisplay'

type Props = ComponentProps<typeof LiveEntryPanel>
const baseEntry = (): EntryDisplay => ({
  state: 'WAIT', side: null, watchSide: 'RISE', reason: 'Waiting for a fresh C5 signal.',
  expiresInSeconds: 0, entryReference: null, target: null, signalPrice: null,
  drift: null, durationLabel: '9 minutes', tickAgeSeconds: 1,
})
function fixture(): Props {
  const liveSetup = computeLiveSetupConfidence([])
  liveSetup.ready = true
  liveSetup.forming = true
  liveSetup.lean = 'RISE'
  return {
    market: { status: 'CONNECTED', ready: true, symbol: 'R_100', candles: [], lastPrice: 568.4,
      lastTickEpoch: 1201, lastConfirmedCandleTime: 1140, error: null },
    eval_: null,
    signal: { time: 1140, final: 'RISE', rawUt: 'BUY', confirmations: { ut: true, supertrend: true, sma: true, rsi: true, ao: true },
      failures: [], close: 568.39, stDir: 1, smaRising: true, smaFalling: false, ao: 1, rsi: 60,
      isQualified: true, strategyId: 'C5', requiredPassed: 4, requiredTotal: 4 },
    liveSetup, entry: baseEntry(), context: { time: 1200, high: 569, low: 567, label: 'Inside range', isClosed: false,
      locationPct: 70, description: 'Price is inside the range. Provisional: this candle is still forming.', chop14: 49 },
    barrierAbs: .01, sound: false, onToggleSound: () => {},
  }
}
const render = (props: Props) => renderToStaticMarkup(createElement(LiveEntryPanel, props))

describe('live entry panel financial states', () => {
  it('WAIT hides stale entry and target references, even if supplied', () => {
    const props = fixture()
    props.entry = { ...baseEntry(), entryReference: 123.45, target: 123.46, signalPrice: 123.44 }
    const html = render(props)
    expect(html).toContain('Waiting for a current setup')
    expect(html).toContain('Watching RISE · not an entry')
    expect(html).not.toContain('Entry reference')
    expect(html).not.toContain('Indicative touch level')
    expect(html).not.toContain('123.45')
    expect(html).not.toContain('123.46')
  })

  it('FORMING is explicitly provisional and never described as completed or confirmed', () => {
    const props = fixture()
    props.entry = { ...baseEntry(), state: 'FORMING', side: 'RISE', entryReference: 568.4, target: 568.41,
      expiresInSeconds: 8, reason: 'All four conditions currently agree. This candle is still forming and can change.' }
    const html = render(props)
    expect(html).toContain('Forming candle · C5 4/4')
    expect(html).toContain('still forming and can change')
    expect(html).not.toContain('Completed candle · C5 4/4')
    expect(html).not.toContain('shows the confirmed signal')
    expect(html).toContain('The broker quote sets the actual entry and barrier.')
  })

  it('CONFIRMED checklist preserves the signal flags when the next forming candle disagrees', () => {
    const props = fixture()
    // Empty-side fixtures have all current checks false and current score 0/4.
    props.entry = { ...baseEntry(), state: 'CONFIRMED', side: 'RISE', expiresInSeconds: 8,
      reason: 'All four conditions agreed at the candle close.' }
    const html = render(props)
    expect(html).toContain('Completed candle · C5 4/4')
    expect(html).toContain('The checklist shows the confirmed signal. Scores below track the current candle.')
    expect(html.match(/✓/g)).toHaveLength(4)
    expect(html).toContain('0/4')
    expect(html).not.toContain('Fresh UT BUY crossover · waiting')
    expect(html).toContain('Optional AO agrees')
  })

  it('marks provisional range data and stale feed without claiming live prices', () => {
    const props = fixture()
    props.entry.tickAgeSeconds = 11
    const html = render(props)
    expect(html).toContain('● Paused')
    expect(html).not.toContain('● Live')
    expect(html).toContain('Inside range · forming')
    expect(html).toContain('Provisional: this candle is still forming.')
    expect(html).toContain('Last available range · waiting for fresh prices.')
    expect(html).toContain('Describes recent movement; does not choose a direction.')
  })

  it('uses configured duration, barrier and directional reference without claiming execution', () => {
    const props = fixture()
    props.barrierAbs = .07
    props.entry = { ...baseEntry(), state: 'FORMING', side: 'FALL', entryReference: 568.4,
      target: 568.33, signalPrice: 568.42, expiresInSeconds: 7, durationLabel: '12 minutes' }
    const html = render(props)
    expect(html).toContain('FALL setup')
    expect(html).toContain('568.33')
    expect(html).toContain('−0.07 · 12 minutes')
    expect(html).toContain('Entry reference')
    expect(html).toContain('Indicative touch level')
    expect(html).toContain('4/4 counts conditions, not win probability.')
  })
})
