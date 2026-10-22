import { ResearchStatus, SyntheticAnalysis, SyntheticConfidence, SyntheticStatus } from '../api/synthetic';

export function decisionIsFresh(analysis: SyntheticAnalysis | undefined, now = Date.now() / 1000): boolean {
  const epoch = analysis?.decision_epoch;
  return epoch != null && Number.isFinite(epoch) && now >= epoch && now - epoch <= 20;
}

export function confidencePresentation(status: SyntheticStatus | null, analysis?: SyntheticAnalysis) {
  const prediction = status?.confidence || analysis?.ml;
  const cached = status?.confidence;
  const combined = prediction?.combined_confidence;
  const confidence = combined != null && Number.isFinite(combined) ? combined : prediction?.confidence;
  const hasReading = confidence != null && Number.isFinite(confidence);
  const hasChecks = (prediction?.checks || []).some(check => check.id !== 'model');
  const state = cached?.status;
  const stale = cached?.stale === true || state === 'stale' || state === 'error';
  const label = !hasReading ? (state === 'loading' ? 'Calculating confidence' : state === 'training' ? 'Training in progress' : 'Confidence unavailable')
    : stale ? 'Last confidence · stale'
      : hasChecks ? 'Signal confidence'
        : prediction?.promoted === true ? 'Validated model confidence' : 'Candidate confidence · display only';
  const reason = cached?.error || prediction?.reason || 'Waiting for the closed-candle signals.';
  return { prediction, hasReading, stale, label, reason, displayConfidence: confidence, updatedAt: (prediction as SyntheticConfidence | undefined)?.updated_at };
}

export function researchPresentation(research?: ResearchStatus | null) {
  const finalSignal = research?.final_signal === 'RISE' || research?.final_signal === 'FALL' ? research.final_signal : 'NO TRADE';
  const probability = research?.calibrated_win_probability;
  return {
    final: finalSignal,
    probability: probability != null && Number.isFinite(probability) ? probability : null,
    expectedValue: research?.expected_value != null && Number.isFinite(research.expected_value) ? research.expected_value : null,
    regime: research?.market_regime || 'unknown',
    volatility: research?.volatility || 'unavailable',
    status: research?.approval_state || research?.model_status || 'RESEARCH',
    reason: research?.reason || 'No research evaluation has been run. The one-minute bot is unchanged.',
    placesTrades: false,
  };
}

const EVIDENCE = ['frama', 'range_filter', 'timeframes', 'momentum', 'trend_structure', 'bos_choch', 'levels', 'breakout_retest', 'liquidity', 'ema', 'macd'];

export function combinedSignal(
  analysis: SyntheticAnalysis | undefined,
  research: ResearchStatus | null | undefined,
  fresh: boolean,
) {
  const raw = research?.raw_win_probability;
  const calibrated = research?.calibrated_win_probability;
  const probability = raw != null && Number.isFinite(raw) ? raw : calibrated != null && Number.isFinite(calibrated) ? calibrated : null;
  const candidate = research?.candidate_direction === 'RISE' || research?.candidate_direction === 'FALL' ? research.candidate_direction : null;
  const modelLean = candidate && probability != null && probability >= 0.55 ? candidate : null;
  const votes = (analysis?.factors || []).flatMap(factor => {
    if (!EVIDENCE.includes(String(factor.id)) || (factor.side !== 'RISE' && factor.side !== 'FALL')) return [];
    return [{ id: String(factor.id), name: factor.name, side: factor.side as 'RISE' | 'FALL' }];
  });
  const rise = votes.filter(vote => vote.side === 'RISE').length;
  const fall = votes.filter(vote => vote.side === 'FALL').length;
  const agreeing = modelLean === 'RISE' ? rise : modelLean === 'FALL' ? fall : 0;
  const opposing = modelLean === 'RISE' ? fall : modelLean === 'FALL' ? rise : 0;
  const confirmed = Boolean(fresh && modelLean && agreeing >= 2 && agreeing > opposing);
  const total = (modelLean ? 1 : 0) + rise + fall;
  const confidence = modelLean && total ? (agreeing + 1) / total : null;
  const direction = confirmed && modelLean ? modelLean : 'NO TRADE';
  let reason: string;
  if (!research) reason = 'The model has no prediction yet. Market evidence can confirm a call only after Evaluate research has produced one.';
  else if (!candidate || probability == null) reason = 'The model did not return a Rise or Fall probability.';
  else if (!modelLean) reason = `The model leans ${candidate} at ${(probability * 100).toFixed(1)}%. That is too close to 50% to call a direction, so the evidence is not used as a signal.`;
  else if (!fresh) reason = `The model leans ${modelLean} at ${(probability * 100).toFixed(1)}%, but this candle is outside the 20-second entry window.`;
  else if (opposing > agreeing) reason = `The model leans ${modelLean}, and more market-evidence checks say the other way. No signal.`;
  else if (agreeing < 2) reason = `The model leans ${modelLean}, but fewer than two evidence checks agree. No signal.`;
  else reason = `The model leans ${modelLean} at ${(probability * 100).toFixed(1)}%. ${agreeing} evidence checks agree and ${opposing} disagree. Confidence is that share of agreement, not a guaranteed win rate.`;
  return {
    direction,
    confidence,
    modelLean,
    probability,
    votes,
    agreeing,
    opposing,
    reason,
    placesTrades: false,
  };
}
