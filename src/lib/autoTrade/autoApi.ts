/**
 * Browser client for the VPS always-on Auto trade worker.
 * On deploy, VITE_AUTO_API_URL=/auto-api (same-origin nginx proxy).
 * All status/control calls require VITE_AUTO_API_TOKEN.
 */
import { autoApiAuthHeaders, getAutoApiToken } from './autoApiAuth'

function normalizeBase(raw: string | undefined): string {
  const v = (raw ?? '/auto-api').trim().replace(/\/$/, '')
  return v || '/auto-api'
}

export const AUTO_API_BASE = normalizeBase(
  import.meta.env.VITE_AUTO_API_URL as string | undefined,
)

export type VpsAutoStatus = {
  ok: boolean
  worker?: string
  symbol?: string
  feedHealthy?: boolean
  tradeStatus?: string
  autoEnabled?: boolean
  credentialsReady?: boolean
  cooldownLeftSec?: number
  lastError?: string | null
  lastSignal?: {
    side: string
    candleTime: number
    source: string
    at: number
    expiresAt: number
  } | null
  settings?: Record<string, unknown>
  log?: Array<{
    id: string
    at: number
    signal: string
    barrier: string
    ok: boolean
    message: string
    contractId?: number | null
  }>
  candleCount?: number
  note?: string
  error?: string
}

export async function fetchVpsAutoStatus(signal?: AbortSignal): Promise<VpsAutoStatus | null> {
  try {
    const res = await fetch(`${AUTO_API_BASE}/status`, {
      signal,
      headers: autoApiAuthHeaders(false),
    })
    if (!res.ok) return null
    return await res.json() as VpsAutoStatus
  } catch {
    return null
  }
}

/** Public liveness only — no secrets. Does not prove control auth. */
export async function fetchVpsAutoHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${AUTO_API_BASE}/health`, { signal })
    return res.ok
  } catch {
    return false
  }
}

export async function postVpsAutoControl(
  path: string,
  body?: Record<string, unknown>,
): Promise<VpsAutoStatus> {
  if (!getAutoApiToken()) {
    throw new Error('VITE_AUTO_API_TOKEN missing — cannot control VPS Auto')
  }
  const res = await fetch(`${AUTO_API_BASE}${path}`, {
    method: 'POST',
    headers: autoApiAuthHeaders(true),
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json() as VpsAutoStatus
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`)
  return json
}

export type BrowserTradeSettingsPayload = {
  appId: string
  token: string
  accountId: string
  stake: number
  useFullStake: boolean
  barrierAbs: number
  duration: number
  durationUnit: string
  cooldownSec: number
  cooldownUntil?: number
  currency: string
}

/**
 * One Auto switch: push browser settings and enable/disable the VPS worker.
 * Returns null if the worker is not deployed / unreachable / unauthorized.
 */
export async function syncBrowserAutoToVps(
  on: boolean,
  settings: BrowserTradeSettingsPayload,
): Promise<VpsAutoStatus | null> {
  if (!getAutoApiToken()) return null
  const healthy = await fetchVpsAutoHealth()
  if (!healthy) return null
  try {
    await postVpsAutoControl('/control/settings', {
      ...settings,
      autoEnabled: false,
    })
    return await postVpsAutoControl(on ? '/control/enable' : '/control/disable')
  } catch {
    return null
  }
}
