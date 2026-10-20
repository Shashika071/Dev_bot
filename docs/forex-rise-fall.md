# Forex Rise / Fall (4-hour holds, 2 signals/day)

Patient multi-timeframe Rise/Fall research on Deriv **forex** majors (`frx*`).
This path is separate from legacy Touch / Volatility Index tools.

## Product rules

| Rule | Value |
|------|--------|
| Contract | Zero-barrier Rise/Fall only |
| Direction | **RISE → CALL**, **FALL → PUT** |
| Expiry | **4 hours** (fixed) |
| Daily cap | **2** signals (Asia/Colombo day) |
| Cooldown | At least 4 hours between signals |
| Default pairs | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF |
| Auto-buy | Demo account only, when execution checks pass |

Analysis uses closed candles on **1m / 5m / 15m / 1h / 4h / 1d**. M1/M5 are context.
D1 and H4 set bias, H1 confirms, M15 supplies the trigger (breakout or pullback rejection).
The checklist score is **not** a win probability.

## Environment

```env
BOT_MODE=forex
# Default no-subscription path:
FOREX_PUBLIC_NEWS=true
# Optional overrides (first configured wins over public feeds):
# FOREX_CONTEXT_FILE=/app/data/forex/context.json
# FOREX_CONTEXT_URL=https://your-feed.example/forex-context.json
# FOREX_CONTEXT_TOKEN=optional_bearer_token
# TRADING_ECONOMICS_API_KEY=your_key
```

Public mode uses the ShowMyTrades free calendar (economic releases) plus official
central-bank RSS headlines. Operator/TE feeds still take priority when configured.
High-impact events during the 4-hour hold still block entries. Sample
`clock: server` files cannot authorize trades.

## Context JSON schema

```json
{
  "source": "Operator calendar",
  "as_of": 1792400400,
  "coverage_start": 1792396800,
  "coverage_end": 1792500000,
  "currencies": ["EUR", "USD", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"],
  "events": [
    {
      "currency": "USD",
      "title": "Nonfarm Payrolls",
      "at": 1792420000,
      "impact": "high"
    }
  ],
  "fundamentals": {
    "interest_rates": [],
    "inflation": [],
    "employment": [],
    "gdp": [],
    "central_bank_speeches": [],
    "geopolitics": [],
    "sentiment": []
  }
}
```

### Fields

- `source` — short provider label shown in the UI.
- `as_of` — the provider's actual update time, as Unix seconds or ISO-8601 with timezone. Must be within **15 minutes** of scan time. Refresh it only when the underlying calendar has actually been refreshed.
- `coverage_start` / `coverage_end` — must cover `[now − 1h, now + duration + 30m]`.
- `currencies` — must include both legs of every watched pair.
- `events[].impact` — `low` | `medium` | `high` | `holiday`. High/holiday in the hold window **blocks** entry.
- `fundamentals` — optional. Each row needs `currency`, `as_of` (≤24h old), `source`, `summary`. `risk: "high"` blocks entry.

Timestamps may be Unix integers or ISO-8601 strings with an explicit timezone (`Z` or offset).
The example timestamps are illustrative and will become stale. Sample files, including
files with `clock: "server"`, cannot authorize signals or purchases. The scanner never
extends a provider's coverage by changing its timestamps. A source must explicitly cover
both currencies and the whole holding period; an empty event list is valid only within
that genuinely verified coverage.

## Trading Economics adapter

Set `TRADING_ECONOMICS_API_KEY`. The bot pulls the calendar country feed for the hold
window and maps importance ≥3 to `high`. Truncated API responses (1000+ rows) fail closed;
use a complete `FOREX_CONTEXT_FILE` or URL instead.

## How to run

1. Copy `.env.example` → `.env`, keep `BOT_MODE=forex`, and configure your current calendar feed. The sample can be inspected, but keeps execution blocked.
2. Start the stack (`docker compose up -d` or your usual deploy script).
3. Open **Forex Rise / Fall**, select majors, optionally save a **demo** API token.
4. **Scan now** to inspect the 37-factor checklist; **Start demo auto-trading** to emit up to two 4-hour signals per Colombo day.

## Honest limits

- Rise/Fall is an **options expiry**, not an open-ended spot FX hold with stops.
- Exchange-wide FX volume and live spread are not on the public Deriv feed; those checklist rows stay unavailable or proxy.
- Geopolitics and sentiment appear only when your context feed supplies them.
- A calendar adapter is a scheduled-event risk filter, not an automatic directional forecast of news. Interest-rate/CPI/NFP/GDP/speech/sentiment commentary requires sourced `fundamentals` rows; absent rows remain unavailable.
- Supply/demand, liquidity and currency-strength calculations are price-based estimates, not observations of all orders or stop-losses.
- The two-signal limit is a maximum across pairs, not a promise to trade twice. Missing evidence can mean zero signals.
- This implementation has no demonstrated live win rate. Validate it on a demo account before drawing conclusions from performance.

## Persistence and execution

The scanner runs every five minutes while started. **Scan now** while stopped only
previews analysis. An encrypted token and enabled demo execution are configured in the
Forex page. Starting the scanner does not bypass account verification, contract
availability, technical, news or quota checks. No real-account fallback exists.

`MODEL_DIR/forex.sqlite` stores settings, signals, daily quotas and purchase attempts.
Keep this file on persistent local storage shared by API processes. A transactional
reservation enforces two signals per Asia/Colombo day, at least four hours between
signals, and no overlap with a previous contract. A failed/blocked attempt still uses
its daily signal slot. Configured positive daily-loss limits also conservatively bound
the total committed demo stakes; winnings do not replenish this budget.

The bot records a purchase attempt before contacting Deriv. A timed-out, interrupted or
unconfirmed purchase remains unresolved and blocks later purchases across restarts.
It is never retried automatically. Verify the demo account history before reconciling
such a record; there is no automatic override. Actual settlement/P&L reconciliation
is not implemented in this path; consult the broker account history for results.

Closed bars are filtered at data acquisition time. Technical freshness, the news
holding window and Stop are checked again immediately before buy. Acknowledged
purchase timing extends the overlap guard so authentication/network delay cannot
cause another trade to start too early. The contract's maximum premium is bounded
by the requested fixed stake (0.35–10 account currency units).

## API references

- [Deriv Forex and Rise/Fall availability](https://deriv.com/markets/forex)
- [Deriv contract discovery](https://developers.deriv.com/comparison/contracts-for/)
- [Deriv proposal response fields](https://developers.deriv.com/comparison/proposal/)
- [Trading Economics calendar by country](https://docs.tradingeconomics.com/economic_calendar/country/)
