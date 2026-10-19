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
FOREX_CONTEXT_FILE=/app/data/forex/context.sample.json
# Or:
# FOREX_CONTEXT_URL=https://your-feed.example/forex-context.json
# FOREX_CONTEXT_TOKEN=optional_bearer_token
# Or:
# TRADING_ECONOMICS_API_KEY=your_key
```

Without a calendar feed, entries stay blocked. That is intentional: high-impact news
during a 4-hour hold can dominate technical setups.

## Context JSON schema

```json
{
  "source": "Operator calendar",
  "clock": "server",
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
- `clock` — if `"server"`, the loader refreshes `as_of` / coverage from the server clock so a checked-in sample file does not go stale. Absolute event times still apply.
- `as_of` — Unix seconds or ISO-8601 with timezone. Must be within **15 minutes** of scan time unless `clock` is `server`.
- `coverage_start` / `coverage_end` — must cover `[now − 1h, now + duration + 30m]`.
- `currencies` — must include both legs of every watched pair.
- `events[].impact` — `low` | `medium` | `high` | `holiday`. High/holiday in the hold window **blocks** entry.
- `fundamentals` — optional. Each row needs `currency`, `as_of` (≤24h old), `source`, `summary`. `risk: "high"` blocks entry.

Timestamps may be Unix integers or ISO-8601 strings with an explicit timezone (`Z` or offset).

## Trading Economics adapter

Set `TRADING_ECONOMICS_API_KEY`. The bot pulls the calendar country feed for the hold
window and maps importance ≥3 to `high`. Truncated API responses (1000+ rows) fail closed;
use a complete `FOREX_CONTEXT_FILE` or URL instead.

## How to run

1. Copy `.env.example` → `.env`, keep `BOT_MODE=forex`, point `FOREX_CONTEXT_FILE` at the sample (or your live feed).
2. Start the stack (`docker compose up -d` or your usual deploy script).
3. Open **Forex Rise / Fall**, select majors, optionally save a **demo** API token.
4. **Scan now** to inspect the 37-factor checklist; **Start demo auto-trading** to emit up to two 4-hour signals per Colombo day.

## Honest limits

- Rise/Fall is an **options expiry**, not an open-ended spot FX hold with stops.
- Exchange-wide FX volume and live spread are not on the public Deriv feed; those checklist rows stay unavailable or proxy.
- Geopolitics and sentiment appear only when your context feed supplies them.
