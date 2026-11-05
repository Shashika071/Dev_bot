import { describe, expect, it } from 'vitest'
import {
  authorizeAutoApiRequest,
  readAutoApiTokenFromHeaders,
} from './autoApiAuth'

describe('auto API auth', () => {
  it('rejects missing server token unless insecure allowed', () => {
    expect(authorizeAutoApiRequest('x', '', false)).toEqual({
      ok: false, status: 503, error: 'AUTO_TRADE_API_TOKEN not configured',
    })
    expect(authorizeAutoApiRequest('', '', true)).toEqual({ ok: true })
  })

  it('rejects wrong bearer token', () => {
    expect(authorizeAutoApiRequest('bad', 'secret', false)).toEqual({
      ok: false, status: 401, error: 'unauthorized',
    })
    expect(authorizeAutoApiRequest('secret', 'secret', false)).toEqual({ ok: true })
  })

  it('reads Authorization bearer and x-auto-trade-token', () => {
    expect(readAutoApiTokenFromHeaders({ authorization: 'Bearer abc' })).toBe('abc')
    expect(readAutoApiTokenFromHeaders({ 'x-auto-trade-token': 'xyz' })).toBe('xyz')
  })
})
