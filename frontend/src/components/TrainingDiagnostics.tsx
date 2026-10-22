import { TrainingReport } from '../api/synthetic';

const percent = (value?: number | null) => value != null && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : 'Unavailable';
const count = (value?: number) => value != null && Number.isFinite(value) ? value.toLocaleString('en-GB') : 'Unavailable';

export function TrainingDiagnostics({ report }: { report?: TrainingReport }) {
  if (!report?.final_test?.catboost) return null;
  const metrics = report.final_test.catboost;
  const diagnostics = metrics.selection_diagnostics;
  const noneSelected = metrics.selected === 0 || metrics.coverage === 0;
  const nearChance = metrics.auc != null && Math.abs(metrics.auc - 0.5) <= 0.03;
  return <div className="sy-training-summary" role="status">
    <h3>{report.accepted ? 'Candidate passed validation' : 'Validation did not establish a trading edge'}</h3>
    <p>{noneSelected
      ? `No final-test trades met all entry rules at the ${percent(report.threshold)} confidence threshold. Selected hit rate is unavailable because there were no qualifying trades.`
      : `${count(metrics.selected)} of ${count(report.final_test.samples)} final-test samples met the entry rules. Overall accuracy includes samples the strategy would not trade.`}</p>
    {nearChance && <p>AUC {metrics.auc?.toFixed(3)} is close to 0.500: this candidate has little ability to separate Rise from Fall on unseen data.</p>}
    {diagnostics && <>
      <div className="sy-diagnostic-stats">
        <div><span>Passed confidence</span><strong>{count(diagnostics.confidence_passed)}</strong></div>
        <div><span>Passed technical rules</span><strong>{count(diagnostics.rule_eligible)}</strong></div>
        <div><span>Passed both + direction</span><strong>{count(diagnostics.selected)}</strong></div>
        <div><span>Median / highest confidence</span><strong>{percent(diagnostics.confidence_median)} / {percent(diagnostics.confidence_max)}</strong></div>
      </div>
      {diagnostics.reason && <p>{diagnostics.reason}</p>}
    </>}
    {!report.accepted && <p>Review data freshness, gaps and the failed checks below. Repeating training on the same data does not establish an edge; this candidate remains blocked from trading.</p>}
  </div>;
}
