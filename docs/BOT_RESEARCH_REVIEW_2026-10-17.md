# Bot research review - 17 October 2026

The bot has two separate problems: its current local setup cannot produce normal model signals, and several evaluation/execution defects make future results unreliable. Fixing those defects can improve correctness and reliability; it cannot establish a profitable trading advantage by itself.

This review covers the current working tree, the running local Docker stack, read-only database/API checks, the available historical dataset, existing tests, isolated synthetic reproductions, and official documentation. Existing uncommitted work was preserved. No purchases, account changes, training launches, model promotions, service restarts, or trading-setting changes were performed. An already-running training job was left running.

**What is happening now**

Read-only snapshot at **19:59 on 17 October 2026, Asia/Colombo**:

| Check | Observed result | Meaning |
| --- | --- | --- |
| Docker services | Backend, worker, frontend running; PostgreSQL healthy | Processes are available |
| Confirmed contract settings | 0 database rows; `/setup/current` returns `{}` | The worker has no confirmed target contract |
| Saved/registered models | No model artifacts; 0 model registry rows; 0 active models | No completed model is available for normal worker inference |
| Training | Already running; pipeline start logged at 19:20:56 | About 39 minutes elapsed at the snapshot; completion was not observed |
| Quotes | 0 database rows; worker reports 0 collected | No executable payout history exists in this local database |
| Live feed | Connected; 1,195 ticks collected during this worker run | Tick ingestion is operating at the snapshot |
| Signals / outcomes | 0 / 0 database rows | Local results cannot explain or measure any past financial losses |
| Watcher | Stopped | No manual analysis watcher is running |
| Automatic execution | Server gate off; user execution off | Purchases are intentionally disabled |

These are local observations, not an inspection of a remote deployment. The older `docs/PRODUCTION_READINESS_REVIEW.md` cites model artifacts from 1 October that are absent now. Its old performance numbers must not be treated as the current model's results.

The larger historical dataset **does exist**: `candel/market.sqlite` and the staged Parquet contain **6,513,667 R_100 ticks**, from **19 May 2026 05:55:36 UTC** through **17 October 2026 00:40:58 UTC**, spanning **150.78 days**. A scan found 18 gaps above five seconds, one above 60 seconds, and a maximum gap of 94 seconds. This is much denser than the live PostgreSQL table, which had 8,273 ticks across 99.13 hours at an earlier snapshot. The running training log confirms it selected the 6.5-million-row history. Lack of raw historical ticks is therefore not the main explanation for this training run.

**Most important confirmed defects**

**1. The economic edge check can approve a losing strategy.**

`backend/app/ml/trainer.py:615-616` averages break-even probabilities across all matched test observations. It then compares that average with the win-rate confidence interval of only the selected trades. Those populations can have very different payouts. `backend/app/ml/validation_gates.py:99-105` does not require positive selected-trade net return, although `backend/app/ml/evaluation.py` computes it.

A synthetic reproduction using the actual evaluator and gate returned:

| Quantity | Result |
| --- | ---: |
| Selected trades | 1,000 |
| Selected win rate | 90.00% |
| Selected break-even probability | 95.00% |
| Overall break-even, including 9,000 unselected observations | 27.50% |
| Selected win-rate confidence lower bound | 87.98% |
| Selected P&L with one-dollar stakes | **-$52.63** |
| Economic gate, with other requirements supplied as passing | **Passed / Edge supported** |

This proves a gate defect; it is not a measurement of the user's trading losses or proof that an entire real training run would pass every promotion requirement.

Repair: use the exact selected, contract-matched trades; require positive payout-adjusted return with uncertainty estimated using time blocks. Report selected-trade break-even separately. An average win rate versus an average payout threshold is insufficient when trade payouts vary.

**2. Training can join payouts from the wrong contract.**

`backend/app/api/train.py:685-704` filters quotes by symbol and direction, but omits barrier, expiry, contract type and currency from the dataframe passed to evaluation. `backend/app/ml/quote_match.py:62-100` subsequently matches direction and prior timestamp only. Training a different barrier or duration can reuse incompatible historical payouts and count them as quote coverage.

Repair: persist and match the complete normalized contract specification: symbol, Touch/No Touch type, direction, barrier distance and units, duration, currency and quote time. Missing or incompatible quotes must remain unmatched. Never present assumed historical payouts as executable evidence.

**3. Labels do not consistently describe the contract that would be entered.**

`backend/app/features/labels.py:56-82` shifts the observation start for entry delay, but retains the decision-time spot/barrier and decision-time expiry. With a five-second delay, a relative barrier must instead be anchored to the accepted entry spot, and the contract duration must run from actual entry.

Synthetic example: decision spot 100; delayed entry spot 101; upper relative barrier +0.2; all later prices 101. The code reports a win against 100.2, but a delayed purchase would need to reach 101.2.

The same labeler also accepts an incomplete future path after only a minimum tick count. A 540-second contract with five non-touch ticks through second 50 is marked `resolved=True, touched=False`, even if the dataset ends there. A later tick at second 600 does not establish what happened during the missing path. The separate research labeler correctly rejects the gapped example.

`backend/app/signal_engine/outcome_resolver.py:63-102` also evaluates from signal creation time, ignoring recorded actual entry time/price, while `:136` can mark an outcome non-hypothetical simply because a manual entry was recorded.

Repair: share one entry/label contract across training, replay and live evaluation. Require valid entry coverage and adequate future coverage; unresolved no-touch paths must not become losses. Store broker entry, expiry, barrier and settlement explicitly, and keep hypothetical signal results separate from real contract results. Retrain affected candidates after correcting labels.

**4. Training does expensive repeated work without useful progress reporting.**

The labeler scans the full tick array for every entry. `backend/app/ml/dataset_builder.py:56-63` builds a snapshot per resolved entry; `backend/app/features/snapshot.py:37` scans/filter-copies the history again; sequence construction also scans it. Roughly 24,000 non-overlapping decisions on 6.5 million ticks imply about 156 billion comparisons for each full-array scan performed per decision, before repeated feature calculations.

The running backend showed approximately 109% CPU and 693 MiB memory in one sample. Its status remained a generic training message. This supports a performance concern; it does not prove a deadlock or identify the current stage conclusively.

Repair: sort once, calculate entry/window positions with binary search, slice bounded arrays, cache shared rolling calculations, and preserve exact training/live feature parity. Report stage, processed/total decisions, elapsed time and failure details. Put training in a separate durable job process so API reloads do not erase job state. Benchmark before and after using the same dataset and verify identical outputs.

**5. Training, activation and the UI disagree.**

`backend/app/api/train.py:757-760` saves trained versions inactive and requires explicit promotion. `/train/promote` exists, but the frontend has no promotion action or handling of active/eligible model state. The worker loads DB-active versions (`backend/app/worker.py:246-254`), while UI wording says a trained model still turns on.

Deleting a model removes files without synchronizing registry state (`backend/app/api/train.py:271-354`). The worker can retain its cached model; removing an active registry entry also does not unload the old generator pack.

Repair: show candidate, gate blockers, eligible and active states explicitly, with promotion available only when its actual requirements pass. Use the registry consistently for manual and worker inference. Deactivate and invalidate cached models before artifact deletion. Report the active model's exact symbol, target, barrier and expiry.

**6. Execution controls have gaps if execution is later enabled.**

The current disabled execution gate is working as intended. However:

- `backend/app/execution/risk.py:35` defines realized-loss recording, but no application code calls it. The daily loss gate therefore does not ingest actual settled losses.
- `backend/app/deriv/trade_client.py:183-186` falls back to the first available account when demo is requested but absent. A synthetic real-only account list was selected despite the demo preference.
- `backend/app/execution/boundary.py` checks execution permission but does not require the signal to be validated and eligible for execution. Research/force/cross-barrier analysis can reach the buyer when permissions are enabled.
- `backend/app/deriv/auto_trade.py` supports near-full-balance stakes without reserving remaining daily/open-contract exposure. A daily realized-loss check alone does not bound the next possible loss.
- Cross-barrier analysis only requires different barriers (`backend/app/signal_engine/manual_generate.py:269-280`). A near-barrier model can be applied to a harder, farther trade. Its probability is not calibrated for that target.
- The buyer obtains a final proposal without recalculating conservative EV against its actual price/payout. Purchase IDs are written to free-text notes rather than a durable order/settlement ledger.

Repair: require the intended account type and identity; prohibit research modes from execution; validate the actual contract and fresh final-proposal EV; commit an order intent and reserve exposure before sending; reconcile unknown results before allowing retries; account for broker settlements idempotently. Use fixed small demo stakes during validation. Full-balance staking should not be part of this validation.

**7. Market-data isolation and disconnect recovery need correction.**

`backend/app/deriv/client.py:259-264` replaces the tick handler while retaining previous-symbol subscriptions. `backend/app/collector/tick_collector.py:64-85,114` neither unsubscribes the old feed nor rejects a mismatched incoming symbol. A mocked switch from R_100 to R_50 caused an R_100 tick to be persisted as R_50. This is a demonstrated code path, not evidence that current R_100 historical rows are contaminated.

`backend/app/deriv/client.py:315-324` handles exception-based disconnection but not normal stream exhaustion. A clean-close mock did not reconnect and retained `connected=True`.

Feature age is measured before slow candle/quote requests and reused later (`backend/app/worker.py:314,398`; manual generation has the same pattern), so the final decision can use a snapshot older than its reported age.

Repair: route by symbol/subscription, reject mismatches before storage, unsubscribe on switches, handle every closure path with cleanup/backoff, and recalculate feature age immediately before the decision. Review provenance for data collected after symbol changes.

**Other improvements that matter**

- Calibration: the trainer admits a calibration split at its configured minimum, passes that same minimum to the calibrator, and then automatically selects isotonic at that size (`trainer.py:286,551`; `calibration.py:71`). The intended small-sample Platt fallback is effectively bypassed. A 20-row synthetic calibration produced 100% at the top, with only 18 local observations and an 82.41% Wilson lower bound. Separate minimum admission size from isotonic eligibility; require minority-class support and independent reliability checks. [Scikit-learn warns that isotonic calibration tends to overfit with substantially fewer than 1,000 observations.](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html)
- Evaluation boundaries: outer walk-forward selection uses a default 12% final holdout, while final evaluation uses configurable `lockbox_fraction`. A larger configured holdout can overlap selection data. Use one shared timestamp boundary everywhere (`walk_forward_eval.py:225`; `trainer.py:256-258`).
- Research P&L: the separate research pipeline applies the median development-period payout to final test trades (`research_pipeline/io_ticks.py:115-121`; `run.py:199,251`). Its note acknowledges absent test-period quotes. Treat this as a payout scenario until actual test quotes are joined.
- Honest status: `/health` always returns OK; worker status never expires; the UI's Ready indicator largely reflects cooldown. Provide one readiness response with confirmed settings, active compatible models, data/quote age, cap/cooldown, current execution permission and last rejection reason.
- Data readiness: `/train/data-info` estimates labels from elapsed min/max span and tick count, without using its returned gap flags to determine readiness. Count actually usable windows. Include the staged historical source in that display, since it differs greatly from the live table.
- Deployment: sensitive configuration, training, deletion and order routes lack user authentication. The current default ports bind to localhost, but public reverse-proxy deployment needs access control. Actual remote access controls were not inspected.
- UI connections: use the shared WebSocket URL helper everywhere and stop reconnection after component disposal. A custom REST API URL can currently coexist with status sockets pointed at localhost.
- Metrics: manual confidence-override signals can be stored as validated while also marked research-only (`generator.py:650,679`). Keep exploratory outcomes out of validated performance/drift statistics. Use actual registry model IDs instead of Python hashes in manual generation.

**How to improve the strategy after correctness is repaired**

Keep one target contract fixed during each experiment: instrument, Touch direction, barrier, expiry, entry delay and payout policy. Compare constant touch frequency, a simple volatility/barrier baseline, logistic regression and one tree model before adding ensembles or LSTM complexity. A model should improve held-out probability quality and selected-trade economics over these baselines consistently across chronological periods.

Use actual quotes and broker settlements. If `A` is the paid amount, `P` is total payout including returned stake, and `p` is calibrated touch probability, then break-even probability is `A/P` and expected net profit is `p*P-A`. For example, paying $1 to receive $1.05 total requires **95.24% wins just to break even**. A 95% win rate would still lose on average in that example.

Freeze thresholds before final testing, purge overlapping horizons, preserve an untouched chronological test period, and estimate return uncertainty using blocks that reflect dependence. Save every decision, including rejected ones, with its feature time, full contract, model version, probability, uncertainty, quote and rejection reason.

Then run a frozen forward demo trial with fixed stakes. Measure calibration, actual net return, drawdown, quote/decision latency, rejected signals and unresolved orders. Promote a challenger only after its evidence passes; retraining must not silently replace the active version.

Additional indicators are hypotheses, not demonstrated predictive information. [Deriv states that its synthetic indices are generated using a secure random number generator and cautions that most synthetic indices may not suit technical indicators.](https://deriv.com/markets/derived-indices/synthetic-indices) This does not prove every possible strategy fails, but it makes independent payout-adjusted validation essential.

**Recommended implementation order**

| Order | Deliverable | Acceptance evidence |
| --- | --- | --- |
| 1 | Correct labels, quote identity and economic gates | The synthetic losing strategy is rejected; delayed-entry and incomplete-window cases resolve correctly; wrong-contract quotes never match |
| 2 | Reliable model lifecycle and truthful status | UI distinguishes saved/eligible/active; incompatible or revoked models unload; missing prerequisites are visible |
| 3 | Faster, observable training | Identical features/labels before and after optimization; benchmark and real progress on the 6.5-million-row dataset |
| 4 | Repair risk, account selection and order lifecycle | Loss settlements update limits; demo never selects real; exposure is reserved; unknown purchases reconcile; research signals cannot buy |
| 5 | Correct feed routing, freshness and recovery | Symbol-switch, clean-close, slow-quote and worker-failure cases pass deterministic tests |
| 6 | Prove predictive value in a frozen demo experiment | Actual selected-trade payouts/settlements, stable baseline improvement, calibration and uncertainty-aware positive return evidence |

**Verification completed**

- Existing backend suite: **130 passed**, 19 warnings, 59.89 seconds.
- Existing frontend suite: **5 passed**.
- Frontend production build: **passed**, with an existing 824.78 kB bundle warning.
- Read-only API checks and PostgreSQL counts confirmed the current empty setup/model/quote/signal/outcome state.
- Historical Parquet epoch scan confirmed row count, range and gap counts.
- Isolated synthetic/mock reproductions demonstrated economic-gate acceptance of a losing strategy, wrong-contract quote acceptance, incomplete labels, delayed-entry barrier mismatch, symbol misrouting, clean-close recovery failure and demo-account fallback. These were diagnostics, not additions to the regression suite.

Passing the existing suite does not cover these missing cases or demonstrate profitability. No completed current model, live trade history, remote deployment audit, or broker demo end-to-end execution was available in this review.

**Minimal reproduction of the economic-gate defect**

Run from `backend` with the project's Python dependencies installed. This uses arrays only and makes no broker requests:

```python
import numpy as np
from app.ml.evaluation import evaluate_model
from app.ml.validation_gates import evaluate_promotion_gates

y = np.r_[np.ones(900), np.zeros(100), np.zeros(9000)]
p = np.r_[np.full(1000, .99), np.full(9000, .1)]
be = np.r_[np.full(1000, .95), np.full(9000, .2)]
report = evaluate_model(
    "repro", "upper", y, p,
    breakeven_probs=be,
    quote_prices=np.ones(10000),
    quote_payouts=1 / be,
    quote_matched=np.ones(10000, dtype=bool),
)
gate = evaluate_promotion_gates(
    selected_signal_count=report.selected_signal_count,
    selected_win_rate=report.selected_win_rate,
    selected_win_rate_ci_lower=report.selected_win_rate_ci_lower,
    mean_breakeven=float(be.mean()),
    quote_match_rate=1., selected_quote_match_rate=1.,
    selected_matched_count=1000, calibration_samples=100,
    calibration_both_classes=True, unresolved_rate=0.,
    eligible_folds=5, fold_win_fraction=1.,
    lockbox_beats_baseline=True,
)
print(report.quote_based_net_return, gate.passed)
# Approximately -52.6315789474, True
```
