"""Timestamped economic calendar and optional fundamental context.

No headlines or macro values are inferred from candles. External context is
data, never instructions. Missing calendar coverage blocks signal publication.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import httpx

CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"}
COUNTRY_CURRENCY = {"United States": "USD", "Euro Area": "EUR", "Germany": "EUR", "France": "EUR",
                    "Italy": "EUR", "Spain": "EUR", "United Kingdom": "GBP", "Japan": "JPY",
                    "Australia": "AUD", "New Zealand": "NZD", "Switzerland": "CHF", "Canada": "CAD"}
FUNDAMENTALS = {21: ("Interest rates", "interest_rates"), 22: ("Inflation / CPI", "inflation"),
                23: ("Employment / NFP", "employment"), 24: ("GDP", "gdp"),
                25: ("Central bank speeches", "central_bank_speeches"),
                27: ("Geopolitical events", "geopolitics"), 28: ("Market sentiment", "sentiment")}


def epoch(value) -> int:
    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Context timestamps require an explicit timezone")
        return int(stamp.timestamp())
    raise ValueError("Invalid timestamp")


async def load_context(settings, now: int, duration: int) -> dict:
    """Load either an operator's structured feed or the documented TE calendar API."""
    try:
        if settings.forex_context_file:
            path = Path(settings.forex_context_file)
            if path.stat().st_size > 2_000_000:
                raise ValueError("Context file too large")
            result = json.loads(path.read_text(encoding="utf-8"))
        elif settings.forex_context_url:
            url = str(settings.forex_context_url)
            if not url.startswith("https://"):
                raise ValueError("Context URL must use HTTPS")
            headers = {"Authorization": f"Bearer {settings.forex_context_token}"} if settings.forex_context_token else {}
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
                response = await http.get(url, headers=headers)
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError("Context response too large")
                result = response.json()
        elif settings.trading_economics_api_key:
            start = datetime.fromtimestamp(now - 86400, timezone.utc).strftime("%Y-%m-%d")
            end = datetime.fromtimestamp(now + duration + 86400, timezone.utc).strftime("%Y-%m-%d")
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
                response = await http.get(f"https://api.tradingeconomics.com/calendar/country/All/{start}/{end}",
                                          params={"c": settings.trading_economics_api_key, "f": "json"})
                response.raise_for_status()
                rows = response.json()
            if not isinstance(rows, list) or not rows:
                raise ValueError("Calendar provider returned no coverage")
            events = []
            for row in rows:
                currency = row.get("Currency") or COUNTRY_CURRENCY.get(row.get("Country"))
                if currency not in CURRENCIES:
                    continue
                date = str(row["Date"])
                # TE documents its timezone-less calendar dates as UTC.
                stamp = datetime.fromisoformat(date.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                events.append({"currency": currency, "title": row.get("Event", "Economic event"),
                               "at": int(stamp.timestamp()), "impact": "high" if int(row.get("Importance", 3)) >= 3 else "medium",
                               "time_uncertain": row.get("DateSpan") not in (0, "0"),
                               "actual": row.get("Actual"), "forecast": row.get("Forecast"), "previous": row.get("Previous")})
            # API can truncate to 1000 rows; do not assert complete coverage then.
            if len(rows) >= 1000:
                raise ValueError("Calendar response may be truncated; configure a complete context feed")
            result = {"source": "Trading Economics calendar", "as_of": now,
                      "coverage_start": now - 3600, "coverage_end": now + duration + 3600,
                      "currencies": sorted({e["currency"] for e in events}), "events": events}
        else:
            return {"error": "Configure FOREX_CONTEXT_URL, FOREX_CONTEXT_FILE, or TRADING_ECONOMICS_API_KEY for current news coverage"}
        if not isinstance(result, dict):
            raise ValueError("Context must be a JSON object")
        # Sample/operator files may set clock=server so as_of/coverage track the
        # scan clock without inventing events. Absolute event times still apply.
        if result.get("clock") == "server":
            result = {
                **result,
                "as_of": now,
                "coverage_start": result.get("coverage_start", now - 3600),
                "coverage_end": result.get("coverage_end", now + duration + 3600),
            }
            if isinstance(result["coverage_start"], str) or isinstance(result["coverage_end"], str):
                result["coverage_start"] = epoch(result["coverage_start"])
                result["coverage_end"] = epoch(result["coverage_end"])
            result["coverage_start"] = min(int(result["coverage_start"]), now - 3600)
            result["coverage_end"] = max(int(result["coverage_end"]), now + duration + 1800)
        return result
    except Exception as exc:
        # Exception URLs can contain provider keys, so do not expose their messages.
        return {"error": f"Context feed unavailable or invalid ({type(exc).__name__})"}


def evaluate_context(context: dict, symbol: str, now: int, duration: int) -> dict:
    currencies = {symbol[3:6], symbol[6:9]}
    blockers = []
    source = str(context.get("source", "No provider"))[:200]
    reason = context.get("error")
    events = []
    if not reason:
        try:
            age = now - epoch(context["as_of"])
            if not 0 <= age <= 900:
                raise ValueError("Calendar is stale or future dated (maximum age 15 minutes)")
            if epoch(context["coverage_start"]) > now - 3600 or epoch(context["coverage_end"]) < now + duration + 1800:
                raise ValueError("Calendar does not cover the full holding period and news buffers")
            if not currencies.issubset(set(context.get("currencies", []))):
                raise ValueError("Calendar does not cover both currencies")
            if not isinstance(context.get("events"), list):
                raise ValueError("Calendar events are missing")
            for event in context["events"]:
                if not isinstance(event, dict) or not event.get("currency") or not event.get("title"):
                    raise ValueError("Invalid calendar event")
                stamp = epoch(event.get("at"))
                impact = event.get("impact")
                if impact not in {"low", "medium", "high", "holiday"}:
                    raise ValueError("Unknown calendar event importance")
                if event["currency"] not in currencies | {"ALL"}:
                    continue
                relevant = now - 3600 <= stamp <= now + duration + 1800
                if event.get("time_uncertain"):
                    relevant = now - 86400 <= stamp <= now + duration + 86400
                if relevant:
                    events.append(event)
                    if impact in {"high", "holiday"}:
                        blockers.append(f"News blackout: {event['currency']} {str(event['title'])[:150]}")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc)
    if reason:
        blockers.append(str(reason))
    factors = [{"id": 26, "name": "Economic calendar", "status": "blocked" if blockers else "clear",
                "detail": str(reason or ("; ".join(blockers) if blockers else "Fresh coverage for both currencies across the complete holding period"))}]
    macro = context.get("fundamentals") if not reason else {}
    if not isinstance(macro, dict):
        macro = {}
    for number, (name, key) in FUNDAMENTALS.items():
        rows = macro.get(key) or []
        if not isinstance(rows, list):
            rows = []
        fresh = []
        for row in rows:
            try:
                if row.get("currency") not in currencies | {"ALL"}:
                    continue
                observed = epoch(row["as_of"])
                if not 0 <= now - observed <= 86400 or not row.get("source") or not row.get("summary"):
                    continue
                fresh.append(row)
            except (TypeError, KeyError, ValueError, AttributeError):
                continue
        summaries = [f"{row['currency']}: {str(row['summary'])[:350]} ({str(row['source'])[:100]})" for row in fresh[:4]]
        risk = any(row.get("risk") == "high" for row in fresh)
        if risk:
            blockers.append(f"High fundamental risk: {name}")
        factors.append({"id": number, "name": name, "status": "blocked" if risk else "context" if fresh else "unavailable",
                        "detail": " | ".join(summaries) if summaries else "No fresh sourced context; not inferred from technical indicators."})
    return {"ready": not blockers, "reason": "; ".join(blockers) if blockers else "Calendar coverage ready",
            "source": source, "blockers": blockers, "events": events, "factors": factors}
