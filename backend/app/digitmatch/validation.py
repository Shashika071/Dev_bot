"""Tick-stream quality checks. Invalid ticks stay stored and are excluded from windows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityResult:
    flags: tuple[str, ...]
    usable: bool


def assess_tick(
    *,
    epoch: int,
    previous_epoch: int | None,
    precision: int | None,
    previous_precision: int | None,
    expected_tick_seconds: float,
    quote_text: str | None = None,
    previous_quote_text: str | None = None,
    same_epoch_conflict: bool = False,
    replay: bool = False,
) -> QualityResult:
    flags: list[str] = []
    if previous_epoch is not None:
        if epoch < previous_epoch:
            flags.append("out_of_order")
        elif epoch - previous_epoch > expected_tick_seconds * 3:
            flags.append("gap")
    if previous_precision is not None and precision is not None and precision != previous_precision:
        flags.append("precision_change")
    if same_epoch_conflict or (
        previous_epoch is not None
        and epoch == previous_epoch
        and previous_quote_text is not None
        and quote_text is not None
        and quote_text != previous_quote_text
    ):
        flags.append("conflict")
    if replay:
        flags.append("replay")
    if precision is None:
        flags.append("precision_unknown")
    blocking = {"out_of_order", "gap", "precision_change", "conflict", "replay", "precision_unknown"}
    usable = not any(flag in blocking for flag in flags)
    return QualityResult(tuple(flags), usable)


def window_is_clean(usable: list[bool] | tuple[bool, ...], start: int, end_inclusive: int) -> bool:
    if start < 0:
        return False
    return all(usable[start : end_inclusive + 1])
