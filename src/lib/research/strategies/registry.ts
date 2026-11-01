import { evalMask, evalMinPassed, MASKS } from './maskStrategies'
import {
  evalS1, evalS2, evalS3, evalS4, evalS5, evalS6, evalS7,
} from './patternStrategies'
import { evalT1, evalT2, evalT3 } from './statStrategies'
import { evalN1, evalN2, evalO1, evalO2 } from './oscillatorStrategies'
import type { StrategyDefinition, StrategyId } from './types'

export const STRATEGY_REGISTRY_VERSION = 'research-strategies-v1'

export const STRATEGY_REGISTRY: StrategyDefinition[] = [
  {
    id: 'B0',
    name: 'Current live baseline (forming ≥80%)',
    family: 'baseline_live',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: true,
    rules: 'Forming M1: ≥4/5 of UT, Supertrend, SMA20, AO, RSI for one side (same definitions as live bot). 80% is a condition score, not win probability.',
    evaluate: (ctx) => evalMinPassed(ctx, 4),
  },
  {
    id: 'B1',
    name: 'Closed-candle baseline (≥80%)',
    family: 'baseline_closed',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1 only: ≥4/5 of the five confirmation conditions for one side.',
    evaluate: (ctx) => evalMinPassed(ctx, 4),
  },
  {
    id: 'C1',
    name: 'Full confirmation (5/5)',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: require UT + Supertrend + SMA + AO + RSI. Omitted none.',
    evaluate: (ctx) => evalMask(ctx, MASKS.C1!, 'C1 full 5/5'),
  },
  {
    id: 'C2',
    name: 'Without UT',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: Supertrend + SMA + AO + RSI (UT ignored, not required to fail).',
    evaluate: (ctx) => evalMask(ctx, MASKS.C2!, 'C2 without UT'),
  },
  {
    id: 'C3',
    name: 'Without Supertrend',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT + SMA + AO + RSI.',
    evaluate: (ctx) => evalMask(ctx, MASKS.C3!, 'C3 without Supertrend'),
  },
  {
    id: 'C4',
    name: 'Without SMA',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT + Supertrend + AO + RSI.',
    evaluate: (ctx) => evalMask(ctx, MASKS.C4!, 'C4 without SMA'),
  },
  {
    id: 'C5',
    name: 'Without AO',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT + Supertrend + SMA + RSI.',
    evaluate: (ctx) => evalMask(ctx, MASKS.C5!, 'C5 without AO'),
  },
  {
    id: 'C6',
    name: 'Without RSI',
    family: 'confirmation_mask',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Completed M1: UT + Supertrend + SMA + AO.',
    evaluate: (ctx) => evalMask(ctx, MASKS.C6!, 'C6 without RSI'),
  },
  {
    id: 'S1',
    name: 'EMA/RSI trend resumption',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'RISE: EMA20>EMA50, EMA50 rising, close>EMA20, RSI crosses ≤50→>50. Mirror for FALL.',
    evaluate: evalS1,
  },
  {
    id: 'S2',
    name: '20-candle range breakout',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Close beyond prior 20 completed candles high/low EXCLUDING signal candle.',
    evaluate: evalS2,
  },
  {
    id: 'S3',
    name: 'Bollinger/RSI reversal',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Prev close outside band + extreme RSI; current close back inside contemporaneous bands (pop. stdev).',
    evaluate: evalS3,
  },
  {
    id: 'S4',
    name: 'Engulfing + EMA50',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Real-body engulfing with ≥1 strict boundary; close vs rising/falling EMA50.',
    evaluate: evalS4,
  },
  {
    id: 'S5',
    name: 'Wick rejection at boundary',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Sweep prior 20-range extreme (excl. signal), close back inside; wick rules; reject zero body/range.',
    evaluate: evalS5,
  },
  {
    id: 'S6',
    name: 'Three-return continuation',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    pairedWith: 'S7',
    rules: 'Three consecutive strictly positive (RISE) or negative (FALL) close-to-close changes; zero breaks streak.',
    evaluate: evalS6,
  },
  {
    id: 'S7',
    name: 'Three-return reversal',
    family: 'pattern',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    pairedWith: 'S6',
    rules: 'Same S6 opportunities, opposite side. Shares cooldown schedule with S6.',
    evaluate: evalS7,
  },
  {
    id: 'T1',
    name: 'Return-shock reversal',
    family: 'statistical',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    pairedWith: 'T2',
    rules: 'z of current log-return vs prior 120 pop. mean/std; RISE z≤-2, FALL z≥+2.',
    evaluate: evalT1,
  },
  {
    id: 'T2',
    name: 'Return-shock continuation',
    family: 'statistical',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    pairedWith: 'T1',
    rules: 'Same T1 opportunities opposite: RISE z≥+2, FALL z≤-2. Shares cooldown with T1.',
    evaluate: evalT2,
  },
  {
    id: 'T3',
    name: 'Return autocorrelation',
    family: 'statistical',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'ρ on last 240 log returns; ≥+0.15 follow latest return; ≤-0.15 fade. Experimental threshold, not significance.',
    evaluate: evalT3,
  },
  {
    id: 'O1',
    name: 'Stochastic reversal 14,3,3',
    family: 'stochastic',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Full Stoch K/D cross from ≤20 (RISE) or ≥80 (FALL). Signal bar included in 14-range.',
    evaluate: evalO1,
  },
  {
    id: 'O2',
    name: 'Stochastic + EMA50',
    family: 'stochastic',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'O1 plus close vs rising/falling EMA50.',
    evaluate: evalO2,
  },
  {
    id: 'N1',
    name: 'CCI20 EMA50 pullback',
    family: 'cci_dmi',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'CCI crosses -100/−/+100 with EMA50 trend filter. MAD of 20 typical prices; zero MAD unavailable.',
    evaluate: evalN1,
  },
  {
    id: 'N2',
    name: 'DMI14/ADX14 crossover',
    family: 'cci_dmi',
    version: STRATEGY_REGISTRY_VERSION,
    usesFormingCandle: false,
    rules: 'Wilder +DI/−DI cross with ADX≥25. ADX is strength only, not direction alone.',
    evaluate: evalN2,
  },
]

export function getStrategy(id: StrategyId): StrategyDefinition {
  const s = STRATEGY_REGISTRY.find((x) => x.id === id)
  if (!s) throw new Error(`Unknown strategy ${id}`)
  return s
}

export function assertRegistryComplete(): void {
  if (STRATEGY_REGISTRY.length !== 22) {
    throw new Error(`Expected 22 strategies, got ${STRATEGY_REGISTRY.length}`)
  }
  const ids = new Set(STRATEGY_REGISTRY.map((s) => s.id))
  if (ids.size !== 22) throw new Error('Duplicate strategy IDs')
}
