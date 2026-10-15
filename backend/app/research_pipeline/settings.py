"""Experiment settings.

Defaults are experimental. They are not confirmed trading settings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

_DEFAULTS_PATH = Path(__file__).with_name("experimental_defaults.json")

TARGETS = ("direction", "upper_touch", "lower_touch", "all")
TIE_POLICIES = ("exclude", "up", "down")


@dataclass
class ExperimentConfig:
    confirmed_trading_settings: bool = False
    symbol: str = "R_100"
    symbol_note: str = ""
    target: str = "all"
    horizon_seconds: int = 540
    barrier_distance: float = 0.3
    barrier_unit: str = "relative_price_points"
    entry_delay_seconds: int = 5
    tie_policy: str = "exclude"
    sample_every_seconds: int = 540
    max_gap_seconds: int = 5
    warmup_seconds: int = 1500
    test_fraction: float = 0.2
    seed: int = 42
    min_labeled_rows: int = 80
    hypothetical_r: float | None = None
    enable_lstm: bool = False
    fast: bool = False
    tree_iterations: int = 300
    early_stopping_rounds: int = 30
    lstm_hidden: int = 16
    lstm_seq_len: int = 32
    lstm_epochs: int = 20
    lstm_patience: int = 4
    n_bootstrap: int = 400

    def resolved_targets(self) -> list[str]:
        if self.target == "all":
            return ["direction", "upper_touch", "lower_touch"]
        return [self.target]

    @property
    def uses_touch_barrier(self) -> bool:
        return any(t in ("upper_touch", "lower_touch") for t in self.resolved_targets())

    def banner(self) -> str:
        if self.confirmed_trading_settings:
            return "Settings were marked confirmed in the config file."
        return (
            "EXPERIMENTAL DEFAULTS. These are not confirmed trading settings. "
            "Instrument, target, horizon, barrier distance, and contract quotes "
            "still need confirmation before any result is treated as a final test "
            "of a real contract."
        )

    def to_public_dict(self) -> dict:
        data = asdict(self)
        data["defaults_are_confirmed_trading_settings"] = bool(self.confirmed_trading_settings)
        return data


def load_config(path: str | Path | None = None, **overrides) -> ExperimentConfig:
    payload = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    if path:
        payload.update(json.loads(Path(path).read_text(encoding="utf-8")))
    for key, value in overrides.items():
        if value is not None:
            payload[key] = value
    known = {item.name for item in fields(ExperimentConfig)}
    kwargs = {key: payload[key] for key in payload if key in known}
    cfg = ExperimentConfig(**kwargs)
    _validate(cfg)
    if cfg.fast:
        cfg.tree_iterations = min(cfg.tree_iterations, 40)
        cfg.early_stopping_rounds = min(cfg.early_stopping_rounds, 10)
        cfg.lstm_hidden = min(cfg.lstm_hidden, 8)
        cfg.lstm_seq_len = min(cfg.lstm_seq_len, 16)
        cfg.lstm_epochs = min(cfg.lstm_epochs, 6)
        cfg.lstm_patience = min(cfg.lstm_patience, 2)
        cfg.n_bootstrap = min(cfg.n_bootstrap, 200)
        cfg.min_labeled_rows = min(cfg.min_labeled_rows, 60)
    return cfg


def _validate(cfg: ExperimentConfig) -> None:
    if cfg.target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    if cfg.tie_policy not in TIE_POLICIES:
        raise ValueError(f"tie_policy must be one of {TIE_POLICIES}")
    if cfg.horizon_seconds <= cfg.entry_delay_seconds:
        raise ValueError("horizon_seconds must be greater than entry_delay_seconds")
    if cfg.sample_every_seconds < 1:
        raise ValueError("sample_every_seconds must be >= 1")
    if not 0.05 <= float(cfg.test_fraction) <= 0.5:
        raise ValueError("test_fraction must be between 0.05 and 0.5")
    if cfg.uses_touch_barrier and float(cfg.barrier_distance) <= 0:
        raise ValueError("barrier_distance must be a positive price-point distance for touch targets")
    if cfg.barrier_unit != "relative_price_points":
        raise ValueError("only relative_price_points barriers are implemented")
    if cfg.hypothetical_r is not None and float(cfg.hypothetical_r) <= 0:
        raise ValueError("hypothetical_r must be a positive net-profit multiple")
