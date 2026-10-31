import { PublicHistory } from './public-history.mjs'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
const api = new PublicHistory()
const results = []
try {
  if (process.argv.includes('--boundary')) {
    const manifest = JSON.parse(await readFile('research-output/touch-003/download-manifest.json', 'utf8'))
    const end = manifest.pages.at(-1).first - 1
    const response = await api.request({ ticks_history: 'R_100', style: 'candles', granularity: 60, count: 5000, end: String(end) })
    await writeFile('research-output/touch-003/boundary-response.json', JSON.stringify(response))
    const invalid = response.candles?.filter((c, i, all) => ![c.epoch, c.open, c.high, c.low, c.close].map(Number).every(Number.isFinite)
      || c.epoch % 60 !== 0 || c.low <= 0 || c.low > Math.min(c.open, c.close) || c.high < Math.max(c.open, c.close)
      || (i > 0 && Number(c.epoch) <= Number(all[i - 1].epoch)))
    console.log(JSON.stringify({ end, count: response.candles?.length, echo: response.echo_req, error: response.error,
      first: response.candles?.slice(0, 3), last: response.candles?.slice(-3), invalidCount: invalid?.length, invalid: invalid?.slice(0, 5) }, null, 2))
    process.exitCode = 0
  } else {
  const { time } = await api.request({ time: 1 })
  for (const days of [180, 200, 240, 270, 300, 330, 360, 364]) {
    const end = Math.floor(time / 60) * 60 - 1 - days * 86400
    try {
      const msg = await api.request({ ticks_history: 'R_100', style: 'candles', granularity: 60, count: 5000, end: String(end) })
      const result = { days, requestedEnd: new Date(end * 1000).toISOString(), echo: msg.echo_req, count: msg.candles?.length,
        first: msg.candles?.[0]?.epoch, last: msg.candles?.at(-1)?.epoch }
      results.push(result)
      console.log(JSON.stringify(result))
    } catch (e) {
      const result = { days, requestedEnd: new Date(end * 1000).toISOString(), error: e.message, code: e.code }
      results.push(result)
      console.log(JSON.stringify(result))
    }
  }
  await mkdir('research-output/touch-003', { recursive: true })
  await writeFile('research-output/touch-003/availability-boundary-probes.json', JSON.stringify({ asOfEpoch: time, results }, null, 2))
  }
} finally { api.close() }
