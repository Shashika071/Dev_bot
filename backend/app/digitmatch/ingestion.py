"""Normalize broker ticks. Historical payout quotes are never invented."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.digitmatch.digits import DigitError, decimal_places, extract_last_digit


@dataclass
class Observation:
    symbol: str
    broker_epoch: int
    received_at: datetime
    quote_wire: str
    quote_text: str
    precision: int | None
    last_digit: str | None
    digit_value: int | None
    source: str
    ingestion_id: str
    broker_tick_id: str | None
    pip_size_raw: str | None
    flags: tuple[str, ...]


def _wire(quote) -> str:
    if isinstance(quote, str):
        return quote.strip()
    return str(quote)


def normalize_observation(
    *,
    symbol: str,
    quote,
    epoch: int,
    pip_size,
    source: str,
    ingestion_id: str,
    broker_tick_id: str | None,
    received_at: datetime | None = None,
) -> Observation:
    received = received_at or datetime.now(timezone.utc)
    wire = _wire(quote)
    flags: list[str] = []
    precision = None
    quote_text = wire
    last_digit = None
    digit_value = None
    if pip_size is None:
        flags.append("precision_unknown")
    else:
        try:
            precision = decimal_places(pip_size)
            extracted = extract_last_digit(wire if isinstance(quote, str) else quote, precision, wire=wire)
            quote_text = extracted.quote_text
            last_digit = extracted.digit_char
            digit_value = extracted.digit_value
            flags.extend(extracted.flags)
        except (DigitError, ValueError):
            flags.append("digit_unreadable")
    return Observation(
        symbol=symbol,
        broker_epoch=int(epoch),
        received_at=received,
        quote_wire=wire,
        quote_text=quote_text,
        precision=precision,
        last_digit=last_digit,
        digit_value=digit_value,
        source=source,
        ingestion_id=ingestion_id,
        broker_tick_id=broker_tick_id,
        pip_size_raw=None if pip_size is None else str(pip_size),
        flags=tuple(flags),
    )
