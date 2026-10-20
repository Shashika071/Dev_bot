"""Timestamped economic calendar and optional fundamental context.

No headlines or macro values are inferred from candles. External context is
data, never instructions. Missing calendar coverage blocks signal publication.

Providers (first configured wins):
1. FOREX_CONTEXT_FILE / FOREX_CONTEXT_URL
2. TRADING_ECONOMICS_API_KEY
3. Public feeds (ShowMyTrades calendar + official central-bank RSS) when enabled
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.forex.news_feeds import load_public_context

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


def _age_seconds(as_of, now: int) -> int | None:
    try:
        return int(now) - epoch(as_of)
    except (TypeError, ValueError, OverflowError):
        return None


async def load_context(settings, now: int, duration: int) -> dict:
    """Load operator feed, TE calendar, or public no-key feeds."""
    try:
        if settings.forex_context_file:
            path = Path(settings.forex_context_file)
            if path.stat().st_size > 2_000_000:
                raise ValueError("Context file too large")
            result = json.loads(path.read_text(encoding="utf-8"))
            result.setdefault("feed_meta", [{
                "id": "context_file", "name": "Configured context file", "kind": "operator_feed",
                "ok": True, "as_of": result.get("as_of"), "items": len(result.get("events") or []),
            }])
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
            result.setdefault("feed_meta", [{
                "id": "context_url", "name": "Configured context URL", "kind": "operator_feed",
                "ok": True, "as_of": result.get("as_of"), "items": len(result.get("events") or []),
            }])
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
                stamp = datetime.fromisoformat(date.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                events.append({"currency": currency, "title": row.get("Event", "Economic event"),
                               "at": int(stamp.timestamp()), "impact": "high" if int(row.get("Importance", 3)) >= 3 else "medium",
                               "time_uncertain": row.get("DateSpan") not in (0, "0"),
                               "actual": row.get("Actual"), "forecast": row.get("Forecast"), "previous": row.get("Previous")})
            if len(rows) >= 1000:
                raise ValueError("Calendar response may be truncated; configure a complete context feed")
            result = {"source": "Trading Economics calendar", "as_of": now,
                      "coverage_start": int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()),
                      "coverage_end": int((datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp()),
                      "currencies": sorted({e["currency"] for e in events}), "events": events,
                      "feed_meta": [{"id": "trading_economics", "name": "Trading Economics", "kind": "economic_releases",
                                     "ok": True, "as_of": now, "items": len(events)}]}
        elif getattr(settings, "forex_public_news", True):
            result = await load_public_context(now, duration)
        else:
            return {"error": "Configure FOREX_CONTEXT_URL, FOREX_CONTEXT_FILE, TRADING_ECONOMICS_API_KEY, or enable FOREX_PUBLIC_NEWS"}
        if not isinstance(result, dict):
            raise ValueError("Context must be a JSON object")
        if result.get("clock") == "server":
            result = {**result, "error": "Sample/server-clock context cannot authorize trades; provide genuinely timestamped calendar coverage"}
        return result
    except Exception as exc:
        return {"error": f"Context feed unavailable or invalid ({type(exc).__name__})"}


def _source_rows(context: dict, *, now: int, calendar_effect: str, calendar_detail: str) -> list[dict]:
    rows = []
    as_of = context.get("as_of")
    rows.append({
        "id": "calendar",
        "name": str(context.get("source") or "Economic calendar")[:160],
        "kind": "economic_releases",
        "age_seconds": _age_seconds(as_of, now),
        "as_of": as_of,
        "effect": calendar_effect,
        "detail": calendar_detail[:400],
        "items": len(context.get("events") or []) if isinstance(context.get("events"), list) else 0,
    })
    for feed in context.get("feed_meta") or []:
        if not isinstance(feed, dict):
            continue
        if feed.get("id") in {"showmytrades_calendar", "trading_economics", "context_file", "context_url"}:
            continue
        effect = "context" if feed.get("ok") and (feed.get("items") or 0) > 0 else (
            "unavailable" if not feed.get("ok") else "clear")
        rows.append({
            "id": str(feed.get("id") or feed.get("name") or "feed")[:80],
            "name": str(feed.get("name") or "Feed")[:160],
            "kind": str(feed.get("kind") or "headline")[:60],
            "currency": feed.get("currency"),
            "age_seconds": _age_seconds(feed.get("as_of"), now) if feed.get("as_of") else None,
            "as_of": feed.get("as_of"),
            "effect": effect,
            "detail": (
                f"{feed.get('items', 0)} recent items"
                if feed.get("ok") else f"Unavailable ({feed.get('error') or 'error'})"
            )[:400],
            "url": feed.get("url"),
            "items": feed.get("items") or 0,
        })
    return rows


def evaluate_context(context: dict, symbol: str, now: int, duration: int) -> dict:
    currencies = {symbol[3:6], symbol[6:9]}
    blockers = []
    source = str(context.get("source", "No provider"))[:200]
    reason = context.get("error")
    if context.get("clock") == "server":
        reason = "Sample/server-clock context cannot authorize trades; provide genuinely timestamped calendar coverage"
    events = []
    calendar_effect = "blocked"
    calendar_detail = ""
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
                    tagged = {
                        **event,
                        "effect": "blocks" if impact in {"high", "holiday"} else "watched",
                        "age_seconds": now - stamp,
                    }
                    events.append(tagged)
                    if impact in {"high", "holiday"}:
                        blockers.append(f"News blackout: {event['currency']} {str(event['title'])[:150]}")
            calendar_effect = "blocks" if blockers else "clear"
            calendar_detail = (
                "; ".join(blockers) if blockers
                else f"Fresh coverage for {', '.join(sorted(currencies))}; {len(events)} events in hold window"
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            reason = str(exc)
            calendar_detail = reason
    if reason:
        blockers.append(str(reason))
        calendar_effect = "blocked"
        calendar_detail = str(reason)
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
    headlines = []
    for row in (context.get("headlines") or []) if isinstance(context.get("headlines"), list) else []:
        try:
            if row.get("currency") not in currencies | {"ALL"}:
                continue
            published = epoch(row["as_of"])
            headlines.append({
                "currency": row["currency"],
                "title": str(row.get("summary") or row.get("title") or "")[:280],
                "source": str(row.get("source") or "")[:120],
                "as_of": published,
                "age_seconds": now - published,
                "link": row.get("link"),
                "effect": "blocks" if row.get("risk") == "high" else "context",
            })
        except (TypeError, KeyError, ValueError):
            continue
    sources = _source_rows(context, now=now, calendar_effect=calendar_effect, calendar_detail=calendar_detail)
    attribution = context.get("attribution") if isinstance(context.get("attribution"), dict) else None
    return {
        "ready": not blockers,
        "reason": "; ".join(blockers) if blockers else "Calendar coverage ready",
        "source": source,
        "blockers": blockers,
        "events": events,
        "factors": factors,
        "sources": sources,
        "headlines": headlines[:20],
        "attribution": attribution,
        "public": bool(context.get("public")),
        "as_of": context.get("as_of"),
        "age_seconds": _age_seconds(context.get("as_of"), now),
    }
