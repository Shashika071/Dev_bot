/**
 * Plain-language manual decision summary for M1 C5 + M5/M15 context.
 * Observations only — never a win probability.
 */
import type { LiveSetupConfidence, SideSetup } from '../strategy/liveConfidence'
import type { EntryDisplay } from '../strategy/entryDisplay'
import type {
  Agreement,
  MultiTimeframeContext,
  TimeframeCard,
} from './timeframeContext'

export type DecisionSummary = {
  headline: string
  missingCondition: string | null
  forSide: string
  againstSide: string
  disclaimer: string
}

/** Short label for the single missing C5 required condition. */
export function missingConditionLabel(setup: SideSetup | null): string | null {
  if (!setup) return null
  const waiting = setup.requiredLabels.filter((r) => !r.ok)
  if (waiting.length !== 1) {
    if (waiting.length === 0) return null
    return waiting.map((w) => w.text).join('; ')
  }
  // Short name for the common copy: “Waiting for RSI.”
  const key = waiting[0]!.key
  if (key === 'rsi') return 'RSI'
  if (key === 'sma') return 'SMA'
  if (key === 'supertrend') return 'Supertrend'
  if (key === 'ut') return 'UT crossover'
  return waiting[0]!.text
}

function missingRequired(setup: SideSetup | null): string | null {
  return missingConditionLabel(setup)
}

function supportingReason(card: TimeframeCard, side: 'RISE' | 'FALL'): { score: number; text: string } | null {
  const c = card.closed
  if (!c || c.agreement !== 'Supports') return null
  const tf = card.label
  if (c.behavior === 'rejection_support' || c.behavior === 'rejection_resistance') {
    return {
      score: 3,
      text: `${tf === '5m' ? 'M5' : 'M15'} closed with a ${c.behavior === 'rejection_support' ? 'support' : 'resistance'} rejection that favors ${side}.`,
    }
  }
  if (c.behavior === 'breakout') {
    return {
      score: 3,
      text: `${tf === '5m' ? 'M5' : 'M15'} closed a breakout that favors ${side}.`,
    }
  }
  if (c.trend === 'Up' && side === 'RISE') {
    return { score: 1, text: `${tf === '5m' ? 'M5' : 'M15'} is in an uptrend.` }
  }
  if (c.trend === 'Down' && side === 'FALL') {
    return { score: 1, text: `${tf === '5m' ? 'M5' : 'M15'} is in a downtrend.` }
  }
  return { score: 1, text: c.agreementReason }
}

function hesitationReason(card: TimeframeCard, side: 'RISE' | 'FALL'): { score: number; text: string } | null {
  const c = card.closed
  const tf = card.label === '5m' ? 'M5' : 'M15'
  // Forming test of opposing zone ranks highest
  if (card.forming?.behavior === 'testing') {
    const opposing =
      (side === 'RISE' && card.forming.text.includes('resistance'))
      || (side === 'FALL' && card.forming.text.includes('support'))
    if (opposing) {
      return {
        score: 4,
        text: `${tf} is testing ${side === 'RISE' ? 'resistance' : 'support'}; no breakout has closed.`,
      }
    }
  }
  if (!c) return null
  if (c.agreement === 'Opposes') {
    return { score: 3, text: `${tf} opposes ${side}. ${c.agreementReason}` }
  }
  if (c.agreement === 'Mixed') {
    const nearOppose =
      (side === 'RISE' && c.resistance && (c.resistance.distance === 'Near' || c.resistance.distance === 'Inside'))
      || (side === 'FALL' && c.support && (c.support.distance === 'Near' || c.support.distance === 'Inside'))
    if (nearOppose) {
      return {
        score: 3,
        text: `${tf} is at ${side === 'RISE' ? 'resistance' : 'support'}. No breakout has closed.`,
      }
    }
    return { score: 2, text: `${tf} is mixed vs ${side}. ${c.agreementReason}` }
  }
  return null
}

export function buildDecisionSummary(opts: {
  entry: EntryDisplay
  liveSetup: LiveSetupConfidence
  side: 'RISE' | 'FALL' | null
  context: MultiTimeframeContext
}): DecisionSummary {
  const { entry, liveSetup, side, context } = opts
  const disclaimer = 'These are observations only. The decision remains yours.'

  if (!side) {
    return {
      headline: 'No clear M1 side yet.',
      missingCondition: null,
      forSide: 'No supporting reason available.',
      againstSide: 'No hesitation reason available.',
      disclaimer,
    }
  }

  const setup = side === 'RISE' ? liveSetup.rise : liveSetup.fall
  const missing = entry.state === 'WAIT' && setup.passed === 3 ? missingRequired(setup) : null

  let headline: string
  if (entry.state === 'CONFIRMED') {
    headline = `Confirmed ${side} 4/4.`
  } else if (entry.state === 'FORMING') {
    headline = `Forming ${side} 4/4.`
  } else if (setup.passed === 3) {
    headline = missing
      ? `Watching ${side} 3/4. Waiting for ${missing}.`
      : `Watching ${side} 3/4.`
  } else {
    headline = `Watching ${side} · ${setup.passed}/4.`
  }

  const supportCandidates = [
    supportingReason(context.m5, side),
    supportingReason(context.m15, side),
  ].filter((x): x is NonNullable<typeof x> => !!x)
  supportCandidates.sort((a, b) => b.score - a.score)

  const hesitateCandidates = [
    hesitationReason(context.m5, side),
    hesitationReason(context.m15, side),
  ].filter((x): x is NonNullable<typeof x> => !!x)
  hesitateCandidates.sort((a, b) => b.score - a.score)

  return {
    headline,
    missingCondition: missing,
    forSide: supportCandidates[0]
      ? `For ${side}: ${supportCandidates[0].text}`
      : 'No supporting reason available.',
    againstSide: hesitateCandidates[0]
      ? `Against ${side}: ${hesitateCandidates[0].text}`
      : 'No hesitation reason available.',
    disclaimer,
  }
}

export function agreementLabel(agreement: Agreement | null): string {
  if (agreement == null) return '—'
  return agreement
}
