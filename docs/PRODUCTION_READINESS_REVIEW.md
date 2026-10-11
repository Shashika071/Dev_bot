# Production readiness review

Reviewed 11 October 2026. Scope: local source, saved Touch model reports, backend tests and frontend build. This is an assessment, not a production certification or a profitability claim. No broker purchases, deployment, or trading-configuration changes were performed as part of the review. The existing test suite did contact public Deriv candle-history endpoints.

## Verdict

Useful research foundation, but not ready for unattended real-money operation. Engineering safety and demonstrated predictive value are separate release requirements; neither can be established by a high displayed confidence percentage.

The repository already includes chronological splits, overlap purging, calibration, baseline comparisons, quote-aware evaluation code, model metadata, and extensive tests. Digit Matches additionally has demo-account enforcement, fixed stakes, daily limits, and explicit handling of uncertain purchases. Preserve these foundations.

## Actual model evidence

The local `data/models/latest_upper.json` and `latest_lower.json` point to Touch models trained on 1 October 2026 for R_100, barrier distance 0.09 price points, duration 540 seconds. These local artifacts are not proof of what a remote deployment currently runs.

| Held-out metric | Upper Touch | Lower Touch |
| --- | ---: | ---: |
| Selected model | LSTM | Weighted ensemble |
| Test samples | 262 | 262 |
| Selected signals | 5 | 224 |
| Selected win rate | 80.00% | 94.64% |
| Selected win-rate interval | 37.55–96.38% | 90.87–96.91% |
| Selected model Brier score | 0.04761 | 0.04598 |
| Constant baseline Brier score | 0.04732 | 0.04380 |
| Demonstrated edge | false | false |

Lower Brier score is better: both selected models were worse than the constant baseline on these held-out tests. Both reports explicitly say historical per-sample quotes were not joined. Their reference break-even probability of approximately 95.6% is an assumption, not a verified executable payout for each observation. These reports therefore cannot establish profitability. A 94–95% win rate can still lose money at a sufficiently small payout.

The Digit Matches trainer correctly labels its output as having no demonstrated payout-adjusted edge. No saved Digit Matches performance report was found in the local model-file inventory examined; its predictive advantage remains unverified here.

## Release blockers, in priority order

### 1. Authenticate the entire application

Evidence: `backend/app/main.py`; `backend/app/api/setup.py:165`, `:318`; `backend/app/api/signals.py:245`; `backend/app/api/train.py:815`; `deploy/fiyola-nginx-devbot.conf`.

Touch configuration, token replacement, signal generation, watcher controls, and training routes have no user-authentication dependency. The supplied reverse-proxy example does not add authentication. Digit Matches password controls do not protect those other routers. If published as supplied without another access-control layer, callers could change trading configuration or trigger actions. CORS is not authentication.

Update: shared authenticated sessions, authorization on every sensitive HTTP and WebSocket route, CSRF protection appropriate to the session design, login rate limits, secure cookies/TLS, and startup rejection of development secrets in production. Verify unauthorized requests cannot read account data, modify credentials, generate purchases, delete data, or start training. The remote host's actual access controls were not inspected.

### 2. Enforce one mandatory Touch execution gate

Evidence: `backend/app/signal_engine/generator.py:129`, `:370`; `backend/app/signal_engine/manual_generate.py:228`; `backend/app/api/signals.py:282`; `backend/app/deriv/auto_trade.py`; `backend/app/deriv/trade_client.py:718`.

Force modes can bypass EV/edge checks, and confidence/force overrides can ignore the paused-alert state. Generated signals can reach the auto-trade path. The buyer obtains another proposal but does not re-evaluate model probability against that proposal's payout before buying.

Update: place authorization, current pause state, model eligibility, exact contract compatibility, quote freshness, conservative EV, and risk limits in the final execution service shared by every entry point. Experimental overrides must not qualify for real-money execution. Cross-barrier scoring needs separate validation for the actual traded barrier; a probability for another barrier is not a calibrated probability for this contract. Re-evaluate EV against the exact final proposal and allowed purchase price.

### 3. Replace full-balance staking with enforced exposure limits

Evidence: `backend/app/deriv/auto_trade.py:25`; `backend/app/api/setup.py:120`.

The Touch path can stake nearly the entire account balance, capped at 10,000. Its buyer does not enforce the same daily loss and open-contract controls found in Digit Matches. A signal-count cap is not an account-loss limit.

Update: remove full-balance staking from production, implement server-side per-order and total-open-exposure caps, daily realized-plus-potential-loss limits, a shared account emergency stop, and explicit account-mode verification. Reserve risk before submission using durable state. Start validation in demo mode with fixed stakes.

### 4. Make purchases durable and crash-safe

Evidence: `backend/app/digitmatch/execution.py:170` onward; `backend/app/digitmatch/worker.py:52`, `:396` onward; `backend/app/deriv/trade_client.py:769`.

Digit Matches appends intent to memory before buying, but persists it only after the cycle returns. The database execution lock is released before that persistence. A crash after broker acceptance can leave no durable intent or contract; a second worker can also operate on stale risk/open-contract state. Touch treats purchase exceptions as ordinary failures without a durable uncertain-order workflow.

Update: commit a unique intent and reserve exposure before transmitting a buy, re-read risk after acquiring the execution lock, retain ownership through durable outcome recording, and reconcile unfinished intents on restart. An unknown result must block another order until reconciled; a client-generated ID alone does not make broker submission idempotent. Ensure lock expiry cannot allow overlapping in-flight submissions.

Reconciliation also needs stronger identity checks: `reconcile_unknown` currently matches statement rows by buy action, amount, and contract ID presence. It does not verify the intent time, symbol, digit, or duration. Fetch contract details and reject ambiguous or unrelated matches.

### 5. Fix Digit Matches freshness and emergency-stop races

Evidence: `backend/app/digitmatch/worker.py:306`, `:377`; `backend/app/digitmatch/execution.py:115` onward.

The live callback updates database state, while `run_cycle` compares the unchanged `CycleState.latest_tick_epoch` snapshot. Ten proposals are requested sequentially and proposal age is passed as zero. The final emergency-stop assessment also uses an earlier risk snapshot. Consequently, the documented guarantee to skip decisions overtaken by a live tick is not enforced by this worker wiring, and a stop activated during quoting can be missed for that cycle.

Update: associate probabilities with the exact feature-window endpoint and tick sequence; check the current sequence and current stop state immediately before transmission; measure actual quote age with a monotonic clock. Refresh the chosen proposal and rescore or skip when the tick changes. Test a tick/stop arriving during quoting, slow responses, and prediction computation overlapping ingestion. The system must fail closed when freshness cannot be established.

### 6. Make model artifacts immutable

Evidence: `backend/app/digitmatch/training.py:307`; `backend/app/digitmatch/registry.py:38`, `:71`; `backend/app/digitmatch/store.py:406`.

Digit Matches dataset IDs hash schema, length, positions and seed, rather than actual tick content. Different rolling datasets of equal shape can receive the same ID and overwrite the same model filename. A checksum is saved but `load_bundle` does not verify it. Promotion merely marks a row active.

Update: hash normalized data content and relevant configuration, use unique immutable run directories, write artifacts atomically, validate checksum/schema/dependencies before loading, and atomically promote a tested candidate with rollback available. Separate research promotion from execution eligibility.

## Improving prediction quality

1. **Collect the evidence needed for the target.** Store precision-correct ticks, gaps and replay flags, exact proposals, quote timestamps, latency, selected probabilities, accepted contract parameters, and broker entry/exit outcomes. Historical t+5 digit labels are a proxy; the broker's accepted five-tick contract is the execution target.
2. **Benchmark simple models first.** For Touch, compare a constant event-rate model and a volatility/barrier-distance baseline with logistic regression and trees. For digits, retain uniform 10% and rolling-frequency baselines. Complexity is justified only by repeatable held-out improvement.
3. **Use repeated chronological evaluation.** Keep calibration and model-selection partitions separate from final testing. Purge overlapping horizons. Freeze thresholds before testing and reserve genuinely new data for subsequent model revisions. Do not repeatedly tune against the same final test.
4. **Measure economic performance at real quotes.** Break-even probability = ask / total payout. Net EV = calibrated probability × total payout − ask. Use uncertainty-aware EV, actual accepted prices and latency, and block-aware intervals for dependent samples. Accuracy or AUC alone is insufficient.
5. **Strengthen Touch promotion criteria.** `_compute_edge` currently allows 25% overall quote coverage and treats fewer than two walk-forward folds as a note. Require adequate quote coverage specifically for selected trades, positive payout-adjusted results with uncertainty accounted for, and stability across independent periods. Determine sample needs from effect size and dependence, rather than an arbitrary tick target.
6. **Run a frozen demo trial.** Log every decision, including rejected trades, and compare forecasts with broker settlement. Monitor calibration, payout-adjusted return, drawdown, prediction/quote latency, data gaps, and uncertain purchases. Retraining should create a challenger for evaluation rather than silently replacing the active model. Stop when evidence degrades.

More indicators or a larger neural network do not guarantee more predictive information. Deriv's synthetic-indices page itself cautions that most synthetic indices may not suit technical indicators and describes apparent historical patterns as coincidental: https://deriv.com/markets/derived-indices/synthetic-indices?tab=cfds . Treat any proposed digit or price-pattern advantage as a hypothesis requiring evidence.

## Verification performed

- `python -m pytest tests -q`: **100 passed, 2 failed**, 7 warnings.
- `test_confidence_override_emits_when_high`: calls live Deriv candle history and fails because current candle confirmation rejects the fixture's direction. Mock this external dependency and explicitly test both acceptance and rejection; do not weaken the production gate to satisfy the test.
- `test_embargo_keeps_non_overlapping`: `DatetimeIndex` reaches `.iloc` in `backend/app/features/dataset.py:222`, causing `AttributeError`. Normalize accepted input types and verify temporal spacing.
- `npm run build`: **passed**, with a warning about a JavaScript chunk larger than 500 kB.
- These are local tests using the installed environment, not a clean-container dependency verification. PostgreSQL migration/restore, multi-process fault injection, deployed authentication, and broker demo end-to-end validation remain required.

## Practical implementation order and release gates

1. Security and execution controls: authentication, no production overrides/full-balance bets, final-proposal EV checks, account-level limits.
2. Order correctness: durable intent, crash recovery, reconciliation, fresh-tick/stop checks, immutable model artifacts.
3. Deterministic CI: repair the two failures; add integration tests for unauthorized requests, lost buy responses, crashes after acceptance, two workers, stop-during-quote, stale data, and model corruption.
4. Operations: separate liveness from readiness, monitor database/model/worker/feed health, alert on unresolved orders, rehearse backup restoration and rollback. `/health` currently always returns `ok`.
5. Predictive validation: collect executable quote/outcome evidence, retrain and evaluate, then complete a frozen forward demo trial. Both engineering gates and evidence gates must pass before considering real-money operation. A safe production system may correctly make no trades.

The README's claim that the application never buys contracts is obsolete: optional Touch auto-trading and demo Digit Matches purchases exist. Update documentation and UI wording alongside the changes so operators understand the actual behavior.
