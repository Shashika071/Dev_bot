import pytest

from app.forex.context import evaluate_context, load_context

NOW = 1792400400


def feed(**updates):
    return {"source": "Test calendar", "as_of": NOW, "coverage_start": NOW - 3600,
            "coverage_end": NOW + 86400, "currencies": ["EUR", "USD"], "events": [], **updates}


def test_fresh_complete_empty_calendar_is_valid_but_not_fabricated_fundamentals():
    result = evaluate_context(feed(), "frxEURUSD", NOW, 14400)
    assert result["ready"]
    assert next(f for f in result["factors"] if f["id"] == 22)["status"] == "unavailable"


@pytest.mark.parametrize("updates", [{"as_of": NOW - 901}, {"as_of": NOW + 1},
    {"coverage_end": NOW + 10000}, {"coverage_start": NOW}, {"currencies": ["USD"]},
    {"events": None}, {"events": [{"currency": "USD", "title": "NFP", "at": "bad", "impact": "high"}]}])
def test_bad_or_incomplete_calendar_blocks(updates):
    assert not evaluate_context(feed(**updates), "frxEURUSD", NOW, 14400)["ready"]


@pytest.mark.parametrize("event_time", [NOW - 3500, NOW + 100, NOW + 14399, NOW + 15000])
def test_high_impact_news_across_entire_holding_window(event_time):
    data = feed(events=[{"currency": "USD", "title": "CPI", "at": event_time, "impact": "high"}])
    result = evaluate_context(data, "frxEURUSD", NOW, 14400)
    assert not result["ready"] and "CPI" in result["reason"]


def test_news_unrelated_currency_does_not_block():
    data = feed(events=[{"currency": "JPY", "title": "Rate decision", "at": NOW + 100, "impact": "high"}])
    assert evaluate_context(data, "frxEURUSD", NOW, 14400)["ready"]


def test_sourced_high_risk_fundamental_blocks_but_old_data_not_used():
    row = {"currency": "ALL", "as_of": NOW, "summary": "Elevated geopolitical uncertainty", "source": "Source", "risk": "high"}
    data = feed(fundamentals={"geopolitics": [row]})
    assert not evaluate_context(data, "frxEURUSD", NOW, 14400)["ready"]
    data["fundamentals"]["geopolitics"][0]["as_of"] = NOW - 90000
    result = evaluate_context(data, "frxEURUSD", NOW, 14400)
    assert next(f for f in result["factors"] if f["id"] == 27)["status"] == "unavailable"


@pytest.mark.asyncio
async def test_missing_provider_fails_closed_without_network():
    from types import SimpleNamespace
    settings = SimpleNamespace(forex_context_url=None, forex_context_file=None, trading_economics_api_key=None)
    assert "error" in await load_context(settings, NOW, 14400)


@pytest.mark.asyncio
async def test_local_feed_loading(tmp_path):
    import json
    from types import SimpleNamespace
    path = tmp_path / "context.json"
    path.write_text(json.dumps(feed()))
    result = await load_context(SimpleNamespace(
        forex_context_file=str(path), forex_context_url=None, trading_economics_api_key=None), NOW, 14400)
    assert evaluate_context(result, "frxEURUSD", NOW, 14400)["ready"]


@pytest.mark.asyncio
async def test_server_clock_sample_stays_fresh_without_inventing_events(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    sample = Path(__file__).resolve().parents[2] / "data" / "forex" / "context.sample.json"
    payload = json.loads(sample.read_text(encoding="utf-8"))
    assert payload.get("clock") == "server"
    path = tmp_path / "context.sample.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = await load_context(SimpleNamespace(
        forex_context_file=str(path), forex_context_url=None, trading_economics_api_key=None), NOW, 14400)
    assert loaded["as_of"] == NOW
    assert loaded["coverage_start"] <= NOW - 3600
    assert loaded["coverage_end"] >= NOW + 14400 + 1800
    assert loaded["events"] == []
    result = evaluate_context(loaded, "frxEURUSD", NOW, 14400)
    assert result["ready"]
    assert next(f for f in result["factors"] if f["id"] == 26)["status"] == "clear"

