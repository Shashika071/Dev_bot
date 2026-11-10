import {
  evalR1, evalR2, evalR3, evalR4, evalR5, evalR6, evalR7, evalR8,
} from './signalStrategies'
import type { StrategyDefinition, StrategyId } from './types'

export const STRATEGY_REGISTRY_VERSION = 'research-strategies-v2'

export const STRATEGY_REGISTRY: StrategyDefinition[] = [
  {
    id: 'R1',
    name: 'Supertrend flip signal',
    family: 'supertrend',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: Supertrend ATR7×2 direction flip (bearish→bullish = RISE, bullish→bearish = FALL). Uses the ST-generated signal as the entry trigger.',
    evaluate: evalR1,
  },
  {
    id: 'R2',
    name: 'UT signal solo',
    family: 'ut',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: fresh UT Bot Alerts buy (RISE) or sell (FALL) — key=2, ATR=10. No other filters.',
    evaluate: evalR2,
  },
  {
    id: 'R3',
    name: 'UT + EMA50',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT buy/sell AND close on the correct side of a rising/falling EMA50.',
    evaluate: evalR3,
  },
  {
    id: 'R4',
    name: 'Supertrend + EMA50',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: Supertrend flip AND EMA50 confirmation (close above rising EMA50 for RISE; below falling for FALL).',
    evaluate: evalR4,
  },
  {
    id: 'R5',
    name: 'Supertrend + candle wick',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: Supertrend flip AND same-side wick rejection (pin bar: wick ≥2× body, close in outer third).',
    evaluate: evalR5,
  },
  {
    id: 'R6',
    name: 'UT + candle wick',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT buy/sell AND same-side wick rejection pin bar.',
    evaluate: evalR6,
  },
  {
    id: 'R7',
    name: 'Supertrend + wick + EMA50',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: Supertrend flip + same-side wick rejection + EMA50 confirmation.',
    evaluate: evalR7,
  },
  {
    id: 'R8',
    name: 'UT + wick + EMA50',
    family: 'combo',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT buy/sell + same-side wick rejection + EMA50 confirmation.',
    evaluate: evalR8,
  },
]

export function getStrategy(id: StrategyId): StrategyDefinition {
  const s = STRATEGY_REGISTRY.find((x) => x.id === id)
  if (!s) throw new Error(`Unknown strategy ${id}`)
  return s
}

export function assertRegistryComplete(): void {
  if (STRATEGY_REGISTRY.length !== 8) {
    throw new Error(`Expected 8 strategies, got ${STRATEGY_REGISTRY.length}`)
  }
  const ids = new Set(STRATEGY_REGISTRY.map((s) => s.id))
  if (ids.size !== 8) throw new Error('Duplicate strategy IDs')
  for (const id of ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8'] as StrategyId[]) {
    if (!ids.has(id)) throw new Error(`Missing strategy ${id}`)
  }
}
