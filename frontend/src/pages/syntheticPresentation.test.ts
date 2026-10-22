import { describe, expect, it } from 'vitest';
import { combinedSignal, confidencePresentation, decisionIsFresh, researchPresentation } from './syntheticPresentation';
import { SyntheticAnalysis } from '../api/synthetic';

describe('decision freshness', () => {
  const analysis: SyntheticAnalysis = { symbol: 'R_100', direction: 'RISE', eligible: true, decision_epoch: 1000 };
  it('only qualifies a decision inside the entry window', () => {
    expect(decisionIsFresh(analysis, 1020)).toBe(true);
    expect(decisionIsFresh(analysis, 1021)).toBe(false);
    expect(decisionIsFresh(analysis, 999)).toBe(false);
    expect(decisionIsFresh(undefined, 1000)).toBe(false);
  });
});

describe('confidence presentation', () => {
  it('shows passive candidate probabilities even without a trading scan', () => {
    const view = confidencePresentation({ running: false, confidence: { confidence: 0.523, p_rise: 0.523, promoted: false, status: 'ready' } });
    expect(view.hasReading).toBe(true);
    expect(view.prediction?.p_rise).toBe(0.523);
    expect(view.label).toBe('Candidate confidence · display only');
  });
  it('shows the signal share and leaves the model out', () => {
    const view = confidencePresentation({
      running: false,
      confidence: {
        confidence: null, combined_confidence: 0.6, p_rise: null, promoted: false, status: 'ready',
        checks: [{ id: 'frama', name: 'FRAMA', side: 'RISE', status: 'PASS', detail: '' }],
      },
    });
    expect(view.displayConfidence).toBe(0.6);
    expect(view.label).toBe('Signal confidence');
  });
  it('never describes stale promoted confidence as a current validated reading', () => {
    const view = confidencePresentation({ running: false, confidence: { confidence: 0.7, promoted: true, status: 'error', error: 'Feed timed out' } });
    expect(view.stale).toBe(true);
    expect(view.label).toContain('stale');
    expect(view.reason).toBe('Feed timed out');
  });
  it('does not substitute an old scanned probability for a new unavailable reading', () => {
    const view = confidencePresentation({ running: false, confidence: { confidence: null, reason: 'Waiting for the closed-candle signals.' } }, {
      symbol: 'R_100', direction: 'RISE', ml: { confidence: 0.8 },
    });
    expect(view.hasReading).toBe(false);
    expect(view.reason).toContain('closed-candle signals');
    expect(view.displayConfidence).toBeNull();
  });
});

describe('research presentation', () => {
  it('shows no trade and no invented probability before an evaluation exists', () => {
    const view = researchPresentation(undefined);
    expect(view.final).toBe('NO TRADE');
    expect(view.probability).toBeNull();
    expect(view.expectedValue).toBeNull();
    expect(view.status).toBe('RESEARCH');
    expect(view.placesTrades).toBe(false);
  });

  it('keeps a supplied probability attached to the returned signal', () => {
    const view = researchPresentation({
      final_signal: 'FALL', calibrated_win_probability: 0.64, expected_value: 0.04,
      approval_state: 'SIGNAL_READY', market_regime: 'trending_low_volatility',
    });
    expect(view.final).toBe('FALL');
    expect(view.probability).toBe(0.64);
    expect(view.expectedValue).toBe(0.04);
    expect(view.regime).toBe('trending_low_volatility');
  });
});

describe('combined signal', () => {
  const factors = [
    { id: 'frama', name: 'FRAMA channel', status: 'PASS', detail: '', side: 'RISE' },
    { id: 'range_filter', name: 'Range Filter', status: 'PASS', detail: '', side: 'RISE' },
    { id: 'levels', name: 'Support / resistance', status: 'PASS', detail: '', side: 'RISE' },
    { id: 'breakout_retest', name: 'Breakout / retest', status: 'PASS', detail: '', side: 'FALL' },
  ];
  const analysis: SyntheticAnalysis = { symbol: 'R_100', direction: 'RISE', decision_epoch: 1000, factors };

  it('confirms only when the model lean and at least two evidence checks agree', () => {
    const view = combinedSignal(analysis, { candidate_direction: 'RISE', raw_win_probability: 0.62 }, true);
    expect(view.direction).toBe('RISE');
    expect(view.confidence).toBeCloseTo(4 / 5);
    expect(view.placesTrades).toBe(false);
  });

  it('does not turn a near-50% model reading into a signal', () => {
    const view = combinedSignal(analysis, { candidate_direction: 'RISE', raw_win_probability: 0.504 }, true);
    expect(view.direction).toBe('NO TRADE');
    expect(view.confidence).toBeNull();
    expect(view.reason).toContain('too close to 50%');
  });

  it('stays flat when the evidence mostly disagrees with the model', () => {
    const opposed: SyntheticAnalysis = {
      ...analysis,
      factors: factors.map(factor => ({ ...factor, side: factor.id === 'frama' ? 'FALL' : 'FALL' })),
    };
    const view = combinedSignal(opposed, { candidate_direction: 'RISE', raw_win_probability: 0.7 }, true);
    expect(view.direction).toBe('NO TRADE');
    expect(view.reason).toContain('other way');
  });
});
