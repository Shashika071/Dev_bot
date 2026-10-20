# Forex bot audit — 20 October 2026

**Assessment: the current training accuracy should not be trusted yet.** The demo
execution safeguards have useful test coverage, but the training dataset, labels,
validation and model activation need corrections. This was a read-only code/data
audit; no trading state or application source was changed, no user model was trained,
and no trades were placed. This report is the only added deliverable.

## Priority findings

| Priority | Finding | Evidence and consequence | Required correction |
|---|---|---|---|
| P1 | Higher-timeframe training features contain future information | `backend/app/forex/ml/dataset.py:28–54` selects H1/H4 by candle opening timestamp, then uses its eventual closing price. A reproduced decision at epoch 1792289700 used an H4 close at 1792296000 (105 minutes later). Changing that future close changed the training feature. Live inputs filter closed bars, so training and inference also differ. | Select only bars whose closing timestamps are at or before the decision, using the same function in training and live prediction. Add tests that mutate still-forming H1/H4 bars. |
| P1 | Labels do not represent the specified expiry | `dataset.py:81–92` uses the open of the entry-containing M15 candle and the close of the expiry-containing candle. An entry at 5 and expiry at 14405 selected the close at 15300, 895 seconds late. With missing history it can choose a price days later. | Use timestamp-matched tick/contract data for execution-faithful labels, with bounded price age and gap rejection. If only candles are available, explicitly train a candle-aligned proxy target rather than claiming exact contract results. |
| P1 | Training outcomes overlap the test period | `backend/app/forex/ml/train.py:43–48` samples hourly, labels four hours ahead and splits 80/20 without purging. Reproduction: the last training label ended 10,805 seconds after the first test decision. | Remove training rows whose label end overlaps the validation period. Use purged chronological validation, then an untouched final test period. |
| P1 | Every completed model is immediately activated | `train.py:70–82` writes directly over the active artifact regardless of accuracy, calibration, baseline performance or economic results. `predict.py:12–19` considers metadata alone sufficient for readiness. | Save a candidate version, validate it, and promote only after explicit acceptance criteria. Publish a complete version atomically; verify model, metadata, feature order, symbol and duration together. |
| P1 if externally reachable | Control API has no authentication | `backend/app/api/forex.py:90–100` permits token replacement and enabling execution without an authentication dependency. Start/stop/train/download are also unprotected. The supplied production nginx proxy adds no authentication. External access controls may exist outside this repository; they were not verified. | Protect the entire control API before exposing it beyond a trusted local environment. CORS alone is not authentication. |
| P2 | An installed ML filter can be bypassed when inputs are missing | `predict.py:31–33` returns `ready=False, hybrid_passed=False`; `service.py:373–379`, `422` and `456` only enforce disagreement when ready is true. Technical analysis accepts 60 M15 bars while ML needs 80. Rules-only operation without a model is advertised, but failure of an installed filter is indistinguishable from that mode. | Separate disabled, unavailable, invalid and ready model states. Block ML-enabled entries when an installed model cannot produce valid features. Test the EURUSD path. |
| P2 | Reported accuracy differs from live decisions | `train.py:59` treats every probability below 0.55 as FALL; live prediction uses WAIT for probabilities between 0.45 and 0.55. It also treats a Rise loss label as the other class, although a tie loses both strict Rise and strict Fall contracts. | Evaluate the same RISE/FALL/WAIT policy as deployment, handle ties explicitly, report coverage and selected-trade results, and compare against actual payout breakeven. |
| P2 | History checks are too weak | `backend/app/forex/history.py:83–108` turns incomplete groups into complete higher-timeframe candles. A single M15 bar can become an H4 bar. Readiness checks only the number of rows; resumption refreshes the latest page and extends the oldest edge without repairing internal gaps. | Verify bucket completeness, timestamp alignment, closed status, OHLC validity and shared coverage. Detect and repair internal gaps; exclude incomplete intervals from training. |
| P2 | Download and training can run on inconsistent files | `backend/app/forex/jobs.py:39–46` does not block training during a download. `history.py:57` overwrites Parquet in place while the trainer may read it. Model publication likewise writes directly to live files. | Use immutable training snapshots and atomic file replacement; coordinate concurrent jobs. |
| P2 | Feature construction can crash on missing RSI | `dataset.py:47` applies `float()` to a possibly None RSI. Reproduced with flat candles; the resulting TypeError aborts construction. No such sampled zero-loss window was found in the inspected local history. | Handle flat and one-sided RSI limits deliberately; reject non-finite features consistently in training and inference. |
| P2 | Chart can show the wrong market's candles | `frontend/src/components/ForexChart.tsx:184–190,226` clears React state when switching market/timeframe, but not the chart series or overlays. If the new request fails, the previous market's candles remain beneath the new heading. | Clear series, levels and markers on identity changes; only apply responses matching the current market/timeframe. |
| P2 | Chart precision and positioning need correction | `ForexChart.tsx:140` omits price precision, leaving the library's two-decimal default; line 329 refits on new candles; line 42 rounds marker times into potentially future candles. | Use instrument-specific price precision, preserve manual scrolling, and map signals to their containing candle. |

## Verified and unverified behavior

- **146 selected backend tests passed**, including analysis, execution, calendar,
  quotas, service orchestration, history and ML tests. These tests do not cover the
  demonstrated training defects and do not establish profitability.
- The frontend production build passed. Its test runner currently fails with
  "No test files found"; there are no automated frontend tests.
- Fake-broker tests cover positive demo-account verification, four-hour expiry,
  fresh finite quote validation, Stop rechecking, durable daily limits, and no
  automatic retries after ambiguous purchases.
- Training currently supports **EURUSD only**, using M15/H1/H4 features and a
  logistic-regression filter. The wider six-timeframe/37-factor checklist is a
  separate rules engine, not a model trained on all factors or all Forex pairs.
- News/calendar checks are enforced. Directional fundamental interpretation,
  geopolitics and sentiment are not inferred automatically; they require sourced
  feed entries. Missing fundamental rows remain optional/unavailable.
- Purchase settlement/P&L reconciliation and an operator interface for resolving
  unknown purchases are not implemented. The unknown-attempt block is conservative
  and persists across restarts.
- The local backend at `127.0.0.1:8000` was unreachable during this audit. Runtime
  status, remote deployment, external access control and live broker integration
  were not verified.

## Local data inspected

The saved EURUSD history runs approximately from **17 October 2025 to 19 October
2026**, about 367 days. The downloader targets three years but the saved data does
not currently contain three years.

| Timeframe | Rows |
|---|---:|
| M15 | 24,248 |
| H1 | 6,059 |
| H4 | 1,556 |

No duplicate epochs or obvious OHLC ordering violations were found in these files.
This is not a completeness certification. No Forex `meta.json` was found in the
local `data/models/forex_eurusd_rise_fall` location, so a completed local Forex
training result could not be assessed. Other deployment/model locations were not
assumed to be equivalent.

## Recommended order

1. Correct causal features and target labels; add reproductions as regression tests.
2. Validate history and create an immutable data snapshot.
3. Purge overlapping labels; evaluate the actual entry/WAIT policy on unseen data.
4. Add model acceptance/versioning and block failures of an enabled ML filter.
5. Protect exposed controls and correct chart identity/precision.
6. Retrain only after those corrections; then measure demo outcomes with actual
   entries, expiries, payouts and settlement records.

References: [Deriv Rise/Fall semantics](https://doc-deriv.readme.io/docs/risefall)
and [scikit-learn time-series splitting and gap support](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
