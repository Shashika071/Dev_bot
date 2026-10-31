const API_BASE = 'https://api.derivws.com'

export type DerivAccount = {
  account_id: string
  balance?: number
  currency?: string
  status?: string
  account_type?: string
  group?: string
}

type ApiEnvelope<T> = {
  data?: T
  errors?: Array<{ message?: string; code?: string; status?: number }>
}

function authHeaders(token: string, appId: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    'Deriv-App-ID': appId,
    'Content-Type': 'application/json',
  }
}

async function readJson<T>(res: Response): Promise<ApiEnvelope<T>> {
  const text = await res.text()
  try {
    return JSON.parse(text) as ApiEnvelope<T>
  } catch {
    throw new Error(res.ok ? 'Invalid JSON from Deriv API' : `HTTP ${res.status}: ${text.slice(0, 160)}`)
  }
}

function firstError(body: ApiEnvelope<unknown>, fallback: string): string {
  return body.errors?.[0]?.message || fallback
}

/** List Options demo/real accounts for this PAT + App ID. */
export async function listDerivAccounts(token: string, appId: string): Promise<DerivAccount[]> {
  const res = await fetch(`${API_BASE}/trading/v1/options/accounts`, {
    method: 'GET',
    headers: authHeaders(token, appId),
  })
  const body = await readJson<DerivAccount[]>(res)
  if (!res.ok) throw new Error(firstError(body, `List accounts failed (${res.status})`))
  const rows = Array.isArray(body.data) ? body.data : []
  if (!rows.length) throw new Error('No Options accounts found. Create a demo account in Deriv first.')
  return rows
}

/** One-time authenticated WebSocket URL (valid ~120s, single use). */
export async function getTradingWsUrl(token: string, appId: string, accountId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/trading/v1/options/accounts/${encodeURIComponent(accountId)}/otp`, {
    method: 'POST',
    headers: authHeaders(token, appId),
  })
  const body = await readJson<{ url?: string }>(res)
  if (!res.ok) throw new Error(firstError(body, `OTP failed (${res.status})`))
  const url = body.data?.url
  if (!url) throw new Error('OTP response did not include a WebSocket URL')
  return url
}
