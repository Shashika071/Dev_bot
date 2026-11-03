import {
  DEFAULT_FROZEN_CONTRACT,
  normalizeContractSettings,
  type FrozenContractSettings,
} from './contractSettings'

const KEY = 'v100_research_cooldown_prefs_v1'

export interface ResearchCooldownPrefs {
  entryCooldownSec: number
  postWinCooldownSec: number
}

export function loadResearchCooldownPrefs(): ResearchCooldownPrefs {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) {
      return {
        entryCooldownSec: DEFAULT_FROZEN_CONTRACT.entryCooldownSec,
        postWinCooldownSec: DEFAULT_FROZEN_CONTRACT.postWinCooldownSec,
      }
    }
    const p = JSON.parse(raw) as Partial<ResearchCooldownPrefs>
    const normalized = normalizeContractSettings({
      ...DEFAULT_FROZEN_CONTRACT,
      entryCooldownSec: p.entryCooldownSec,
      postWinCooldownSec: p.postWinCooldownSec,
    })
    return {
      entryCooldownSec: normalized.entryCooldownSec,
      postWinCooldownSec: normalized.postWinCooldownSec,
    }
  } catch {
    return {
      entryCooldownSec: DEFAULT_FROZEN_CONTRACT.entryCooldownSec,
      postWinCooldownSec: DEFAULT_FROZEN_CONTRACT.postWinCooldownSec,
    }
  }
}

export function saveResearchCooldownPrefs(prefs: ResearchCooldownPrefs): void {
  try {
    const normalized = normalizeContractSettings({
      ...DEFAULT_FROZEN_CONTRACT,
      ...prefs,
    })
    localStorage.setItem(KEY, JSON.stringify({
      entryCooldownSec: normalized.entryCooldownSec,
      postWinCooldownSec: normalized.postWinCooldownSec,
    }))
  } catch {
    /* ignore quota */
  }
}

export function contractWithPrefs(
  base: FrozenContractSettings,
  prefs: ResearchCooldownPrefs,
): FrozenContractSettings {
  return normalizeContractSettings({
    ...base,
    entryCooldownSec: prefs.entryCooldownSec,
    postWinCooldownSec: prefs.postWinCooldownSec,
  })
}
