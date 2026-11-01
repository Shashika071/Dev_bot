/**
 * Optional remote collector client.
 * Deployed UI uses in-browser research by default (see useBrowserResearch).
 * Set VITE_RESEARCH_API_URL only if you run a separate collector on a reachable host.
 */
export const RESEARCH_API_BASE =
  (import.meta.env.VITE_RESEARCH_API_URL as string | undefined)?.replace(/\/$/, '')
  || 'http://127.0.0.1:8787'

export async function fetchResearchStatus(signal?: AbortSignal) {
  const res = await fetch(`${RESEARCH_API_BASE}/status`, { signal })
  if (!res.ok) throw new Error(`Research API ${res.status}`)
  return res.json()
}

export async function fetchResearchHealth(signal?: AbortSignal) {
  const res = await fetch(`${RESEARCH_API_BASE}/health`, { signal })
  if (!res.ok) throw new Error(`Research API ${res.status}`)
  return res.json() as Promise<{ ok: boolean; live: boolean; generatedAt: number; stage: string }>
}

export async function postResearchControl(path: string, body?: unknown) {
  const res = await fetch(`${RESEARCH_API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`Research control failed ${res.status}`)
  return res.json()
}

export function researchExportUrl(kind: 'csv' | 'report' | 'summary') {
  if (kind === 'csv') return `${RESEARCH_API_BASE}/export/csv`
  if (kind === 'report') return `${RESEARCH_API_BASE}/export/report`
  return `${RESEARCH_API_BASE}/export/summary`
}
