# R_100 Touch Accuracy: implemented profile and research results

**Technical-profile update:** The expanded nine-group implementation and its current results are documented in [Touch Accuracy technical upgrade](TOUCH_ACCURACY_TECHNICAL_UPGRADE.md). The 42-feature results below describe the original compact profile; `data/accuracy/latest.json` identifies the active profile.

Built 18 October 2026, Asia/Colombo. The confirmed target is **R_100, either upper or lower Touch, ±0.01 relative price points, 540 seconds**, with a desired 10–15 signals per 24 hours. The user requested that this mode optimize touch accuracy independently of payout economics.

The dedicated **Touch Accuracy** screen is now implemented. It compares calibrated models, generates probability-based signals, and records fresh simulated touch outcomes. It has its own model artifact and signal history. It does not route signals into the order-execution engine.

## Actual result of the implemented profile

Training used **6,513,667 ticks over 150.78 days**. Decisions were sampled every 600 seconds. The source file is `data/parquet/R_100_sqlite_1779170136_1792197658.parquet`.

The selection rule was fixed before final-period metrics were calculated. It selected **XGBoost**, with an estimated-probability threshold of **97%**. The operating policy permits at most 15 signals per Asia/Colombo calendar day and enforces at least 5,760 seconds (96 minutes) between them. Because decision opportunities occur every ten minutes, successful consecutive opportunities normally have 100-minute spacing. The replay averaged **14.28 signals per full day**.

| Candidate | Earlier selection touch rate | Later replay touches / signals | Later replay touch rate | Later Brier | Later AUC |
|---|---:|---:|---:|---:|---:|
| Frequency baseline | 95.40% | 413 / 434 | 95.16% | 0.030554 | 0.484 |
| Logistic regression | 94.20% | 419 / 431 | 97.22% | 0.030568 | 0.486 |
| **XGBoost: frozen selection** | **95.82%** | **413 / 430** | **96.05%** | **0.030550** | **0.497** |
| CatBoost | 94.93% | 426 / 434 | 98.16% | 0.030554 | 0.509 |

All table policies use the selected 97% probability threshold and the same cap/cooldown rules. Each candidate can select different timestamps/directions, so the touch rates are policy results, not paired superiority tests. AUC is pooled across the two direction outcomes; even the per-direction frequency comparator can differ from 0.5 under changing direction frequencies.

For the selected XGBoost policy, the ordinary Wilson interval is **93.76–97.52%**, and the day-cluster bootstrap interval on full days is **93.76–97.83%**. Neither interval is a guarantee of future accuracy or a correction for the full strategy search. The displayed 97% model probability is an estimate; the observed later touch rate was 96.05%.

CatBoost's 98.16% later result is interesting, but choosing it after seeing that period would turn the evaluation period into selection data. It was weaker on the earlier selection period. A future challenger trial can compare it on newly arriving data; the current frozen policy remains XGBoost. Near-0.5 AUC and tiny Brier differences do not establish strong general predictive skill. A high touch rate is partly a property of the nearby barrier itself.

The final period covers approximately 16 September–17 October UTC. Earlier research in this project has already examined these historical dates. Accordingly, this build explicitly labels the entire comparison **exploratory**, with fresh forward validation pending. No claim of an untouched research history is made.

The complete fitted artifact and report are under:

- `data/accuracy/20261017T184616237357Z/bundle.pkl`
- `data/accuracy/20261017T184616237357Z/protocol.json`
- `data/accuracy/20261017T184616237357Z/selection.json`
- `data/accuracy/20261017T184616237357Z/report.json`
- `data/accuracy/latest.json` points to the current profile.

## What the bot now uses

The feature extractor is shared by training and live prediction. Its **42 past-only features**, expanded to **85 columns with direction interactions**, include:

- Price movement over 5, 15, 30, 60, 120, 300, and 600 seconds.
- Tick volatility and price ranges over 60, 300, and 600 seconds.
- Barrier distance divided by recent tick volatility.
- The last two fully closed 1-minute and 5-minute candles: body, upper/lower wick, range, body fraction, and close position.
- Engulfing and inside-bar geometry.
- Upper/lower direction and interactions with the same features.

The comparison trains a frequency baseline, standardized regularized logistic regression, shallow XGBoost, and shallow CatBoost. Trees use unweighted probability objectives. Each learned candidate receives its own Platt calibration on separate calibration data; no arbitrary indicator score is added to the resulting probability. This also avoids the exact 100% tail probabilities seen in the exploratory isotonic experiment.

Fit, calibration, selection, and final replay use 45% / 15% / 20% / 20% of elapsed time. Outcome windows crossing a split boundary are removed. The respective usable decision counts were 9,757 / 3,250 / 4,334 / 4,329. Both directions belong to the same time partition. The scaler is fitted only on the fit partition; calibration and thresholds never use final-period outcomes.

Seven probability thresholds, from 95% through 99%, are compared on the selection period. Candidates need at least 100 selected outcomes and 10–15 signals per full day there. The ranking uses the day-cluster lower touch-rate bound, then Brier score, then deterministic tie-breaking. The latest bundle is only written when a policy meets these requirements. Future retraining can legitimately find no qualifying policy.

Live decisions are causal: at each ten-minute decision point, the bot uses only ticks already available, chooses one direction, checks the trained probability threshold, and applies spacing and the daily cap. It does not select a day's best signals retrospectively. There is no daily minimum that forces a weak prediction.

## Timing and data correctness

The assumed processing start is decision time plus five seconds. The entry reference is the **first tick strictly after processing start**. The nine-minute expiry remains measured from processing start. The entry tick itself cannot count as a touch of its own ±0.01 barrier. These conventions follow the entry/expiry descriptions in [Deriv SmartTrader's trading explanation](https://smarttrader.deriv.me/en/trading).

Touch labels use tick prices rather than candle closes. R_100's two-decimal prices are compared in integer hundredths, avoiding floating-point equality errors at this one-point-in-the-last-decimal barrier. Upper and lower touches are separate events; both can occur within one window.

Missing future ticks, stale history, or gaps greater than five seconds produce an unresolved result. Closed-candle features exclude unfinished candles. Live scoring needs a fresh timestamp and continuous recent history. Manual entry timing may differ from the five-second replay assumption, so the forward log reports simulated touch outcomes rather than broker-confirmed contract results.

Signal history survives restarting the mode: earlier timestamps restore the cap and cooldown. Restarting does not create another signal for the same decision point. The watcher stops on server restart; use the Start button to resume it. Its JSON-lines log is `data/accuracy/signals.jsonl`.

## How to start

1. Refresh the existing dashboard at `http://localhost:5173`.
2. Open **Touch Accuracy** in the sidebar. The trained profile should already appear.
3. Click **Start accuracy signals**. Use **Enable signal sound** in the sidebar for browser alerts.
4. Leave the backend running. The signal service continues when navigating to other screens. Keep the dashboard open for sound/browser notifications.
5. Review new signals and their resolved nine-minute outcomes in the Forward signals table. **Stop** ends this mode.

The accuracy profile has fixed contract parameters and its own 15-signal cap. Editing the legacy Configuration page does not change this profile. Existing legacy signals are independent and are not counted toward this profile's cap.

If the sidebar or API has not refreshed after a source update, refresh the browser. Restart the containers only while other training jobs are idle:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml restart backend frontend
```

To reproduce training using the installed backend environment:

```powershell
docker compose exec -T backend python -m app.accuracy.cli train --ticks /app/data/parquet/R_100_sqlite_1779170136_1792197658.parquet --output /app/data/accuracy --profile compact_v1
```

Run this from the project root. A new timestamped accuracy artifact is created. A currently running accuracy watcher keeps its frozen bundle until stopped and started again. Reusing the same historical test period is exploratory; reserve genuinely new dates for subsequent confirmation.

## Research coverage and priority

The accompanying [primary-source review](NEAR_TOUCH_RESEARCH_SOURCES.md) contains twelve findings and eighteen source links, including original papers on candlesticks, technical-rule data snooping, discrete barrier crossings, tabular models, calibration, and abstention. Its general discussion also distinguishes economics from accuracy; this implemented profile follows the user's accuracy-only objective.

| Family | What was checked | Decision for this build |
|---|---|---|
| Constant/random direction | Fixed upper/lower, seeded random, training-frequency direction | Mandatory controls; close barriers already touch frequently |
| Momentum / reversal | 60s and 300s momentum and mean reversion | Fixed-rule experiments; no universal winning rule established |
| Candle patterns | Completed 1m/5m body, wick and range geometry; engulfing, inside bars; primary candlestick studies | Numeric features tested jointly; no manufactured probability from named-pattern votes |
| Trend confluence | Existing EMA/RSI/Bollinger/Donchian and multi-timeframe code inspected | Do not assume correlated indicators provide independent confirmations; validate later as separate ablations |
| Volatility / first passage | Empirical tick movement, discrete crossings, normalized barrier distance | Relevant baseline/features; continuous near-zero barrier formulas need caution |
| Logistic / XGBoost / CatBoost | Fixed modest model comparison, separate calibration, frozen policy selection | Implemented and trained |
| LSTM / Transformer / stacking | Existing earlier model results and primary model/calibration literature | Deferred until a fresh comparison justifies their added complexity |
| News / order flow / volume | Official synthetic-market properties | No external news or order-book features invented for this feed |
| Selective prediction | Thresholds, spacing, daily cap, chronological replay | Implemented; lower coverage is allowed when confidence is insufficient |

The first fixed-rule study and its entry-alignment sensitivity are preserved under `data/research/near_touch_feasibility`. An earlier fixed 12/day completed-candle rule produced 348/361 touches (96.40%) under the corrected next-tick alignment; the seeded direction control produced 349/362 (96.41%). A separate isotonic-calibrated exploratory model comparison selected the frequency baseline and produced 450/465 touches (96.77%) with a different, more concentrated daily-cap schedule. These are different policies, not interchangeable accuracy claims for the final running profile.

The best-supported next improvement is a frozen forward trial of the current policy and prespecified challengers, including CatBoost. Add richer trend or sequence features only when they improve probability quality and selected-signal accuracy on new dates. More indicators by themselves do not establish higher confidence.
