import type { FrozenContractSettings } from './contractSettings'
import type { ResearchObservation, ResearchSide } from './strategies/types'
import type { PaperQuote } from './touchPaper'

type Job = { ids: string[]; side: ResearchSide; deadline: number }

/** Every accepted observation gets a result; same-tick/same-side quotes are shared. */
export class ResearchQuoteScheduler {
  private queue: Job[] = []
  private active = 0
  private cancelled = false
  private request: (side: ResearchSide) => Promise<PaperQuote | null>
  private result: (ids: string[], quote: PaperQuote) => void
  private now: () => number
  private concurrency: number

  constructor(
    request: (side: ResearchSide) => Promise<PaperQuote | null>,
    result: (ids: string[], quote: PaperQuote) => void,
    now: () => number = () => Date.now() / 1000,
    concurrency = 2,
  ) { this.request = request; this.result = result; this.now = now; this.concurrency = concurrency }

  enqueue(rows: ResearchObservation[], contract: FrozenContractSettings): void {
    const groups = new Map<string, Job>()
    for (const row of rows) {
      if (row.touch?.quoteStatus !== 'waiting') continue
      const key = `${row.signalEpoch}:${row.side}`
      const job = groups.get(key) ?? { ids: [], side: row.side, deadline: row.receiptEpoch + contract.maxQuoteAgeSec }
      job.ids.push(row.id)
      job.deadline = Math.min(job.deadline, row.receiptEpoch + contract.maxQuoteAgeSec)
      groups.set(key, job)
    }
    this.queue.push(...groups.values())
    this.drain()
  }

  cancel(): void { this.cancelled = true; this.queue = [] }

  private drain(): void {
    while (!this.cancelled && this.active < this.concurrency && this.queue.length) {
      const job = this.queue.shift()!
      if (this.now() > job.deadline) {
        this.result(job.ids, { ask: 0, payout: 0, responseEpoch: this.now(), rawError: 'Quote queue deadline exceeded' })
        continue
      }
      this.active++
      void this.run(job)
    }
  }

  private async run(job: Job): Promise<void> {
    let quote: PaperQuote | null = null
    try { quote = await this.request(job.side) }
    catch (error) { quote = { ask: 0, payout: 0, responseEpoch: this.now(), rawError: String(error) } }
    try {
      if (!this.cancelled) this.result(job.ids, quote ?? {
        ask: 0, payout: 0, responseEpoch: this.now(), rawError: 'No proposal returned',
      })
    } finally { this.active--; this.drain() }
  }
}
