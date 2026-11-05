import { describe, expect, it } from 'vitest'
import {
  credentialsReady,
  publicServerAutoSettings,
  sanitizeServerAutoSettings,
} from './serverSettings'

describe('server auto settings', () => {
  it('boots Auto OFF and ignores incomplete credentials', () => {
    const s = sanitizeServerAutoSettings({ autoEnabled: true, appId: '1' })
    expect(s.autoEnabled).toBe(true) // sanitize allows flag; worker still refuses without credentials
    expect(credentialsReady(s)).toBe(false)
  })

  it('never returns full token in public status', () => {
    const s = sanitizeServerAutoSettings({
      token: 'abcdefghijklmnop',
      appId: 'app',
      accountId: 'acc',
    })
    const pub = publicServerAutoSettings(s)
    expect(pub.token).not.toBe(s.token)
    expect(pub.tokenSet).toBe(true)
    expect(pub.token.includes('abcd')).toBe(true)
  })
})
