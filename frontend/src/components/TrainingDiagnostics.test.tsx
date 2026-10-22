import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TrainingDiagnostics } from './TrainingDiagnostics';

describe('training diagnostics', () => {
  it('explains the screenshot result without presenting a missing hit rate as zero accuracy', () => {
    const markup = renderToStaticMarkup(<TrainingDiagnostics report={{ threshold: 0.6, accepted: false, final_test: {
      samples: 25217, catboost: { accuracy: 0.5, auc: 0.502, selected: 0, coverage: 0, selection_diagnostics: {
        confidence_passed: 0, rule_eligible: 30, selected: 0, confidence_median: 0.52, confidence_max: 0.59,
      } },
    } }} />);
    expect(markup).toContain('60.0% confidence threshold');
    expect(markup).toContain('no qualifying trades');
    expect(markup).toContain('AUC 0.502 is close to 0.500');
    expect(markup).toContain('52.0% / 59.0%');
    expect(markup).toContain('remains blocked from trading');
  });
  it('renders no invented result before training', () => {
    expect(renderToStaticMarkup(<TrainingDiagnostics />)).toBe('');
  });
});
