"""Last-digit extraction that keeps trailing zeroes via broker precision."""

from __future__ import annotations

import math
from dataclasses import dataclass


class DigitError(ValueError):
    pass


@dataclass(frozen=True)
class DigitExtract:
    quote_text: str
    digit_char: str
    digit_value: int
    precision: int
    flags: tuple[str, ...]


def decimal_places(pip_size: float | int | str) -> int:
    """Deriv sends pip_size as decimal places (2) or as a price increment (0.01)."""
    value = float(pip_size)
    if not math.isfinite(value) or value <= 0:
        raise DigitError("precision must be a positive finite number")
    if value >= 1:
        places = int(value)
        if abs(value - places) > 1e-9 or places > 12:
            raise DigitError("precision is not a whole number of decimal places")
        return places
    places = int(round(-math.log10(value)))
    if places < 0 or places > 12:
        raise DigitError("precision is out of range")
    return places


def format_quote(raw: str | float | int, precision: int, *, wire: str | None = None) -> tuple[str, tuple[str, ...]]:
    flags: list[str] = []
    if wire is None:
        wire = raw if isinstance(raw, str) else repr(raw)
    text = str(raw).strip()
    if isinstance(raw, str):
        text = raw.strip()
    if "e" in text.lower():
        flags.append("scientific_notation")
        text = f"{float(text):.{precision}f}"
    if text.startswith("+"):
        text = text[1:]
    if "." in text:
        whole, frac = text.split(".", 1)
        if not whole or whole in {"-", "+"}:
            whole = "0" if whole != "-" else "-0"
        digits_only = frac
        if not digits_only.isdigit() or not whole.lstrip("-").isdigit():
            raise DigitError(f"quote is not a decimal: {text}")
        if len(digits_only) < precision:
            flags.append("precision_padded")
            digits_only = digits_only.ljust(precision, "0")
        elif len(digits_only) > precision:
            flags.append("extra_fraction_digits")
            digits_only = digits_only[:precision]
        formatted = f"{whole}.{digits_only}" if precision else whole
    else:
        if not text.lstrip("-").isdigit():
            raise DigitError(f"quote is not numeric: {text}")
        flags.append("no_decimal_in_source")
        formatted = f"{text}.{('0' * precision)}" if precision else text
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        shortest = format(raw, "f").rstrip("0").rstrip(".") if isinstance(raw, float) else str(raw)
        if formatted != shortest and formatted.endswith("0"):
            flags.append("trailing_zero_from_precision")
    return formatted, tuple(flags)


def extract_last_digit(raw: str | float | int, precision: int, *, wire: str | None = None) -> DigitExtract:
    if precision < 0:
        raise DigitError("precision cannot be negative")
    formatted, flags = format_quote(raw, precision, wire=wire)
    if precision == 0:
        digit_char = formatted.lstrip("-")[-1]
    else:
        frac = formatted.split(".", 1)[1]
        if len(frac) != precision:
            raise DigitError("formatted quote does not match precision")
        digit_char = frac[-1]
    if not digit_char.isdigit():
        raise DigitError("last digit is not numeric")
    return DigitExtract(formatted, digit_char, int(digit_char), precision, flags)
