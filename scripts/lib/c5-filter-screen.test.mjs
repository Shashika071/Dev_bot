import test from 'node:test'
import assert from 'node:assert/strict'
import { auditCandles, chartContext, relativeContext, observeOpportunity, summarize, dayBlockIntervals } from './c5-filter-screen.mjs'

const bar = (i, values = {}) => ({ time: i * 60, open: 100, high: 100.1, low: 99.9, close: 100, isClosed: true, ...values })
test('entry references next open and excludes signal candle extremes', () => {
  const candles = [bar(0, { high: 200 }), ...Array.from({ length: 9 }, (_, i) => bar(i + 1, { open: 101, high: 101.005, low: 100.9, close: 101 }))]
  const row = observeOpportunity(candles, 0, 'RISE')
  assert.equal(row.hit, false)
  assert.equal(row.oppositeHit, true)
  assert.equal(row.signalCloseHit, true)
  assert.equal(row.entry, 101)
  assert.equal(row.outcomeEndTime, 600)
})
test('includes exact barrier and ninth minute, excludes tenth minute', () => {
  const candles = Array.from({ length: 11 }, (_, i) => bar(i, { high: 100.005, low: 100 }))
  candles[10].high = 101
  assert.equal(observeOpportunity(candles, 0, 'RISE').hit, false)
  candles[9].high = 100.01
  assert.equal(observeOpportunity(candles, 0, 'RISE').firstTouchMinute, 9)
  candles[9].low = 99.99
  assert.equal(observeOpportunity(candles, 0, 'FALL').hit, true)
})
test('missing, invalid or unfinished outcome is excluded', () => {
  const candles = Array.from({ length: 10 }, (_, i) => bar(i))
  assert.equal(observeOpportunity(candles.slice(0, 9), 0, 'RISE'), null)
  candles[5].time += 60
  assert.equal(observeOpportunity(candles, 0, 'RISE'), null)
  candles[5] = bar(5, { isClosed: false })
  assert.equal(observeOpportunity(candles, 0, 'RISE'), null)
  assert.throws(() => auditCandles(candles))
})
test('prior channel excludes signal and context never reads future bars', () => {
  const candles = Array.from({ length: 21 }, (_, i) => bar(i))
  candles[20] = bar(20, { high: 101, close: 100.5 })
  const context = chartContext(candles, 20)
  assert.equal(context.upper, 100.1)
  assert.equal(context.label, 'UP_BREAKOUT')
  assert.equal(relativeContext(context, 'RISE'), 'ALIGNED_BREAKOUT')
  assert.deepEqual(chartContext([...candles, bar(21, { high: 200 })], 20), context)
  candles[20].close = 100
  assert.equal(chartContext(candles, 20).label, 'UPPER_REJECTION')
  candles[20].low = 99
  assert.equal(chartContext(candles, 20).label, 'BOTH_REJECTIONS')
})
test('same-time random-side baseline is not assumed to be 50 percent', () => {
  const rows = [
    { hit: true, oppositeHit: true, bothHit: true, confirmedTime: 60, passes: ['BASELINE', 'X'] },
    { hit: false, oppositeHit: true, bothHit: false, confirmedTime: 120, passes: ['BASELINE'] },
  ]
  assert.equal(summarize(rows).randomSideRate, .75)
  assert.equal(summarize(rows).edgeOverRandomSide, -.25)
  assert.deepEqual(dayBlockIntervals(rows, 'X', 20).deltaVsC5, [.5, .5])
  assert.deepEqual(dayBlockIntervals(rows, 'X', 20).edgeOverRandomSide, [0, 0])
})
