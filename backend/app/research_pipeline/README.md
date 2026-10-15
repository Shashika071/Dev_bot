# Research pipeline

This pipeline tests whether Volatility index ticks, a small indicator set, and a few strategy conditions predict a later outcome. It does not place orders, and it does not change the live signal trainer.

The defaults in `experimental_defaults.json` are experimental. They are not confirmed trading settings. A result from those defaults is not a final test of a real contract.

Unconfirmed items:

- Instrument: standard Volatility 100 (`R_100`) or Volatility 100 (1s) (`1HZ100V`)
- Target: direction, upper touch, lower touch, or a stated combination
- Horizon / expiry
- Barrier distance and units, for a touch target
- Historical contract quotes for that same barrier and expiry

## Data the importer accepts

There is no tick file in this repository. Ticks normally live in the Postgres `ticks` table (`symbol`, `epoch` in seconds, `quote`). Docker has to be running to read that table.

A file also works. Required columns:

- `epoch` (Unix seconds, milliseconds, or an ISO timestamp) or `timestamp`
- `quote` or `price`

Optional:

- `symbol` (required when the file contains more than one instrument)
- Quote file for payouts: `epoch`, `ask_price`, `payout`, `barrier_direction`, and preferably `duration_seconds` and `barrier_distance`

The importer does not invent rows. It drops exact duplicate `(symbol, epoch, quote)` extras and non-positive prices. The same price at two different epochs is kept. Gaps are reported and are not filled.

Screenshot summaries such as "319.4 hours" or "2,127 labels" are not used unless `inspect` computes them from the file or table you pass.

## Setup

From the `backend` directory, with the project environment that already installs `backend/requirements.txt`:

```bash
python -m pip install -r requirements.txt
python -m pip install -r app/research_pipeline/requirements.txt
```

The LSTM is optional. The backend image installs CPU `torch==2.4.1`. If torch is missing, or the LSTM does not improve chronological validation loss, it is skipped.

## Commands

Inspect a file:

```bash
python -m app.research_pipeline inspect --ticks path\to\ticks.csv --symbol R_100
```

Inspect the database from a shell where `DATABASE_URL` is set, or pass the URL. Do not commit that URL.

```bash
python -m app.research_pipeline inspect --database-url postgresql://USER:PASSWORD@postgres:5432/deriv_bot --symbol R_100
```

Inside the running backend container the hostname is `postgres`. From the repository root:

```bash
docker compose exec backend python -m app.research_pipeline inspect --database-url postgresql://USER:PASSWORD@postgres:5432/deriv_bot --symbol R_100
```

Train. This writes a report, model, feature schema, preprocessing, calibration, and a demo signal log. It does not send an order.

```bash
python -m app.research_pipeline train --ticks path\to\ticks.csv --symbol R_100 --out ..\data\research_runs\exp1
```

Useful overrides (still experimental until you confirm them):

```bash
python -m app.research_pipeline train --ticks path\to\ticks.csv --symbol R_100 --target upper_touch --horizon-seconds 540 --barrier-distance 0.30 --sample-every-seconds 540 --quotes path\to\quotes.csv --out ..\data\research_runs\exp1
```

`--lstm` turns on the optional sequence model. `--hypothetical-r 0.25` scores a labeled hypothetical payout where a win earns 0.25 stakes net and a loss costs 1 stake. That is not verified profit. Omit it when you do not have quotes.

`--fast` is only for a short smoke run. It is not the evaluation setting.

Demo inference on the last tick in a file:

```bash
python -m app.research_pipeline infer --model ..\data\research_runs\exp1\upper_touch --ticks path\to\ticks.csv
```

`--batch` writes one JSON line per decision timestamp. A line contains the timestamp, instrument, target, horizon, barrier, calibrated probability, model version, data-quality status, and a signal or no-signal reason. Stale or incomplete data produces no signal. Nothing in this command places a trade.

## What the run does

1. Sort and validate ticks. Keep instruments separate.
2. Build completed 15-second and 1-minute candles. An unfinished candle is not used.
3. Label direction, upper touch, and lower touch from raw ticks. Upper and lower are separate. Rows with a gap or an incomplete path to expiry are dropped. Direction ties are excluded unless `tie_policy` is `up` or `down`.
4. Hold out the newest 20% of the time span. Purge development rows whose label window reaches into that span. The purge uses the horizon, not a fixed number of rows.
5. Compare three feature sets on walk-forward folds: returns and volatility, plus indicators, plus strategy flags. A richer set is kept only when the improvement shows up on a majority of folds.
6. Fit a training-period frequency baseline, a momentum rule, logistic regression, CatBoost, and XGBoost. Search only tree depth, with early stopping on an inner chronological slice.
7. Calibrate and choose any ensemble or threshold on validation rows only. There is no fixed 70% threshold. Without a quote or an explicit hypothetical payout, the demo emits no signal.
8. Shuffle development labels once as a sanity check.
9. Open the final test once, after that selection is frozen.

Touch labels use a relative price-point barrier: upper level = entry price + distance, lower level = entry price - distance. The observation window is `(entry + delay, entry + horizon]`. This matches the live labeler's window on a complete path, and it is stricter about gaps. It is not a broker settlement result unless the quotes and contract rules match.

## Tests

```bash
python -m pytest tests/test_research_pipeline.py -q
```
