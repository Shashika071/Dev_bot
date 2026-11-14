import {
  POST_WIN_COOLDOWN_SEC,
  STRATEGY_COOLDOWN_SEC,
} from './horizons'

export interface FrozenContractSettings {
  contractType: 'ONETOUCH'
  barrierAbs: number
  duration: number
  durationUnit: 'm'
  paperStake: number
  currency: string
  maxQuoteAgeSec: number
  /**
   * Research-only: seconds to wait after accepting a paper entry.
   * Independent of the live Trade tab cooldown.
   */
  entryCooldownSec: number
  /**
   * Research-only: seconds that ONE strategy waits after its own Touch HIT.
   * Other strategies keep collecting — wins never share this pause.
   */
  postWinCooldownSec: number
}

export const DEFAULT_FROZEN_CONTRACT: FrozenContractSettings = {
  contractType: 'ONETOUCH',
  barrierAbs: 0.05,
  duration: 7,
  durationUnit: 'm',
  paperStake: 1,
  currency: 'USD',
  maxQuoteAgeSec: 5,
  entryCooldownSec: STRATEGY_COOLDOWN_SEC,
  postWinCooldownSec: POST_WIN_COOLDOWN_SEC,
}

/** The manual decision study has its own contract; other research keeps its run settings. */
export function c5StudyContract(run: FrozenContractSettings): FrozenContractSettings {
  return { ...run, barrierAbs: 0.01, duration: 9, durationUnit: 'm', entryCooldownSec: 0, postWinCooldownSec: 0 }
}

/** Fill missing cooldown fields on older saved experiments. */
export function normalizeContractSettings(
  partial?: Partial<FrozenContractSettings> | null,
): FrozenContractSettings {
  const base = { ...DEFAULT_FROZEN_CONTRACT, ...(partial ?? {}) }
  const entry = Number(base.entryCooldownSec)
  const postWin = Number(base.postWinCooldownSec)
  return {
    ...base,
    contractType: 'ONETOUCH',
    durationUnit: 'm',
    barrierAbs: Number.isFinite(base.barrierAbs) && base.barrierAbs > 0 ? base.barrierAbs : 0.05,
    duration: Number.isFinite(base.duration) && base.duration > 0 ? base.duration : 7,
    paperStake: Number.isFinite(base.paperStake) && base.paperStake > 0 ? base.paperStake : 1,
    currency: typeof base.currency === 'string' && base.currency ? base.currency : 'USD',
    maxQuoteAgeSec: Number.isFinite(base.maxQuoteAgeSec) && base.maxQuoteAgeSec > 0
      ? base.maxQuoteAgeSec : 5,
    entryCooldownSec: Number.isFinite(entry) && entry >= 0 ? Math.floor(entry) : STRATEGY_COOLDOWN_SEC,
    postWinCooldownSec: Number.isFinite(postWin) && postWin >= 0
      ? Math.floor(postWin) : POST_WIN_COOLDOWN_SEC,
  }
}

export function withResearchCooldowns(
  contract: FrozenContractSettings,
  entryCooldownSec: number,
  postWinCooldownSec: number,
): FrozenContractSettings {
  return normalizeContractSettings({
    ...contract,
    entryCooldownSec,
    postWinCooldownSec,
  })
}
