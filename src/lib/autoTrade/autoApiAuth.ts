/** Shared control-plane token for the VPS Auto worker API. */
export function getAutoApiToken(): string {
  return String(import.meta.env.VITE_AUTO_API_TOKEN ?? '').trim()
}

export function autoApiAuthHeaders(json = true): HeadersInit {
  const headers: Record<string, string> = {}
  if (json) headers['Content-Type'] = 'application/json'
  const token = getAutoApiToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

/** Extract bearer / x-auto-trade-token from a Node IncomingMessage-like headers object. */
export function readAutoApiTokenFromHeaders(headers: Record<string, string | string[] | undefined>): string {
  const auth = headers.authorization ?? headers.Authorization
  const authVal = Array.isArray(auth) ? auth[0] : auth
  if (typeof authVal === 'string' && authVal.toLowerCase().startsWith('bearer ')) {
    return authVal.slice(7).trim()
  }
  const alt = headers['x-auto-trade-token'] ?? headers['X-Auto-Trade-Token']
  const altVal = Array.isArray(alt) ? alt[0] : alt
  return typeof altVal === 'string' ? altVal.trim() : ''
}

/**
 * Authorize a control/status request.
 * If expectedToken is empty, deny unless allowInsecure is true (local dev only).
 */
export function authorizeAutoApiRequest(
  providedToken: string,
  expectedToken: string,
  allowInsecure = false,
): { ok: true } | { ok: false; status: number; error: string } {
  if (!expectedToken) {
    if (allowInsecure) return { ok: true }
    return { ok: false, status: 503, error: 'AUTO_TRADE_API_TOKEN not configured' }
  }
  if (!providedToken || providedToken !== expectedToken) {
    return { ok: false, status: 401, error: 'unauthorized' }
  }
  return { ok: true }
}
