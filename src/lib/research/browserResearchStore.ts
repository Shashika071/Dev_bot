import type { ExperimentState } from './experimentEngine'
import { createInitialState } from './experimentEngine'

const KEY = 'v100_research_browser_v1'

export function loadBrowserResearch(): ExperimentState {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return createInitialState()
    const parsed = JSON.parse(raw) as ExperimentState
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.observations)) {
      return createInitialState()
    }
    return parsed
  } catch {
    return createInitialState()
  }
}

export function saveBrowserResearch(state: ExperimentState): void {
  try {
    // Cap observations to keep localStorage healthy
    const lean: ExperimentState = {
      ...state,
      observations: state.observations.slice(-2000),
      rejected: state.rejected.slice(-2000),
      acceptedKeys: state.acceptedKeys.slice(-4000),
      b0Fired: state.b0Fired.slice(-2000),
    }
    localStorage.setItem(KEY, JSON.stringify(lean))
  } catch {
    // quota / private mode — ignore; UI still works in memory
  }
}
