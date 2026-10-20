"""Public news feed parsers (no network)."""
from datetime import datetime, timezone

from app.forex.news_feeds import parse_rss_items


def test_parse_rss20_items():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Rate decision</title>
        <link>https://example.com/1</link>
        <pubDate>Mon, 20 Oct 2025 12:00:00 GMT</pubDate>
        <description>Policy statement</description>
      </item>
      <item><title>Minutes</title><pubDate>Tue, 21 Oct 2025 12:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items = parse_rss_items(xml, limit=5)
    assert len(items) == 2
    assert items[0]["title"] == "Rate decision"
    assert items[0]["published"] is not None
    assert "example.com" in items[0]["link"]


def test_parse_atom_items():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Press conference</title>
        <link href="https://example.com/atom"/>
        <updated>2025-10-20T10:00:00Z</updated>
        <summary>Remarks</summary>
      </entry>
    </feed>"""
    items = parse_rss_items(xml)
    assert len(items) == 1
    assert items[0]["title"] == "Press conference"
    assert items[0]["published"] == int(datetime(2025, 10, 20, 10, 0, tzinfo=timezone.utc).timestamp())
