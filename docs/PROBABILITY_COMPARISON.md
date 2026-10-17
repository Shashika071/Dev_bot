# Comparing Touch probability models

The Touch trainer now compares candidates after independent probability calibration and against a frequency baseline with the same recent outcome information. These changes apply to newly trained versions. Existing saved models and reports retain their original evidence.

**Probability objectives**

CatBoost, XGBoost, the logistic baseline, and stacking use unweighted binary probability objectives. Automatically balancing touch/non-touch classes changes the probability target when touches are common. XGBoost's own [parameter guidance](https://xgboost.readthedocs.io/en/stable/tutorials/param_tuning.html#handle-imbalanced-dataset) distinguishes class weighting for ranking from estimating the correct event probability.

**Chronological roles**

The final training order is:

1. Train: fit base models and training-only ensemble weights. Tree/LSTM early stopping uses an internal training tail.
2. Calibration: fit one independent mapping for every candidate. The simple comparator is exactly this period's touch frequency.
3. Selection: compare frozen calibrated candidates on the next period. A candidate must beat the recent-frequency baseline here and on enough calibrated outer folds.
4. Final test: evaluate the frozen choice and report raw/calibrated scores for diagnostics. Test labels cannot choose a model or fit calibration.

Partitions remain purged. `validation_selection.temporal_order` records `train, cal, val, test`; the saved `cal` count is probability calibration and `val` is candidate selection. Earlier reports used the previous order and must not be interpreted using these new roles.

Outer folds keep their own train, early-stopping validation, calibration and evaluation slices, all before the final lockbox. Their baseline uses their own calibration-period frequency. Tying the baseline does not count as a fold win. Candidates without sufficient fold evidence remain ineligible for selection; weighted/stacking currently have diagnostic scores but no matching outer-fold evidence.

The selection metadata records period ranges, raw and calibrated selection scores, comparator probability, fold evidence and the reason for the choice. If no candidate qualifies, the frequency baseline remains the research candidate.

**Reports and inference**

`candidate_probability_metrics` contains each candidate's raw/calibrated Brier and AUC on identical final-test observations. Existing candidate metric blocks now describe calibrated predictions. The selected candidate's own mapping is persisted for inference; the frequency baseline persists its constant probability and calibration evidence.

The UI labels the historical training event rate as **Training touch frequency**, displays both probability score versions, and identifies the chosen simple model. Legacy results lacking these fields are explicitly labeled as having an unspecified probability basis.

**Validation and limits**

Regression tests check imbalanced no-signal frequencies, per-candidate mappings, recent-frequency baseline comparison, frozen selection when test labels change, calibration/selection ordering, fold isolation, save/load inference and strict baseline wins.

Retraining is required to produce results under the new comparison. A newly selected model still needs exact-contract quotes and payout-adjusted promotion evidence. These engineering changes do not establish higher AUC, a higher win rate or a profitable edge. Repeated changes evaluated on an old test period are research diagnostics; final claims require genuinely new chronological observations.
