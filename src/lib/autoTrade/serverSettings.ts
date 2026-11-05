/**
 * Persisted settings for the VPS always-on C5 Auto worker.
 * Auto always boots OFF unless explicitly enabled after credentials exist.
 */
export type ServerAutoSettings = {
  appId: string
  token: string
  accountId: string
  stake: number
  useFullStake: boolean
  barrierAbs: number
  duration: number
  durationUnit: 's' | 'm' | 'h' | 'd'
  cooldownSec: number
  /** Epoch ms — no Auto buys until this time (shared with browser when synced). */
  cooldownUntil: number
  currency: string
  symbol: string
  /** Must be toggled ON via API — never silently true on first boot from env alone unless AUTO_TRADE_ENABLED=true */
  autoEnabled: boolean
}

export const DEFAULT_SERVER_AUTO_SETTINGS: ServerAutoSettings = {
  appId: '',
  token: '',
  accountId: '',
  stake: 1,
  useFullStake: true,
  barrierAbs: 0.05,
  duration: 7,
  durationUnit: 'm',
  cooldownSec: 420,
  cooldownUntil: 0,
  currency: 'USD',
  symbol: 'R_100',
  autoEnabled: false,
}

export function sanitizeServerAutoSettings(
  raw: Partial<ServerAutoSettings> | null | undefined,
  defaults: ServerAutoSettings = DEFAULT_SERVER_AUTO_SETTINGS,
): ServerAutoSettings {
  const p = raw ?? {}
  return {
    appId: typeof p.appId === 'string' ? p.appId.trim() : defaults.appId,
    token: typeof p.token === 'string' ? p.token.trim() : defaults.token,
    accountId: typeof p.accountId === 'string' ? p.accountId.trim() : defaults.accountId,
    stake: typeof p.stake === 'number' && p.stake > 0 ? p.stake : defaults.stake,
    useFullStake: p.useFullStake !== false,
    barrierAbs: typeof p.barrierAbs === 'number' && p.barrierAbs > 0 ? p.barrierAbs : defaults.barrierAbs,
    duration: typeof p.duration === 'number' && p.duration > 0 ? p.duration : defaults.duration,
    durationUnit:
      p.durationUnit === 's' || p.durationUnit === 'h' || p.durationUnit === 'd'
        ? p.durationUnit
        : 'm',
    cooldownSec: typeof p.cooldownSec === 'number' && p.cooldownSec >= 0 ? p.cooldownSec : defaults.cooldownSec,
    cooldownUntil:
      typeof p.cooldownUntil === 'number' && p.cooldownUntil > Date.now() ? p.cooldownUntil : 0,
    currency: typeof p.currency === 'string' && p.currency ? p.currency : defaults.currency,
    symbol: typeof p.symbol === 'string' && p.symbol ? p.symbol : defaults.symbol,
    autoEnabled: p.autoEnabled === true,
  }
}

export function credentialsReady(s: ServerAutoSettings): boolean {
  return !!(s.appId && s.token && s.accountId)
}

/** Safe public status — never expose full token. */
export function publicServerAutoSettings(s: ServerAutoSettings) {
  return {
    ...s,
    token: s.token ? `${s.token.slice(0, 4)}…${s.token.slice(-4)}` : '',
    tokenSet: !!s.token,
  }
}
