# V100 Smart Signal Bot

R_100 (Deriv Volatility 100, not the 1-second index), M1 signal dashboard.
Signal-only: no account token, purchases, or automatic trades. The strategy has **not** demonstrated a profitable edge; see [BOT_VALIDATION.md](BOT_VALIDATION.md).

## Exactly how a signal is selected

All five conditions must pass on the **same completed one-minute candle**:

| Check | RISE | FALL |
|---|---|---|
| UT Bot | New BUY crossover | New SELL crossover |
| Supertrend | Bullish direction | Bearish direction |
| SMA20 | Close above SMA; SMA higher than previous candle | Close below SMA; SMA lower than previous candle |
| Awesome Oscillator | Above zero and higher than previous candle | Below zero and lower than previous candle |
| RSI14 | Strictly greater than 52 and less than 70 | Strictly greater than 30 and less than 48 |

UT settings: key 2, ATR10, ordinary candle closes (Heikin Ashi off). A BUY crosses from at/below the prior ATR trail to above the current trail; SELL is the opposite. Supertrend uses Wilder ATR7, multiplier 2 and HL2. AO is SMA5(HL2) minus SMA34(HL2). RSI14 uses Wilder smoothing. Slope means comparison with the preceding completed candle.

No fresh UT trigger, any failed condition, or both UT directions means **NO TRADE**. Equal SMA/AO values and RSI boundary values fail. Support/resistance is not a filter. No additional filters were fitted to make the historical results look better.

Data checks also have to pass:

- Only R_100, valid OHLC data and consecutive M1 candles; 60 completed candles for warmup before evaluating the next candle.
- Synchronize history after the first live tick; history ends before that tick and buffered ticks are replayed in order. Recovery does not emit historical alerts.
- Reject older and duplicate ticks. Pause and recover when ticks are missing for over 10 seconds, or when disconnected. Device clock and tick timestamp must agree within 10 seconds.
- Live C5 entry window: 10 seconds from the first forming-candle 4/4 detection, or from candle close for a closed-candle signal. Balance and proposal requests must finish before the deadline; expiry is checked again immediately before sending the buy request. Closed entries check both tick time and local delivery time. The live signal disappears when this window expires. Historical chart arrows are not fresh alerts. Paper research retains its separate 5-second post-close entry rule.
- Request up to 5,000 candles and retain at most that many in memory. The server may return fewer (the verification run returned 1,000 per request). Recursive indicator seeds can differ with the loaded history window.

## Run

Node.js 20.19+ and a modern browser are required.

```powershell
npm install
# Optional on a fresh setup; do not overwrite an existing .env:
copy .env.example .env
npm run dev
```

Open the URL printed by Vite. Wait for **READY**, a changing price and populated candles. SYNCING / PAUSED blocks live signals. The right panel shows the last completed candle's checks and the exact rules. Expand the bottom history section for results and CSV export.

```powershell
npm test
npm run build
npm run lint
```

Tests cover accepted/rejected signals, strict boundaries, exclusion of open candles, no future-data changes, gaps, history recovery, stale/duplicate/out-of-order ticks, socket lifecycle, paper outcomes and research statistics.

## What the result metrics mean

**Paper 60s observations:** start at the public tick that delivers a fresh qualified alert. Exit at the first observed tick at or after entry + 60 seconds, at most five seconds late. RISE needs exit > entry; FALL needs exit < entry; ties count as losses. Disconnections, reloads, missing ticks, or missing expiry ticks mark the result UNKNOWN/INVALID. Unknown results are excluded from the win rate and reported separately; exclusions can bias results.

This is **not a broker demo account and not an actual contract settlement**. It does not model order submission, broker entry delay, quoted payout, execution or settlement rules. No trades are placed. Keep the browser open to collect observations. History is stored locally, capped at 2,000 rows. Export CSV to preserve results. Clearing completed history resets the sample; pending observations remain.

**Assumed net win return:** defaults to 80% profit on a win, with a one-unit loss on a loss. This is an editable research assumption, not a broker quote. Net units, drawdown and break-even rate use that assumption retrospectively. Break-even = 1 / (1 + net win return).

**Historical candle proxy:** signal candle close to the next completed candle close. This is a different measurement from paper observations. Small favorable price touches are no longer counted as wins. Win-rate intervals use Wilson 95% intervals; serial correlation can make these too narrow. No percentage is a guaranteed future accuracy.

## Reproducible research

```powershell
# Public market data only; 30 days plus one hour of warmup:
npm run backtest -- --days 30 --net-win-return 0.8
# Re-run a saved dataset without downloading it again:
npm run backtest -- --input research-output/candles.json --output research-output/replay-report.json
```

The runner paginates candle history using server time, excludes the forming candle, and calls the same TypeScript strategy as the live dashboard. It saves the dataset and JSON report under `research-output/` (ignored by Git). It reports the first 70%, the final 30%, four chronological quarters, and six fixed variants: current rules, UT alone, and each confirmation removed in turn. Both a signal and its outcome must fit inside the evaluated window. Gaps restart warmup.

No parameters are optimized. Once you inspect the holdout to choose a strategy, it is no longer unseen: test the chosen change on a later untouched period. The compatibility script `node scripts/backtest-12h.mjs` uses the same runner for a shorter sample.

Data endpoint: `wss://api.derivws.com/trading/v1/options/ws/public`. [Deriv candle history API](https://developers.deriv.com/docs/data/ticks-history/). [Deriv contract descriptions](https://dsmarttrader.deriv.com/explanation).

## In-app Research tab (22 strategies, paper-only)

The **Research** tab compares 22 locked paper strategies (B0 live forming baseline, B1/C1–C6 closed confirmations, S1–S7 patterns, T1–T3 stats, O1–O2 stochastic, N1–N2 CCI/DMI) on public R_100 data. It does **not** place trades, use your API token, or change Chart/Trade settings.

**Always-on after deploy (close tab OK):** redeploy with the `research` and `autotrade` containers. Edge proxies `/research-api/` and `/auto-api/`. Research continues paper collection; VPS Auto can place real C5 ONETOUCH orders after you enable it in **Trade settings → VPS Auto**.

```powershell
docker compose -p deriv -f docker-compose.prod.yml -f docker-compose.ssl-ports.yml --env-file .env.prod up -d --build --force-recreate frontend research autotrade edge
```

**VPS Auto (real trades, browser closed OK):** after deploy with the `autotrade` container, turn on the normal **Auto trade ON** checkbox (same button). That starts the VPS worker with your form credentials. Close the browser — trading continues. Turn Auto OFF to stop the worker. Local test: `npm run autotrade:worker` → `http://127.0.0.1:8788/status`.

**Browser fallback:** if the research container is down, Research still works in the open tab only (stops when the tab closes).

**Optional local collector / reports:**

```powershell
npm run research:collector
npm run research:report
npm run research:replay
```

**How to read results:** the primary result is **Touch within the seven-minute contract window**. A target hit wins immediately and stays a win even if price reverses or the feed disconnects later. A miss requires no touch through expiry with complete coverage; without that coverage and without a confirmed hit, the result is unknown. Endpoint direction at minute 7 is an optional diagnostic and never delays or cancels a Touch win. 80% means 4/5 conditions, not win probability. Compare completed-candle strategies to **B1**; **B0** uses forming-candle timing. Quoted paper P&L uses a separate entry after quote receipt. Rankings remain descriptive and never auto-enable in production.

**Missing outcomes:** check Signal and Quoted H/M/U/P (hit/miss/unknown/pending). Touch win% means hits / (hits + misses). Unknown and pending Touch outcomes are shown separately. Settled P&L and cost exclude unknown and pending outcomes. A high settled win rate can be biased because quick hits survive a connection interruption while possible misses require full coverage through expiry. The matured quoted P&L range shows all unknown quoted outcomes as losses at its lower bound and wins at its upper bound. This is a sensitivity range, not a confidence interval, and excludes pending and unquoted signals. The optional 50/50 endpoint reference is not a Touch win-rate benchmark.

The collector sends a public ping every 30 seconds, checks the acknowledgement, reconnects failed feeds and logs connection/tick-gap diagnostics to `events.jsonl`. The status panel displays disconnects since the collector process started. CSV exports include horizon and touch unknown reasons. These diagnostics do not recover missing historical ticks or turn unknowns into losses.

Start/Archive and restart preserves the prior experiment in its own directory. `research-data/active.json` records the selected run so restarting the collector resumes that run rather than whichever older run has the most observations. The Docker named data volume must be retained when redeploying. `RESEARCH_DATA_ROOT` can override the directory for isolated tests or offline copied data.

`npm run research:replay -- --id <experiment-id>` reads `journal.jsonl` and verifies the full `session.json` checkpoint: observations, quotes, candle history, settings, cooldowns and lifecycle state. A count match alone is insufficient. Replay fails explicitly for legacy tick-only data, missing initial settings/history or incomplete checkpoint journals. Keep the journal and checkpoint together when copying a run for diagnosis.

Note: older `compare-filters.mjs` / `runTouchBacktest(..., filterConfig)` reports echoed filter settings but **did not apply** optional filters inside the backtest loop.

## Directional ±0.03 touch research

This separate measurement asks whether the next completed minute reaches signal close +0.03 for RISE, or signal close −0.03 for FALL. It does not change the live signal rules or paper settlement metric.

```powershell
# Download/resume the maximum history exposed by the public endpoint:
node scripts/download-max-history.mjs
# Audit any missing minute and append candles completed during the download:
node scripts/audit-history-cache.mjs
# Generate the report and per-signal CSV from the cached data:
node scripts/touch-backtest.mjs
# Compare a seven-minute window using exactly the same saved candles and signals:
node scripts/touch-backtest.mjs --minutes 7
```

Results are in `research-output/touch-003/TOUCH_REPORT.md`, `touch-report.json`, and `signal-touches.csv`. The 31 October 2026 run obtained 525,598 valid M1 candles, from 31 October 2025 through 31 October 2026, with one unavailable minute and a partial oldest candle excluded. 4,587 of 5,233 qualified signals touched 0.03 in the signal direction (87.66%). Both directions touched in 3,978 cases; the matched 50/50-direction baseline was 88.01%. A high excursion rate is not evidence of better direction selection or profitable contract trading. Refer to the report for monthly results, exact timestamps, entry-reference sensitivity and limitations.

The seven-minute report is saved separately under `research-output/touch-003-7m/`. It requires seven consecutive completed candles, uses their high/low extremes for touches and the seventh close for end direction, and includes first-touch timing at minute resolution. It does not change the live bot's one-minute signal rules or its 60-second paper tracker.
