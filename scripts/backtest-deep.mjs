/**
 * Deep backtest: fetch as much R_100 M1 history as possible,
 * run 5-rule strategy, check if price touches ±0.03 within NEXT 7 MINUTES.
 *
 * RISE touch: max(high of next 7 bars) - entry >= 0.03
 * FALL touch: entry - min(low of next 7 bars) >= 0.03
 *
 * NOT official Deriv 60s Rise/Fall settlement.
 */
import WebSocket from 'ws'

const SYMBOL = 'R_100'
const TOUCH = 0.03
const FORWARD_BARS = 7
const BATCH = 5000
const MAX_BATCHES = 20 // up to ~100k bars if API allows

function atrRma(highs, lows, closes, period) {
  const n = closes.length
  const tr = new Array(n)
  const atr = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    if (i === 0) tr[i] = highs[i] - lows[i]
    else {
      const pc = closes[i - 1]
      tr[i] = Math.max(highs[i] - lows[i], Math.abs(highs[i] - pc), Math.abs(lows[i] - pc))
    }
  }
  if (n < period) return atr
  let sum = 0
  for (let i = 0; i < period; i++) sum += tr[i]
  atr[period - 1] = sum / period
  const a = 1 / period
  for (let i = period; i < n; i++) atr[i] = atr[i - 1] + a * (tr[i] - atr[i - 1])
  return atr
}

function sma(values, period) {
  const out = new Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

function computeUtBot(candles, key = 2, period = 10) {
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr = atrRma(highs, lows, closes, period)
  const trail = new Array(candles.length).fill(null)
  const buy = new Array(candles.length).fill(false)
  const sell = new Array(candles.length).fill(false)
  for (let i = 0; i < candles.length; i++) {
    if (atr[i] == null) continue
    const nLoss = key * atr[i]
    const prev = i === 0 || trail[i - 1] == null ? 0 : trail[i - 1]
    const src = closes[i]
    const prevSrc = i > 0 ? closes[i - 1] : src
    if (src > prev && prevSrc > prev) trail[i] = Math.max(prev, src - nLoss)
    else if (src < prev && prevSrc < prev) trail[i] = Math.min(prev, src + nLoss)
    else if (src > prev) trail[i] = src - nLoss
    else trail[i] = src + nLoss
    if (i > 0 && trail[i] != null && trail[i - 1] != null) {
      if (closes[i - 1] <= trail[i - 1] && closes[i] > trail[i]) buy[i] = true
      if (closes[i - 1] >= trail[i - 1] && closes[i] < trail[i]) sell[i] = true
    }
  }
  return { buy, sell }
}

function computeSupertrend(candles, period = 7, mult = 2) {
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const closes = candles.map((c) => c.close)
  const atr = atrRma(highs, lows, closes, period)
  const line = new Array(candles.length).fill(null)
  const dir = new Array(candles.length).fill(0)
  const finalUb = new Array(candles.length).fill(null)
  const finalLb = new Array(candles.length).fill(null)
  for (let i = 0; i < candles.length; i++) {
    if (atr[i] == null) continue
    const hl2 = (highs[i] + lows[i]) / 2
    const basicUb = hl2 + mult * atr[i]
    const basicLb = hl2 - mult * atr[i]
    if (i === 0 || finalUb[i - 1] == null) {
      finalUb[i] = basicUb
      finalLb[i] = basicLb
    } else {
      finalUb[i] =
        basicUb < finalUb[i - 1] || closes[i - 1] > finalUb[i - 1] ? basicUb : finalUb[i - 1]
      finalLb[i] =
        basicLb > finalLb[i - 1] || closes[i - 1] < finalLb[i - 1] ? basicLb : finalLb[i - 1]
    }
    if (i === 0 || line[i - 1] == null) {
      dir[i] = 1
      line[i] = finalLb[i]
    } else if (line[i - 1] === finalUb[i - 1]) {
      if (closes[i] > finalUb[i]) {
        dir[i] = 1
        line[i] = finalLb[i]
      } else {
        dir[i] = -1
        line[i] = finalUb[i]
      }
    } else {
      if (closes[i] < finalLb[i]) {
        dir[i] = -1
        line[i] = finalUb[i]
      } else {
        dir[i] = 1
        line[i] = finalLb[i]
      }
    }
  }
  return { dir }
}

function computeRsi(closes, period = 14) {
  const rsi = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return rsi
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1]
    if (d >= 0) avgGain += d
    else avgLoss -= d
  }
  avgGain /= period
  avgLoss /= period
  rsi[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1]
    const gain = d > 0 ? d : 0
    const loss = d < 0 ? -d : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
  }
  return rsi
}

function evaluate(candles) {
  const ut = computeUtBot(candles)
  const st = computeSupertrend(candles)
  const closes = candles.map((c) => c.close)
  const sma20 = sma(closes, 20)
  const hl2 = candles.map((c) => (c.high + c.low) / 2)
  const aoFast = sma(hl2, 5)
  const aoSlow = sma(hl2, 34)
  const ao = candles.map((_, i) =>
    aoFast[i] != null && aoSlow[i] != null ? aoFast[i] - aoSlow[i] : null,
  )
  const rsi = computeRsi(closes, 14)
  const signals = []

  for (let i = 60; i < candles.length - FORWARD_BARS; i++) {
    const close = closes[i]
    const smaV = sma20[i]
    const smaPrev = sma20[i - 1]
    const smaRising = smaV != null && smaPrev != null && smaV > smaPrev
    const smaFalling = smaV != null && smaPrev != null && smaV < smaPrev
    const aoV = ao[i]
    const aoP = ao[i - 1]
    const rsiV = rsi[i]

    if (ut.buy[i] && !ut.sell[i]) {
      const ok =
        st.dir[i] === 1 &&
        smaV != null &&
        close > smaV &&
        smaRising &&
        aoV != null &&
        aoP != null &&
        aoV > 0 &&
        aoV > aoP &&
        rsiV != null &&
        rsiV > 52 &&
        rsiV < 70
      if (ok) signals.push({ i, side: 'RISE', time: candles[i].time, entry: close })
    } else if (ut.sell[i] && !ut.buy[i]) {
      const ok =
        st.dir[i] === -1 &&
        smaV != null &&
        close < smaV &&
        smaFalling &&
        aoV != null &&
        aoP != null &&
        aoV < 0 &&
        aoV < aoP &&
        rsiV != null &&
        rsiV < 48 &&
        rsiV > 30
      if (ok) signals.push({ i, side: 'FALL', time: candles[i].time, entry: close })
    }
  }
  return signals
}

function forwardTouch(candles, signal) {
  let bestHigh = -Infinity
  let bestLow = Infinity
  for (let k = 1; k <= FORWARD_BARS; k++) {
    const c = candles[signal.i + k]
    if (!c) break
    bestHigh = Math.max(bestHigh, c.high)
    bestLow = Math.min(bestLow, c.low)
  }
  if (signal.side === 'RISE') {
    const fav = bestHigh - signal.entry
    return { fav, touched: fav >= TOUCH }
  }
  const fav = signal.entry - bestLow
  return { fav, touched: fav >= TOUCH }
}

function fetchBatch(endEpoch) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket('wss://api.derivws.com/trading/v1/options/ws/public')
    const timer = setTimeout(() => {
      ws.close()
      reject(new Error('timeout'))
    }, 45000)
    ws.on('open', () => {
      ws.send(
        JSON.stringify({
          ticks_history: SYMBOL,
          style: 'candles',
          granularity: 60,
          count: BATCH,
          end: endEpoch,
          req_id: 1,
        }),
      )
    })
    ws.on('message', (raw) => {
      const msg = JSON.parse(String(raw))
      if (msg.error) {
        clearTimeout(timer)
        ws.close()
        reject(new Error(msg.error.message || JSON.stringify(msg.error)))
        return
      }
      if (msg.candles) {
        clearTimeout(timer)
        ws.close()
        resolve(
          msg.candles.map((c) => ({
            time: Number(c.epoch),
            open: Number(c.open),
            high: Number(c.high),
            low: Number(c.low),
            close: Number(c.close),
          })),
        )
      }
    })
    ws.on('error', (e) => {
      clearTimeout(timer)
      reject(e)
    })
  })
}

async function fetchAllHistory() {
  const map = new Map()
  let end = Math.floor(Date.now() / 1000)
  for (let b = 0; b < MAX_BATCHES; b++) {
    process.stdout.write(`Downloading batch ${b + 1}/${MAX_BATCHES} (end=${end})...\n`)
    let batch
    try {
      batch = await fetchBatch(end)
    } catch (e) {
      console.log(`Stop download: ${e.message}`)
      break
    }
    if (!batch.length) break
    const before = map.size
    for (const c of batch) map.set(c.time, c)
    const added = map.size - before
    console.log(`  got ${batch.length} candles, unique total=${map.size} (+${added})`)
    if (added === 0) break
    const earliest = Math.min(...batch.map((c) => c.time))
    const nextEnd = earliest - 60
    if (nextEnd >= end) break
    end = nextEnd
    await new Promise((r) => setTimeout(r, 400))
  }
  return [...map.values()].sort((a, b) => a.time - b.time)
}

const candles = await fetchAllHistory()
if (candles.length < 200) {
  console.error('Not enough candles')
  process.exit(1)
}

const signals = evaluate(candles)
let rise = 0
let fall = 0
let touchYes = 0
let touchNo = 0
let sumFav = 0
const byDay = new Map()

for (const s of signals) {
  if (s.side === 'RISE') rise++
  else fall++
  const { fav, touched } = forwardTouch(candles, s)
  sumFav += fav
  if (touched) touchYes++
  else touchNo++
  const day = new Date(s.time * 1000).toISOString().slice(0, 10)
  const d = byDay.get(day) || { n: 0, touch: 0, rise: 0, fall: 0 }
  d.n++
  if (touched) d.touch++
  if (s.side === 'RISE') d.rise++
  else d.fall++
  byDay.set(day, d)
}

const hours = ((candles[candles.length - 1].time - candles[0].time) / 3600).toFixed(1)
const days = (hours / 24).toFixed(1)

console.log('')
console.log('========== DEEP BACKTEST ==========')
console.log(`Symbol: ${SYMBOL} | M1 | Strategy: UT+ST+SMA20+AO+RSI`)
console.log(
  `History: ${new Date(candles[0].time * 1000).toISOString()} → ${new Date(candles[candles.length - 1].time * 1000).toISOString()}`,
)
console.log(`Candles: ${candles.length} (~${hours} hours / ~${days} days)`)
console.log(`Forward window: next ${FORWARD_BARS} minutes`)
console.log(`Touch rule: favorable excursion ≥ ${TOUCH}`)
console.log('')
console.log(`Qualified signals: ${signals.length}`)
console.log(`  RISE: ${rise}`)
console.log(`  FALL: ${fall}`)
console.log('')
console.log(`Touched ±${TOUCH} within next ${FORWARD_BARS} min:`)
console.log(`  YES: ${touchYes}`)
console.log(`  NO:  ${touchNo}`)
console.log(
  `  Rate: ${signals.length ? ((100 * touchYes) / signals.length).toFixed(2) : 0}%`,
)
console.log(
  `  Avg favorable excursion: ${signals.length ? (sumFav / signals.length).toFixed(4) : 0}`,
)
console.log('')
console.log('Per-day breakdown:')
for (const [day, d] of [...byDay.entries()].sort()) {
  const rate = d.n ? ((100 * d.touch) / d.n).toFixed(0) : '0'
  console.log(
    `  ${day}  signals=${d.n} (R${d.rise}/F${d.fall})  touch=${d.touch}/${d.n} (${rate}%)`,
  )
}
console.log('')
console.log('NOTE: This measures wick touch vs signal candle close over next 7 M1 bars.')
console.log('It is NOT Deriv official 60-second Rise/Fall contract P/L.')
