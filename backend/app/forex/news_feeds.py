"""Public economic calendar and central-bank headline loaders (no API key).

Calendar: ShowMyTrades public JSON (designed for robot news filters).
Headlines: official central-bank RSS/Atom press feeds.

These feeds are risk/context inputs — not directional trade signals.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")

SHOWMYTRADES_URL = "https://showmytrades.com/api/public/calendar/v1"

# Official press/media RSS endpoints (public). Soft-fail per feed.
CENTRAL_BANK_FEEDS = (
    {"currency": "USD", "name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"currency": "EUR", "name": "European Central Bank", "url": "https://www.ecb.europa.eu/rss/press.html"},
    {"currency": "GBP", "name": "Bank of England", "url": "https://www.bankofengland.co.uk/rss/news"},
    {"currency": "JPY", "name": "Bank of Japan", "url": "https://www.boj.or.jp/en/rss/whatsnew.xml"},
    {"currency": "AUD", "name": "Reserve Bank of Australia", "url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"},
    {"currency": "CAD", "name": "Bank of Canada", "url": "https://www.bankofcanada.ca/feed/"},
    {"currency": "NZD", "name": "Reserve Bank of New Zealand", "url": "https://www.rbnz.govt.nz/feeds/news-and-events"},
    {"currency": "CHF", "name": "Swiss National Bank", "url": "https://www.snb.ch/en/rss/mmr"},
)

_TAG_RE = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text or "")).strip()


def _parse_stamp(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z") or "+" in text[10:] or text.count("-") >= 3:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            stamp = parsedate_to_datetime(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return int(stamp.timestamp())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_rss_items(xml_text: str, *, limit: int = 8) -> list[dict]:
    """Parse RSS 2.0 or Atom entries into {title, link, published, summary}."""
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    nodes = list(root.iter())
    for node in nodes:
        name = _local(node.tag)
        if name not in {"item", "entry"}:
            continue
        title = link = summary = published = None
        for child in list(node):
            child_name = _local(child.tag)
            text = (child.text or "").strip()
            if child_name == "title":
                title = text
            elif child_name == "link":
                link = child.attrib.get("href") or text or link
            elif child_name in {"pubdate", "published", "updated", "date"}:
                published = text or published
            elif child_name in {"description", "summary", "content"}:
                summary = text or summary
        if not title:
            continue
        stamp = _parse_stamp(published)
        items.append({
            "title": _strip_html(title)[:220],
            "link": (link or "")[:400],
            "published": stamp,
            "summary": _strip_html(summary or "")[:400],
        })
        if len(items) >= limit:
            break
    return items


async def fetch_showmytrades_calendar(http: httpx.AsyncClient, *, now: int, duration: int,
                                      currencies: tuple[str, ...] = CURRENCIES) -> dict:
    days = max(2, min(14, int((duration + 2 * 86400) / 86400) + 2))
    response = await http.get(
        SHOWMYTRADES_URL,
        params={
            "currency": ",".join(currencies),
            "impact": "high,medium,holiday",
            "days": str(days),
        },
    )
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("Public calendar response too large")
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("Unexpected public calendar shape")
    as_of = int(payload.get("server_time") or now)
    start = _parse_stamp(payload.get("from")) or (now - 6 * 3600)
    end = _parse_stamp(payload.get("to")) or (now + days * 86400)
    events = []
    for row in payload["events"]:
        if not isinstance(row, dict):
            continue
        currency = str(row.get("currency") or "").upper()
        if currency not in CURRENCIES and currency != "CNY":
            continue
        if currency == "CNY":
            continue
        impact = str(row.get("impact") or "medium").lower()
        if impact not in {"low", "medium", "high", "holiday"}:
            impact = "medium"
        stamp = int(row["ts"]) if row.get("ts") is not None else _parse_stamp(row.get("time"))
        if stamp is None:
            continue
        events.append({
            "currency": currency,
            "title": str(row.get("title") or "Economic event")[:200],
            "at": stamp,
            "impact": impact,
            "forecast": row.get("forecast") or None,
            "previous": row.get("previous") or None,
            "actual": row.get("actual") or None,
            "provider": "ShowMyTrades",
        })
    attribution = payload.get("attribution") if isinstance(payload.get("attribution"), dict) else {}
    return {
        "source": "ShowMyTrades public economic calendar",
        "as_of": as_of,
        "coverage_start": start,
        "coverage_end": end,
        "currencies": sorted({e["currency"] for e in events}),
        "events": events,
        "attribution": {
            "text": attribution.get("text") or "Economic calendar by ShowMyTrades",
            "url": attribution.get("url") or "https://showmytrades.com/tools/economic-calendar",
        },
        "feed_meta": [{
            "id": "showmytrades_calendar",
            "name": "ShowMyTrades calendar",
            "kind": "economic_releases",
            "url": SHOWMYTRADES_URL,
            "as_of": as_of,
            "ok": True,
            "items": len(events),
        }],
    }


async def fetch_central_bank_headlines(http: httpx.AsyncClient, *, now: int) -> tuple[list[dict], list[dict]]:
    """Return (fundamental rows for speeches, feed_meta)."""
    rows: list[dict] = []
    meta: list[dict] = []
    for feed in CENTRAL_BANK_FEEDS:
        entry = {
            "id": f"cb_{feed['currency'].lower()}",
            "name": feed["name"],
            "kind": "central_bank_headlines",
            "currency": feed["currency"],
            "url": feed["url"],
            "ok": False,
            "as_of": None,
            "items": 0,
            "error": None,
        }
        try:
            response = await http.get(feed["url"])
            response.raise_for_status()
            if len(response.content) > 1_500_000:
                raise ValueError("Feed too large")
            items = parse_rss_items(response.text, limit=5)
            newest = max((item["published"] for item in items if item.get("published")), default=None)
            entry.update(ok=True, as_of=newest or now, items=len(items))
            for item in items:
                published = item.get("published") or now
                if not 0 <= now - int(published) <= 7 * 86400:
                    # Keep very fresh window for factor scoring (24h) separately;
                    # still surface weekly headlines in the UI via feed_meta items.
                    pass
                rows.append({
                    "currency": feed["currency"],
                    "as_of": published,
                    "source": feed["name"],
                    "summary": item["title"] + (f" — {item['summary']}" if item.get("summary") else ""),
                    "link": item.get("link"),
                    "risk": "medium",
                    "kind": "headline",
                })
        except Exception as exc:
            entry["error"] = type(exc).__name__
        meta.append(entry)
    return rows, meta


async def load_public_context(now: int, duration: int) -> dict:
    """Compose calendar + CB headlines for the no-subscription path."""
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "deriv-touch-bot-forex/1.0 (+local research bot)"},
    ) as http:
        calendar = await fetch_showmytrades_calendar(http, now=now, duration=duration)
        headlines, cb_meta = await fetch_central_bank_headlines(http, now=now)
    feed_meta = list(calendar.get("feed_meta") or []) + cb_meta
    # Speeches factor uses ≤24h rows; UI can still show weekly via headlines list.
    fresh_speeches = [row for row in headlines if 0 <= now - int(row["as_of"]) <= 86400]
    return {
        **calendar,
        "source": "Public feeds: ShowMyTrades calendar + central-bank RSS",
        "fundamentals": {"central_bank_speeches": fresh_speeches},
        "headlines": headlines[:40],
        "feed_meta": feed_meta,
        "public": True,
    }
