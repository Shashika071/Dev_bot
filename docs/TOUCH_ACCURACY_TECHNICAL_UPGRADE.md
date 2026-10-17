# Touch Accuracy: nine-group technical profile

This update adds the requested technical-analysis inputs to the dedicated R_100 Touch Accuracy predictor. The contract remains either direction, ±0.01 price points, and 540 seconds. It generates signals and simulated outcomes; it has no order-execution dependency.

## Trained result

Run `20261017T192025406655Z` used the same 6,513,667-tick archive spanning 150.78 days. The requested technical profile selected **logistic regression** using the earlier selection period, with a **97% estimated-probability threshold**. All nine groups have nonzero coefficients in this fitted model. Coefficients establish that the inputs participate in prediction, not that they improve accuracy.

Later diagnostic replay: **393 touches / 407 signals (96.56%)**, with **13.55 signals per full day**. The day-cluster 95% interval was **94.74–98.03%**. These figures describe simulated historical outcomes with the stated entry delay.

All rows below use the common eligible decision grid and the selected 97% threshold:

| Inputs / model | Earlier selection touch rate | Later touches / signals | Later touch rate |
|---|---:|---:|---:|
| Frequency control | 95.93% | 396 / 409 | 96.82% |
| Compact logistic | 94.95% | 387 / 406 | 95.32% |
| Compact XGBoost | 97.60% | 399 / 408 | 97.79% |
| Compact CatBoost | 96.64% | 398 / 408 | 97.55% |
| **Technical logistic — selected expanded model** | **96.15%** | **393 / 407** | **96.56%** |
| Technical XGBoost | 95.91% | 390 / 406 | 96.06% |
| Technical CatBoost | 94.72% | 400 / 408 | 98.04% |

The control-policy search independently selected compact logistic at **97.5%**, which produced **361 / 373 touches (96.78%)** and 12.41 signals per full day on the common diagnostic grid. Its different threshold explains the difference from the table. The expanded model was selected within the user-requested technical profile; it did not beat all compact controls. **Adding the nine groups has not demonstrated an accuracy improvement.** CatBoost's stronger later result was not used to override the earlier selection.

The technical test grid contained 4,066 eligible decisions, compared with 4,329 for the compact history requirement. The 263 exclusions are explicitly counted as additional history abstentions. Test Brier was 0.029824 and pooled AUC was 0.4828, providing no evidence of strong general ranking skill.

Saved files are under `data/accuracy/20261017T192025406655Z/`. The prior `20261017T184616237357Z` compact bundle remains available.

Validation passed: **233 backend tests**, **13 frontend tests**, and the production frontend build. The saved expanded model also loaded successfully and produced matching predictions through its live tick-window path and saved offline feature row. That prediction took approximately 10 milliseconds in the running backend environment. The expanded artifact was activated and the watcher resumed; activation details are recorded in `data/accuracy/technical_upgrade/activation.json`.

## Inputs supplied to the model

The `technical_v2` profile combines the original 42 inputs with **226 technical inputs**, giving **268 input features** and **537 columns after direction interactions**. The same feature calculation runs during training and live prediction.

| Requested area | Implemented measurements |
|---|---|
| Market trend | EMA separation, distance and slope; regression slope; movement efficiency |
| Market structure | Confirmed higher/lower swing highs and lows, structure state, breaks of structure and changes of character |
| Multi-timeframe analysis | Closed 1m, 5m, 15m and 1h context; directional alignment and agreement between adjacent timeframes |
| Support and resistance | Distances to prior range levels and repeated contacts, scaled by recent true range |
| Liquidity / stop hunts | Excursions beyond prior highs/lows that close back inside: **price-sweep proxies**, without claims about observed stop orders |
| Breakouts and retests | Closes beyond prior ranges and the following candle's retest of the previously broken level |
| Candlestick patterns | Bodies, ranges, wick proportions, closing position, engulfing, inside/outside bars, doji, hammer and shooting-star geometry |
| Technical indicators | EMA, RSI, MACD, ATR, ADX/directional indicators and Bollinger measurements |
| Momentum and confirmation | Returns over several candle windows, acceleration, up/down proportions and trend agreement with RSI/MACD |

These are inputs to a learned probability model. They are not nine independent votes, nine mandatory gates, or guaranteed contributions from every feature. A fitted tree may give some inputs no weight. Tree importance, when available, is descriptive rather than evidence that a group improves accuracy.

## Timing and warmup

Only completed UTC-aligned candles enter technical features. A candle covering `[start, end)` becomes usable at `end`; a tick exactly at `end` belongs to the next candle. Swing pivots require two completed candles on each side and become known only when the second right-hand candle closes. Signals never receive those pivots retrospectively.

The model uses at most 12 hours of price history and requires at least 10 continuous hours. Startup retrieves additional history for candle alignment. Stale ticks, gaps over five seconds in the required recent path, or incomplete candles make the model abstain. No prices are forward-filled. Missing live ticks are repaired from public history outside the subscription callback, with bounded requests and retry delays.

The 1m, 5m and 15m contexts require at least 35 completed candles. RSI/ATR/ADX use smoothing coefficient 1/14; MACD uses 12/26/9. Exponential recurrences start from the first observation in the bounded window. The 1h context uses EMA 3/6 and price geometry; it does **not** pretend to have enough bars for standard RSI/MACD/ADX. Indicator periods are fixed research choices, not established optima.

Touch labels retain the original entry and expiry semantics: the first tick strictly after the assumed five-second processing delay anchors the barrier, and expiry is measured from processing start. Entry itself never counts as a touch. Upper/lower outcomes remain independent; both barriers can touch in the same window.

## Training and comparison

The expanded candidates are regularized logistic regression, shallow XGBoost and shallow CatBoost. Each receives its own probability calibration on a separate chronological period. The requested expanded profile selects among the learned technical candidates; frequency and compact-feature models are explicit controls.

Fit, calibration, policy selection and diagnostic replay use the existing 45% / 15% / 20% / 20% chronological divisions. Candidate selection and the selected probability threshold are frozen before computing diagnostic-period model scores. Selection requires at least 100 signals and 10–15 signals per full day on that earlier period. Final-period performance is not an additional tuning or activation gate.

Technical and compact controls train and compare on the same eligible decision timestamps. The report separately counts additional history abstentions and shows how the frozen compact control behaves on its wider native coverage. This prevents longer-history exclusions from being mistaken for better predictions.

The underlying archive has already been analysed in previous experiments. **These results are exploratory and do not establish improved live accuracy.** Future signals carry their model run and feature profile, so new forward outcomes can be distinguished from the original compact model.

## Operation and reproduction

The Touch Accuracy page lists the nine input groups and the active profile. Home shows the active profile above the chart; historical signals retain the profile that generated them. The daily cap remains 15, with a minimum 96-minute separation and no forced minimum number of signals.

To train another expanded candidate without changing the active model pointer:

```powershell
docker compose exec -T backend python -m app.accuracy.cli train --ticks /app/data/parquet/R_100_sqlite_1779170136_1792197658.parquet --output /app/data/accuracy --profile technical_v2 --no-activate
```

Use `--profile compact_v1` to reproduce the original feature family. Old compact artifacts remain loadable. Expanded artifacts must match the profile and exact ordered feature manifest implemented by the code. A running watcher retains its loaded model until restarted.

Training saves the protocol, source and feature-definition hashes, feature matrix, selection trials, model bundle, model comparison, coverage diagnostics and feature-group metadata in a new timestamped directory. The previous artifact is preserved.
