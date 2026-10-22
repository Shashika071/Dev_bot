# R_100 research signal engine

This package is the Volatility 100 Rise/Fall research path. It reads the
one-minute candles already stored by the synthetic bot and can also page
public Deriv ticks. It does not place orders, and it does not change the
existing one-minute FRAMA bot.

## What it calculates

- Causal features on closed 1m candles, with 5m, 15m, 1h and 1d trends built
  only from complete lower-timeframe buckets.
- Direction models: logistic regression, and XGBoost, LightGBM and CatBoost
  when those libraries are installed. Settings are chosen on the earliest
  training rows only.
- A rule-based regime, a ridge volatility forecast, nearest-neighbor
  outcome checks, and structure rates. Neighbors and structures never
  authorize a trade by themselves.
- Optional two-state Gaussian HMM and optional LSTM, GRU and temporal CNN
  benchmarks on earlier rows. Sequence scores are not the untouched test
  and stay out of the signal.
- Purged, embargoed walk-forward splits and one untouched final test.
- Rise/Fall settlement where an equal price loses. Expected value uses the
  payout you supply. The engine never assumes 80%.

## States

`RESEARCH` is the default. `PAPER` means the test showed some ranking skill
but not enough to publish a signal. `SIGNAL_READY` requires the untouched
test to beat the base-rate log loss and Brier score, clear the payout's
expected-value bound, and produce enough selected signals. Training accuracy
cannot promote a model.

A live card still becomes `NO TRADE` when the entry window, quote, data gap,
strategy, calibration, risk limit or model age fails.

## Labels

The entry price is the open of the first candle at or after the decision plus
the entry delay. The settlement price is the close of the candle that ends at
expiry. A missing candle drops the label. It is not filled.

## API

The synthetic status payload includes `research`. These control routes use the
same control token as the other synthetic actions:

- `POST /synthetic/research/evaluate`
- `POST /synthetic/research/score`
- `POST /synthetic/research/paper`
- `POST /synthetic/research/ticks`

Backtest totals and the paper journal are stored and reported separately.
Paper stake stays at 1 unit.
