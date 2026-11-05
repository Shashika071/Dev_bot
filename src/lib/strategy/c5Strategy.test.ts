import { describe, expect, it } from 'vitest'
import { enrich } from './signalEngine'
import {
  C5_REQUIRED,
  evaluateC5Index,
  evaluateC5LatestClosed,
} from './c5Strategy'
import { evalMask, MASKS } from '../research/strategies/maskStrategies'
import { buildResearchExtras } from '../research/strategies/buildExtras'
import type { StrategyContext } from '../research/strategies/types'
import { fixtureCandles } from '../testFixtures'

function seriesFor(side: 'RISE' | 'FALL', aoAgree = true) {
  const s = enrich(fixtureCandles(120))
  const i = s.candles.length - 1
  const up = side === 'RISE'
  s.utBuy[i] = up
  s.utSell[i] = !up
  s.stDir[i] = up ? 1 : -1
  s.candles[i]!.isClosed = true
  s.sma20[i] = s.candles[i]!.close + (up ? -1 : 1)
  s.smaRising[i] = up
  s.smaFalling[i] = !up
  s.rsi[i] = up ? 60 : 40
  s.ao[i] = aoAgree ? (up ? 1 : -1) : (up ? -1 : 1)
  s.ao[i - 1] = aoAgree ? (up ? 0.5 : -0.5) : (up ? -0.5 : 0.5)
  return { s, i }
}

describe('live C5 vs research C5', () => {
  it('agrees with research MASKS.C5 on identical enriched inputs', () => {
    for (const side of ['RISE', 'FALL'] as const) {
      const { s, i } = seriesFor(side, false)
      const live = evaluateC5Index(s, i)
      const ctx: StrategyContext = {
        candles: s.candles,
        closedCount: s.candles.length,
        index: i,
        series: s,
        extras: buildResearchExtras(s.candles),
        feedHealthy: true,
        nowEpoch: s.candles[i]!.time + 60,
      }
      const research = evalMask(ctx, MASKS.C5!, 'C5')
      expect(MASKS.C5).toEqual([...C5_REQUIRED])
      expect(live.final).toBe(side)
      expect(research?.side).toBe(side)
      expect(live.confirmations.ao).toBe(false)
      expect(live.isQualified).toBe(true)
    }
  })

  it('AO disagreement does not block qualification', () => {
    for (const side of ['RISE', 'FALL'] as const) {
      const { s, i } = seriesFor(side, false)
      const ev = evaluateC5Index(s, i)
      expect(ev.final).toBe(side)
      expect(ev.confirmations.ao).toBe(false)
      expect(ev.requiredPassed).toBe(4)
    }
  })

  it('missing fresh UT or any required condition blocks entry', () => {
    for (const key of ['ut', 'supertrend', 'sma', 'rsi'] as const) {
      const { s, i } = seriesFor('RISE')
      if (key === 'ut') { s.utBuy[i] = false; s.utSell[i] = false }
      if (key === 'supertrend') s.stDir[i] = 0
      if (key === 'sma') s.smaRising[i] = false
      if (key === 'rsi') s.rsi[i] = 50
      expect(evaluateC5Index(s, i).isQualified, key).toBe(false)
    }
  })

  it.each([['RISE', 52], ['RISE', 70], ['FALL', 30], ['FALL', 48]] as const)(
    'excludes RSI boundary %s=%s',
    (side, rsi) => {
      const { s, i } = seriesFor(side)
      s.rsi[i] = rsi
      expect(evaluateC5Index(s, i).isQualified).toBe(false)
    },
  )

  it('forming / incomplete candles never qualify at that index', () => {
    const { s, i } = seriesFor('RISE')
    s.candles[i]!.isClosed = false
    expect(evaluateC5Index(s, i).isQualified).toBe(false)
  })

  it('evaluateC5LatestClosed ignores a trailing forming bar', () => {
    const closed = fixtureCandles(80)
    const withForming = [
      ...closed,
      { ...closed.at(-1)!, time: closed.at(-1)!.time + 60, isClosed: false },
    ]
    const latestClosed = evaluateC5LatestClosed(closed)
    const withOpen = evaluateC5LatestClosed(withForming)
    expect(withOpen?.time).toBe(latestClosed?.time ?? undefined)
    expect(withOpen?.time).not.toBe(withForming.at(-1)!.time)
  })
})
