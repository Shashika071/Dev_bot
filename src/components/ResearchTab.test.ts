// @vitest-environment jsdom
import { createElement } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { createInitialState } from '../lib/research/experimentEngine'
import { summarizeExperiment } from '../lib/research/summarizeExperiment'
import { ResearchTab } from './ResearchTab'

vi.mock('../lib/research/researchApi', () => ({
  fetchResearchHealth: vi.fn().mockRejectedValue(new Error('Disconnected')),
  fetchResearchStatus: vi.fn().mockRejectedValue(new Error('Disconnected')),
  postResearchControl: vi.fn(),
  researchExportUrl: (kind: string) => `/research-api/export/${kind}`,
}))
afterEach(() => { cleanup(); sessionStorage.clear(); localStorage.clear() })

it('keeps server results visible on disconnect, with aligned headers and explicit coverage', async () => {
  const state = createInitialState('discovery', ['S6'])
  const summary = summarizeExperiment(state)
  const s = summary.strategies[0]!
  Object.assign(s, { candidatesAccepted: 10, touchHits: 9, touchMisses: 0, touchUnknown: 1, quotedSettled: 8, quotedHits: 8, quotedUnknown: 2,
    quotesAvailable: 10, paperCost: 8, paperPnl: 0.24, paperReturn: 0.03,
    paperPnlLower: -1.76, paperPnlUpper: 0.30, pnlUnavailable: false })
  Object.assign(s.horizons.find((h) => h.minutes === 7)!, { total: 10, continuation: 1, unknown: 9,
    contRate: 1, randomContRate: 0.5, insufficient: true })
  sessionStorage.setItem('v100_research_server_status_v1', JSON.stringify({
    experimentId: 'discovery-test', state: { ...state, observationCount: 10 },
    summary, registryCount: 22, feedDiagnostics: { disconnects: 3, lastDisconnectReason: 'Socket closed (1006)' },
  }))
  render(createElement(ResearchTab, { market: null }))
  await waitFor(() => expect(screen.getByText(/showing last saved snapshot/i)).toBeTruthy())
  expect(screen.getByText(/Signal Touch:/).textContent).toContain('9 hits, 0 misses, 1 unknown, 0 pending')
  expect(screen.getByText(/Quoted Touch:/).textContent).toContain('8 hits, 0 misses, 2 unknown, 0 pending')
  expect(screen.queryByRole('columnheader', { name: '7m cont%' })).toBeNull()
  expect(screen.getByRole('columnheader', { name: 'Signal Touch win%' })).toBeTruthy()
  expect(screen.getByText('-1.76 to 0.30')).toBeTruthy()
  expect(screen.getAllByRole('columnheader')).toHaveLength(15)
  expect(screen.getAllByRole('row')[1].querySelectorAll('td')).toHaveLength(15)
  expect((screen.getByRole('button', { name: 'Resume' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByText(/using browser mode for now/i)).toBeNull()
})

it('does not warn about missing Touch outcomes when only endpoint diagnostics are unknown', async () => {
  const state = createInitialState('discovery', ['S6'])
  const summary = summarizeExperiment(state)
  const s = summary.strategies[0]!
  Object.assign(s, { candidatesAccepted: 1, touchHits: 1, quotedHits: 1, quotesAvailable: 1,
    quotedSettled: 1, paperCost: 1, paperPnl: 0.03, paperReturn: 0.03, pnlUnavailable: false })
  Object.assign(s.horizons.find((h) => h.minutes === 7)!, { total: 1, unknown: 1 })
  sessionStorage.setItem('v100_research_server_status_v1', JSON.stringify({
    experimentId: 'discovery-test', state: { ...state, observationCount: 1 }, summary, registryCount: 22,
  }))
  render(createElement(ResearchTab, { market: null }))
  await waitFor(() => expect(screen.getByText(/Signal Touch:/).textContent).toContain('1 hits, 0 misses, 0 unknown, 0 pending'))
  expect(screen.queryByText(/Missing Touch outcomes can affect/)).toBeNull()
  expect(screen.getAllByText('100.0%*')).toHaveLength(2)
})
