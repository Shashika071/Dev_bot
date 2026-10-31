# Bot repair and validation — 31 October 2026

The data-handling and measurement defects identified in the review were repaired. This does not establish profitable trading accuracy. The existing five-rule strategy was kept unchanged and all comparisons were predefined; no winning parameters were selected from this sample.

## Downloaded public-data result

43,260 completed R_100 M1 candles, no missing minutes: 30 days plus 60 minutes of warmup. UTC coverage: 2026-09-30 20:29 through 2026-10-30 21:29 (end exclusive). Sri Lanka coverage: 1 October 01:59 through 31 October 02:59.

The final 30% begins at 2026-10-21 21:10 UTC. These are **next-candle close direction proxies**, not executed contracts or public-tick paper observations.

| Current five rules | First 70% | Final 30% holdout |
|---|---:|---:|
| Signals with completed outcomes | 296 | 128 |
| Correct direction | 143 | 57 |
| Win-rate proxy | 48.31% | 44.53% |
| Wilson 95% interval | 42.68–53.99% | 36.21–53.18% |
| Net units at assumed 80% net win return | -38.60 | -25.40 |
| Longest losing streak | 9 | 12 |

The 80% net return assumption requires 55.56% wins to break even. Neither period reached that level. Holdout maximum simulated drawdown was 30.40 units. No trade was placed to obtain these results.

| Exploratory variant | Holdout outcomes | Win-rate proxy | Net units at assumed 80% return |
|---|---:|---:|---:|
| Current five rules | 128 | 44.53% | -25.40 |
| UT alone | 799 | 48.81% | -97.00 |
| Without Supertrend | 162 | 45.68% | -28.80 |
| Without SMA | 138 | 47.10% | -21.00 |
| Without AO | 296 | 49.32% | -33.20 |
| Without RSI | 132 | 44.70% | -25.80 |

Every tested variant had negative hypothetical expectancy at this return assumption. Comparing variants repeatedly can overfit the data. The intervals assume independent observations, and the real contract entry and payout can differ. Collect later paper observations before drawing stronger conclusions; a different period may produce different results.

## Repairs delivered

- History synchronization before signals; ordered tick buffering; strict candle validation; fresh-data and signal-expiry gates; recovery after gaps; stale socket callback isolation and handshake timeout.
- One shared strategy for live and historical analysis; consistent warmup and exclusion of forming candles; no indicators spanning missing candles.
- Public-tick 60-second paper observations using the alert price, explicit missing-data outcomes, persisted history, CSV export, uncertainty, loss streaks, simulated drawdown and payout-sensitive accounting.
- Visible condition checklist, rule explanation, history and research panels.
- Replacement of the copied 12-hour/touch backtest with a paginated chronological research runner and fixed strategy comparisons.
- 47 automated tests passed; production build and lint passed. The saved dataset reproduced the original report byte-for-byte.
- Live browser verification reached READY with changing prices; persisted paper results loaded correctly. CSV export downloaded successfully with 10 history rows, including the paper entry/expiry/exit fields. The small live paper sample is not evidence of accuracy.

## Reproduce this result

The generated `research-output/candles.json` and `research-output/backtest-report.json` are local artifacts and are ignored by Git. Use:

```powershell
npm run backtest -- --input research-output/candles.json --output research-output/replay-report.json
```

SHA256 of the downloaded dataset:

`498CEE0A4030E243F49477435E3010B3E2BE69F4E38AE7333A2405543659D8E4`

SHA256 of the original JSON report:

`654A6C27F743F2801B047884105BAAE01E9BA9D62A5C2ECECF755C1E58C21AE4`
