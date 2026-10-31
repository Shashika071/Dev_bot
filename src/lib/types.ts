export type ConnectionStatus = 'CONNECTED' | 'RECONNECTING' | 'DISCONNECTED'

export interface Candle {
  /** Candle open time (UTC epoch seconds), aligned to minute boundary */
  time: number
  open: number
  high: number
  low: number
  close: number
  /** true only after the minute has completed */
  isClosed: boolean
}

export interface Tick {
  epoch: number
  quote: number
  symbol: string
}

export type TrendDir = 1 | -1 | 0

export interface IndicatorSnapshot {
  time: number
  utTrail: number | null
  utBuy: boolean
  utSell: boolean
  stLine: number | null
  stDir: TrendDir
  sma20: number | null
  sma20Rising: boolean
  sma20Falling: boolean
  ao: number | null
  aoPrev: number | null
  rsi: number | null
}

export type FinalSignal = 'RISE' | 'FALL' | 'NO_TRADE'

export interface ConfirmationFlags {
  ut: boolean
  supertrend: boolean
  sma: boolean
  ao: boolean
  rsi: boolean
}

export interface SignalEvaluation {
  time: number
  final: FinalSignal
  rawUt: 'BUY' | 'SELL' | 'NONE'
  confirmations: ConfirmationFlags
  failures: string[]
  close: number
  stDir: TrendDir
  smaRising: boolean
  smaFalling: boolean
  ao: number | null
  rsi: number | null
  /** Fully qualified RISE/FALL only */
  isQualified: boolean
}

export interface SignalHistoryRow {
  id: string
  timestamp: number
  signal: FinalSignal | 'UT_BUY_REJECTED' | 'UT_SELL_REJECTED'
  candleClose: number
  notifyPrice: number | null
  stDir: TrendDir
  smaDir: 'BULLISH' | 'BEARISH' | 'FLAT'
  ao: number | null
  rsi: number | null
  connection: ConnectionStatus
  failures: string[]
  isQualified: boolean
  notifyEpoch?: number
  paper?: {
    entryEpoch: number
    entryPrice: number
    expiryEpoch: number
    lastTickEpoch: number
    status: 'PENDING' | 'WIN' | 'LOSS' | 'INVALID'
    exitEpoch?: number
    exitPrice?: number
    reason?: string
  }
}

export interface SRLevel {
  price: number
  type: 'support' | 'resistance'
  confirmTime: number
  touches: number
  broken: boolean
}
