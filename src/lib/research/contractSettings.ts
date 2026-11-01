export interface FrozenContractSettings {
  contractType: 'ONETOUCH'
  barrierAbs: number
  duration: number
  durationUnit: 'm'
  paperStake: number
  currency: string
  maxQuoteAgeSec: number
}

export const DEFAULT_FROZEN_CONTRACT: FrozenContractSettings = {
  contractType: 'ONETOUCH',
  barrierAbs: 0.05,
  duration: 7,
  durationUnit: 'm',
  paperStake: 1,
  currency: 'USD',
  maxQuoteAgeSec: 5,
}
