import { createElement, type ComponentProps } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LiveEntryPanel } from './LiveEntryPanel'
import { computeLiveSetupConfidence } from '../lib/strategy/liveConfidence'
import type { EntryDisplay } from '../lib/strategy/entryDisplay'
import { emptyMultiTimeframeContext, type MultiTimeframeContext, type TimeframeCard } from '../lib/timeframes/timeframeContext'

type Props = ComponentProps<typeof LiveEntryPanel>
const baseEntry = (): EntryDisplay => ({
  state: 'WAIT', side: null, watchSide: 'RISE', reason: 'Waiting for a fresh C5 signal.',
  expiresInSeconds: 0, entryReference: null, target: null, signalPrice: null,
  drift: null, durationLabel: '9 minutes', tickAgeSeconds: 1,
})

function card(partial: Partial<TimeframeCard> & Pick<TimeframeCard, 'timeframe' | 'label'>): TimeframeCard {
  return {
    status: 'ready',
    statusText: 'Ready',
    closed: null,
    forming: null,
    ...partial,
  }
}

function tfFixture(over?: Partial<MultiTimeframeContext>): MultiTimeframeContext {
  const base = emptyMultiTimeframeContext('RISE')
  return {
    ...base,
    status: 'ready',
    statusText: 'Ready',
    m1Side: 'RISE',
    m5: card({
      timeframe: 5, label: '5m', status: 'ready', statusText: 'Ready',
      closed: {
        timeframe: 5, label: '5m', candleTime: 900, trend: 'Up', sma20: 100, atr14: 0.4,
        support: { type: 'support', mid: 99.5, hi: 99.7, lo: 99.3, distance: 'Near', present: true },
        resistance: { type: 'resistance', mid: 101, hi: 101.2, lo: 100.8, distance: 'Away', present: true },
        behavior: 'rejection_support', behaviorText: 'Last candle rejected support.',
        reactionSide: 'RISE', agreement: 'Supports',
        agreementReason: 'Uptrend. Last candle rejected support.',
      },
      forming: {
        timeframe: 5, openTime: 1200, closeInSeconds: 180, countdownLabel: '3m 00s',
        behavior: 'testing', text: 'M5 is testing support; 3m 00s left.',
        supportDistance: 'Near', resistanceDistance: 'Away', livePrice: 100.2,
      },
    }),
    m15: card({
      timeframe: 15, label: '15m', status: 'ready', statusText: 'Ready',
      closed: {
        timeframe: 15, label: '15m', candleTime: 0, trend: 'Up', sma20: 100, atr14: 0.8,
        support: { type: 'support', mid: 98, hi: 98.4, lo: 97.6, distance: 'Away', present: true },
        resistance: { type: 'resistance', mid: 100.5, hi: 100.8, lo: 100.2, distance: 'Near', present: true },
        behavior: 'no_clear_reaction', behaviorText: 'No clear level reaction on the last closed candle.',
        reactionSide: null, agreement: 'Mixed',
        agreementReason: 'Uptrend, but price is near resistance.',
      },
      forming: {
        timeframe: 15, openTime: 900, closeInSeconds: 252, countdownLabel: '4m 12s',
        behavior: 'testing', text: 'M15 is testing resistance; 4m 12s left.',
        supportDistance: 'Away', resistanceDistance: 'Near', livePrice: 100.2,
      },
    }),
    ...over,
  }
}

function fixture(): Props {
  const liveSetup = computeLiveSetupConfidence([])
  liveSetup.ready = true
  liveSetup.forming = true
  liveSetup.lean = 'RISE'
  liveSetup.rise.passed = 3
  liveSetup.rise.total = 4
  liveSetup.rise.requiredLabels = liveSetup.rise.requiredLabels.map((r, i) => ({
    ...r, ok: i < 3,
  }))
  return {
    market: { status: 'CONNECTED', ready: true, symbol: 'R_100', candles: [], lastPrice: 568.4,
      lastTickEpoch: 1201, lastConfirmedCandleTime: 1140, error: null },
    eval_: null,
    signal: { time: 1140, final: 'RISE', rawUt: 'BUY', confirmations: { ut: true, supertrend: true, sma: true, rsi: true, ao: true },
      failures: [], close: 568.39, stDir: 1, smaRising: true, smaFalling: false, ao: 1, rsi: 60,
      isQualified: true, strategyId: 'C5', requiredPassed: 4, requiredTotal: 4 },
    liveSetup, entry: baseEntry(), context: { time: 1200, high: 569, low: 567, label: 'Inside range', isClosed: false,
      locationPct: 70, description: 'Price is inside the range. Provisional: this candle is still forming.', chop14: 49 },
    timeframe: tfFixture(),
    barrierAbs: .01, sound: false, onToggleSound: () => {},
    quote: { available: false, reason: 'Quote unavailable' },
  }
}
const render = (props: Props) => renderToStaticMarkup(createElement(LiveEntryPanel, props))

describe('live entry panel financial states', () => {
  it('WAIT hides stale entry and target references, even if supplied', () => {
    const props = fixture()
    props.liveSetup.rise.passed = 0
    props.liveSetup.lean = 'NONE'
    props.entry = { ...baseEntry(), watchSide: null, entryReference: 123.45, target: 123.46, signalPrice: 123.44 }
    props.timeframe = { ...props.timeframe, m1Side: null }
    const html = render(props)
    expect(html).toContain('Waiting for a current setup')
    expect(html).toContain('Waiting for directional agreement')
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
    expect(html).toContain('Forming RISE 4/4')
    expect(html).toContain('Conditions on the forming candle can change.')
    expect(html).not.toContain('Completed candle · C5 4/4')
    expect(html).not.toContain('shows the confirmed signal')
    expect(html).toContain('The broker quote sets the actual entry and barrier.')
  })

  it('CONFIRMED checklist preserves the signal flags when the next forming candle disagrees', () => {
    const props = fixture()
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

  it('shows M5/M15 cards before 4/4 when M1 is RISE 3/4 Watching', () => {
    const props = fixture()
    // Exact 3/4 watch — RSI waiting
    props.liveSetup.rise.passed = 3
    props.liveSetup.rise.requiredLabels = [
      { key: 'ut', ok: true, text: 'Fresh UT BUY crossover' },
      { key: 'supertrend', ok: true, text: 'Supertrend bullish (ATR7×2)' },
      { key: 'sma', ok: true, text: 'Close > rising SMA20' },
      { key: 'rsi', ok: false, text: '52 < RSI14 < 70' },
    ]
    props.entry = { ...baseEntry(), state: 'WAIT', side: null, watchSide: 'RISE' }
    const closedM5 = props.timeframe.m5.closed!
    const closedM15 = props.timeframe.m15.closed!
    // Mutate forming wick — closed reading must remain in the rendered HTML
    props.timeframe = {
      ...props.timeframe,
      m5: {
        ...props.timeframe.m5,
        forming: {
          ...props.timeframe.m5.forming!,
          text: 'M5 is testing resistance; 1m 05s left.',
          closeInSeconds: 65,
          countdownLabel: '1m 05s',
        },
      },
    }
    const html = render(props)
    expect(html).toContain('M1 C5 · RISE 3/4 · Watching')
    expect(html).toContain('Waiting for RSI')
    expect(html).toContain('5m')
    expect(html).toContain('15m')
    expect(html).toContain('Supports RISE')
    expect(html).toContain('Mixed')
    expect(html).toContain('Uptrend. Last candle rejected support.')
    expect(html).toContain('Uptrend, but price is near resistance.')
    expect(html).toContain('Forming now')
    expect(html).toContain('M15 is testing resistance; 4m 12s left.')
    // Closed reasons preserved despite forming change
    expect(html).toContain(closedM5.agreementReason)
    expect(html).toContain(closedM15.agreementReason)
    expect(html).toContain('M5/M15 readings do not change C5 scoring')
    expect(html).toContain('Quote unavailable')
    expect(html).toContain('The decision remains yours.')
  })
})
