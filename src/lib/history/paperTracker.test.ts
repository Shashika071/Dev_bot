import { describe, expect, it } from 'vitest'
import { advancePaper, startPaper } from './paperTracker'
import type { SignalHistoryRow } from '../types'

function row(side: 'RISE' | 'FALL' = 'RISE'): SignalHistoryRow {
  return {
    id: 'test', timestamp: 0, signal: side, candleClose: 99, notifyPrice: 100,
    stDir: 1, smaDir: 'BULLISH', ao: 1, rsi: 60, connection: 'CONNECTED',
    failures: [], isQualified: true, paper: startPaper(60, 100),
  }
}
function run(side: 'RISE' | 'FALL', exit: number, end = 120) {
  let rows = [row(side)]
  for (let epoch = 62; epoch <= 118; epoch += 2) {
    rows = advancePaper(rows, { epoch, quote: 110, symbol: 'R_100' }, true)
  }
  return advancePaper(rows, { epoch: end, quote: exit, symbol: 'R_100' }, true)[0].paper!
}

describe('paper outcome tracking', () => {
  it.each([['RISE', 101, 'WIN'], ['RISE', 99, 'LOSS'], ['FALL', 99, 'WIN'], ['FALL', 101, 'LOSS'], ['RISE', 100, 'LOSS'], ['FALL', 100, 'LOSS']] as const)('%s at %s is %s', (side, price, status) => {
    const result = run(side, price)
    expect(result.status).toBe(status)
    expect(result.entryPrice).toBe(100)
    expect(result.exitEpoch).toBe(120)
    expect(result.exitPrice).toBe(price)
  })
  it('uses first expiry tick with a bounded tolerance', () => {
    expect(run('RISE', 101, 123).status).toBe('WIN')
    expect(run('RISE', 101, 126).status).toBe('INVALID')
  })
  it('does not settle from a favorable touch before expiry', () => {
    expect(advancePaper([row()], { epoch: 62, quote: 120, symbol: 'R_100' }, true)[0].paper?.status).toBe('PENDING')
    expect(run('RISE', 99).status).toBe('LOSS')
  })
  it('invalidates gaps, reloads and disconnects instead of guessing results', () => {
    expect(advancePaper([row()], null, false)[0].paper?.status).toBe('INVALID')
    expect(advancePaper([row()], { epoch: 80, quote: 120, symbol: 'R_100' }, true)[0].paper?.status).toBe('INVALID')
  })
  it('ignores duplicate/older ticks and cannot resettle a final outcome', () => {
    const rows = [row()]
    expect(advancePaper(rows, { epoch: 59, quote: 120, symbol: 'R_100' }, true)).toBe(rows)
    const settled = [{ ...row(), paper: run('RISE', 101) }]
    expect(advancePaper(settled, { epoch: 122, quote: 50, symbol: 'R_100' }, true)).toBe(settled)
  })
})
