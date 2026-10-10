# Digit Matches research desk

This is a separate option inside the existing Deriv touch bot. The touch dashboard is unchanged. Open **Digit Matches** in the sidebar.

The desk studies one contract only:

- Instrument: standard Volatility 100 Index. The legacy symbol is `R_100`. The app accepts that symbol only when the broker’s display name is exactly `Volatility 100 Index`. It will not switch to Volatility 100 (1s).
- Contract: Digit Matches (`DIGITMATCH`).
- Duration: 5 ticks.
- Prediction: one digit from 0 to 9.
- Win condition, per Deriv: the last digit of the contract’s final tick equals the selected digit.
- Execution: demo accounts only. There is no real-money path and no stake progression after a loss.

A result of “no demonstrated edge” is a valid outcome. The software does not claim a predictive advantage.

## API family

Digit Matches uses the **classic Deriv WebSocket v3 API** and a classic API token.

- Socket: `wss://ws.derivws.com/websockets/v3?app_id=YOUR_APP_ID`
- Auth message: `{"authorize": "<token>"}`
- Demo proof: the authorize response must include `is_virtual: 1`. If that field is missing, or it is not 1, the account is rejected.
- Contract flow, from Deriv’s Digit Matches page: `active_symbols` → `contracts_for` → `proposal` → `buy` → `proposal_open_contract`.
- History and live prices: `ticks_history` and `ticks` with `subscribe: 1`.
- Reconciliation: `statement` and `profit_table`.
- Cleanup and keepalive: `forget` and `ping`.

This path does **not** use PAT tokens (`pat_...`), OAuth, or the one-time-password trading socket. Those belong to a different API family already used by the touch bot’s optional buy path. Mixing the two request schemas is intentionally refused.

Account requirements:

1. A Deriv **demo** account.
2. An API token created on that demo account with **read** and **trade** scopes.
3. An application id from [developers.deriv.com](https://developers.deriv.com/).

The token stays in the backend environment. It is not written to the browser, exports, or logs. Requests are logged by message type only.

## Modes

The first launch stays in **OBSERVE**. Nothing switches mode by itself.

| Mode | What it does |
| --- | --- |
| OBSERVE | Stores ticks and can score a promoted model. Never buys. |
| DEMO EXPLORE | Buys a fixed-stake demo contract on a cooldown after settlement, without requiring a proven edge. Results are an execution experiment. |
| DEMO FILTERED | Buys only when calibrated probability is at least break-even plus the configured margin, and expected value is positive. It may stay idle. |

`break_even_probability = ask_price / total_payout`

`estimated_net_ev = calibrated_probability × total_payout − ask_price`

`total_payout` is the amount returned on a win, including the stake. Net profit on a win is `total_payout − ask_price`. Quotes come from the live proposal. An 800% payout is not hard-coded.

If a new tick arrives after the probabilities were built and before the buy, the cycle skips. It does not send the stale decision.

Stopping, pausing, or the emergency stop blocks **new** orders. A contract that is already purchased keeps running at the broker until it settles.

## Historical target

Training labels the digit at `t+5`. That is a research proxy. A live five-tick contract uses the broker’s entry and exit ticks, which depend on when the purchase is accepted. Stored demo contracts use the broker’s settlement as the result that counts.

Historical ticks do not include historical payout quotes. The evaluator leaves payout-adjusted return empty unless you separately run a clearly labelled assumed-payout simulation. That simulation is not shown as trading P/L.

## Windows setup

Requirements: Docker Desktop, or Python 3.11, Node 18, and PostgreSQL 16.

### Docker (recommended)

```bat
cd deriv-touch-bot
copy .env.example .env
```

Leave `DM_DERIV_API_TOKEN` and `DM_DERIV_APP_ID` empty to reuse the App ID and API token already saved for the touch bot in Configuration. Set `DM_ALLOW_DEMO_CONTROL=true` on this PC so the Digit Matches buttons work. Do not put a real-account token in either place.

```bat
docker compose up -d --build
```

- UI: http://127.0.0.1:5173
- API health: http://127.0.0.1:8000/health
- Digit Matches health: http://127.0.0.1:8000/api/digitmatch/health

The dev compose binds the UI and API to localhost. It does not start in DEMO EXPLORE or DEMO FILTERED.

Postgres data is in `pgdata`. Do not delete that folder, and do not run `docker compose down -v`.

### Without Docker

```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set `DATABASE_URL` to your local Postgres instance, for example:

`postgresql+asyncpg://deriv:change_me_in_production@127.0.0.1:5432/deriv_bot`

```bat
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.digitmatch.worker
python -m app.digitmatch.train_worker
```

In another terminal:

```bat
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

## Collect history and train

1. Open **Digit Matches**.
2. Confirm the connection says the account is demo. If credentials are empty, the status stays unconfigured and no balance is invented.
3. Choose **Download history**. The worker pages `ticks_history` backward, checkpoints the oldest epoch, and can be cancelled. The default goal is 100,000 ticks. The broker may return fewer. That goal is not a sample-size proof.
4. When enough clean ticks exist, choose **Train candidate**. Training uses chronological train / calibration / selection / test partitions (50% / 15% / 15% / 20%), purges labels that cross a boundary, and fits temperature scaling on the calibration partition only.
5. Read the held-out log loss, Brier score, calibration bins, and the comparison with the uniform baseline. If the block-bootstrap interval crosses zero, the screen treats the comparison as inconclusive.
6. Promote a candidate only if you want the live desk to use it. Promotion is manual.

CSV export is on the same panel. An imported CSV records a sha256 and a note. Payout columns in an import are ignored.

## Start automatic demo exploration

1. Promote a model if you want the digit choice to come from it. Without a model, explore does not guess a digit.
2. Set stake, cooldown, and daily limits. Defaults: stake 1, cooldown 30 seconds after settlement, 50 trades per day, daily loss 20, daily profit stop 20. The reset timezone defaults to `Asia/Colombo` and is stored in the database.
3. Click **Demo explore**.
4. The worker buys at most one contract, then waits for broker settlement and the cooldown. This mode does not require a proven edge. Treat the P/L as an experiment.

## Enable filtered demo trading

1. Train and promote a model.
2. Leave the margin at the saved value, or set one yourself. With no historical payout quotes, the trainer does not pretend to have optimised the margin.
3. Click **Demo filtered**.
4. The desk buys only when the live proposal’s break-even plus the margin is cleared and expected value is positive. If that never happens, it stays idle. Thresholds are not lowered to force a trade.

## Remote hosting

Keep `DM_REQUIRE_APP_AUTH=false` only on a machine where the API is bound to localhost.

If the API is reachable beyond localhost:

- Set `DM_REQUIRE_APP_AUTH=true` and a long `DM_APP_PASSWORD` and `DM_SESSION_SECRET`.
- Put TLS in front of the service. The existing nginx examples under `deploy/` are a starting point; do not publish this app without TLS and the app password.
- Leave CORS on the explicit origin list in `CORS_ORIGINS`.

## Backup and restore

```bat
docker compose exec postgres pg_dump -U deriv deriv_bot > deriv_bot.sql
docker compose exec -T postgres psql -U deriv -d deriv_bot < deriv_bot.sql
```

Stop the workers before restoring so a live purchase cannot overlap the restore.

## Logs

Workers use structured logs. Authorize payloads, tokens, and password fields are redacted. If you still see a secret, treat it as a bug and rotate the token.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Credentials missing | `DM_DERIV_APP_ID` and `DM_DERIV_API_TOKEN` in `.env`, then restart the digitmatch worker. |
| PAT token refused | Create a classic demo API token. Do not reuse a `pat_` token. |
| Real account rejected | The token’s account returned `is_virtual` other than 1. |
| Contract unavailable | The broker did not offer `DIGITMATCH` for exactly 5 ticks on Volatility 100 Index. The app will not switch instrument or duration. |
| Uncertain purchase | The buy was written and the response was lost. Use the reconciliation banner. Do not send the buy again. The worker tries `statement` once on reconnect. |
| Training error about empty partitions | Not enough clean ticks after gap removal. Download more history. |
| Port already in use | The dev UI is `127.0.0.1:5173` and the API is `127.0.0.1:8000`. |

## Tests

From `backend`:

```bat
python -m pytest tests/test_digitmatch.py tests/test_migrations_smoke.py -q
```

From `frontend`:

```bat
npm run build
```

Synthetic tests check software behaviour. They are not evidence of profitability.
