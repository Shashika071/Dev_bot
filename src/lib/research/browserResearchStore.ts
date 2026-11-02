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

export function archiveBrowserResearch(state: ExperimentState): void {
  if (state.startedAt != null) {
    // Fail visibly rather than overwriting the only copy if storage is full.
    localStorage.setItem(`${KEY}_archive_${state.startedAt}_${Date.now()}`, JSON.stringify(state))
  }
}

export function saveBrowserResearch(state: ExperimentState): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(state))
    return true
  } catch {
    return false
  }
}
