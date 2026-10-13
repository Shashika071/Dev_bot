"""Versioned market and analysis response models."""

from typing import Any

from pydantic import BaseModel, Field


class MarketSnapshotResponse(BaseModel):
    schema_version: int = 1
    symbol: str
    last_tick: dict | None = None
    tick_age_seconds: float | None = None
    intervals: dict[str, Any] = Field(default_factory=dict)
    stale: bool = True


class AnalysisSnapshotResponse(BaseModel):
    schema_version: int = 1
    symbol: str
    interval: str
    as_of_epoch: int | None = None
    fresh: bool
    stale_reason: str | None = None
    regime: str
    structure: str
    volume_note: str
    timeframes: dict[str, Any]
    indicators: dict[str, Any]
    series: dict[str, Any]
    events: list[dict[str, Any]]
    strategies: list[dict[str, Any]]
    confluence: dict[str, Any]
    signal: dict[str, Any] | None = None
    buy_signals: list[dict[str, Any]] = Field(default_factory=list)
    sell_signals: list[dict[str, Any]] = Field(default_factory=list)
    candles: list[dict[str, Any]]
    ml: dict[str, Any]
    trend: dict[str, Any] = Field(default_factory=dict)
    volatility: dict[str, Any] = Field(default_factory=dict)
    strength: dict[str, Any] = Field(default_factory=dict)
    noise_filter: dict[str, Any] = Field(default_factory=dict)
    checklist: list[dict[str, Any]] = Field(default_factory=list)


class AnnotationBody(BaseModel):
    symbol: str = "R_100"
    interval: str = "1m"
    kind: str = Field(pattern="^(hline|trendline|ray|rectangle|label)$")
    geometry: dict[str, Any]
    style: dict[str, Any] | None = None
    label: str | None = None


class AnnotationResponse(BaseModel):
    id: str
    symbol: str
    interval: str
    kind: str
    geometry: dict[str, Any]
    style: dict[str, Any] | None = None
    label: str | None = None
