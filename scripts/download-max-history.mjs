import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { PublicHistory } from './public-history.mjs'

const dir = resolve('research-output/touch-003')
await mkdir(resolve(dir, 'pages'), { recursive: true })
const manifestPath = resolve(dir, 'download-manifest.json')
let manifest
try { manifest = JSON.parse(await readFile(manifestPath, 'utf8')) } catch { /* New download. */ }
const api = new PublicHistory()
const delay = (ms) => new Promise((r) => setTimeout(r, ms))

async function retry(payload) {
  for (let attempt = 0; ; attempt++) {
    try { return await api.request(payload) } catch (error) {
      if (attempt >= 3) throw error
      console.log(`Retry ${attempt + 1}: ${error.message}`)
      if (/rate.?limit/i.test(`${error.code} ${error.message}`)) {
        // Respect the server's request budget rather than reconnecting around it.
        await delay(30000 * (attempt + 1))
      } else {
        api.close()
        await delay(1000 * (attempt + 1))
      }
    }
  }
}
try {
  if (!manifest) {
    const { time } = await retry({ time: 1 })
    if (!Number.isFinite(time)) throw new Error('Invalid server time')
    manifest = { symbol: 'R_100', granularity: 60, asOfEpoch: time,
      endExclusive: Math.floor(time / 60) * 60, pages: [], bars: 0, complete: false }
  }
  if (!manifest.complete) {
    let end = manifest.pages.length ? manifest.pages.at(-1).first - 1 : manifest.endExclusive - 1
    while (end >= 0) {
      const payload = { ticks_history: 'R_100', style: 'candles', granularity: 60, count: 5000, end: String(end) }
      const response = await retry(payload)
      const raw = response.candles
      if (!Array.isArray(raw)) throw new Error('Missing candle array')
      const candles = raw.map((c) => ({ time: Number(c.epoch), open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close), isClosed: true }))
      // Retention can cut through a minute; never invent the missing beginning of that candle.
      if (candles[0]?.time % 60 !== 0 && candles[0]?.time <= end
        && (candles.length === 1 || candles[1].time === Math.floor(candles[0].time / 60) * 60 + 60)) {
        const partial = candles.shift()
        manifest.excludedBoundaryCandles ??= []
        manifest.excludedBoundaryCandles.push(partial)
        console.log(`Excluded incomplete boundary minute at ${new Date(partial.time * 1000).toISOString()}`)
      }
      if (candles.some((c, i) => ![c.time, c.open, c.high, c.low, c.close].every(Number.isFinite)
        || c.time % 60 !== 0 || c.low <= 0 || c.low > Math.min(c.open, c.close)
        || c.high < Math.max(c.open, c.close) || (i > 0 && c.time <= candles[i - 1].time))) {
        throw new Error('Malformed, duplicate or unsorted candles')
      }
      if (!candles.length || candles.some((c) => c.time > end || c.time + 60 > manifest.endExclusive)) {
        // Confirm once with explicit start and one requested candle. Never count fallback recent data.
        const confirmation = await retry({ ...payload, count: 1, start: Math.max(0, end - 59) })
        const valid = confirmation.candles?.filter((c) => Number(c.epoch) % 60 === 0
          && Number(c.epoch) <= end && Number(c.epoch) >= Math.floor(end / 60) * 60)
        if (valid?.length) {
          throw new Error('Boundary page rejected but one-minute probe succeeds; extend boundary recovery before concluding')
        }
        manifest.complete = true
        manifest.stop = {
          requestedEnd: end, reason: candles.length ? 'API returned candles newer than requested end; confirmed with explicit one-minute request' : 'No complete older minute remains; explicit one-minute probe returned no complete candle',
          returnedFirst: candles[0]?.time, returnedLast: candles.at(-1)?.time,
          confirmationFirst: confirmation.candles?.[0]?.epoch, confirmationLast: confirmation.candles?.at(-1)?.epoch,
        }
        break
      }
      const name = `pages/${String(manifest.pages.length).padStart(5, '0')}.json`
      await writeFile(resolve(dir, name), JSON.stringify(candles))
      manifest.pages.push({ file: name, first: candles[0].time, last: candles.at(-1).time, bars: candles.length, requestedEnd: end })
      manifest.bars += candles.length
      end = candles[0].time - 1
      await writeFile(manifestPath, JSON.stringify(manifest, null, 2))
      if (manifest.pages.length % 20 === 0) console.log(`${manifest.bars} candles; reached ${new Date(candles[0].time * 1000).toISOString()}`)
      await delay(500)
    }
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2))
  }
  console.log(JSON.stringify({ bars: manifest.bars, pages: manifest.pages.length, oldest: manifest.pages.at(-1)?.first, latestExclusive: manifest.endExclusive, stop: manifest.stop }))
} finally { api.close() }
