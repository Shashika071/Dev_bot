"""
Application configuration loaded from environment variables.
All secrets stay in .env — never hardcoded.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Deriv API ---
    deriv_app_id: Optional[str] = Field(
        None, description="Deriv API app_id (only needed for old v3 endpoint)"
    )
    deriv_api_token: Optional[str] = Field(
        None, description="Optional API token for authenticated endpoints (not required for public market data)"
    )
    deriv_ws_url: str = Field(
        "wss://api.derivws.com/trading/v1/options/ws/public",
        description="Deriv WebSocket API URL (new public endpoint, no app_id required)",
    )

    # --- Database ---
    database_url: str = Field(
        "postgresql+asyncpg://deriv:change_me_in_production@postgres:5432/deriv_bot"
    )

    # --- Application ---
    app_timezone: str = Field("Asia/Colombo", description="Timezone for daily signal cap reset")
    max_signals_per_day: int = Field(3, ge=0, le=10)
    signal_cooldown_seconds: int = Field(
        540, ge=0, description="Min seconds between signals (from last created_at)"
    )
    contract_duration_seconds: int = Field(540, description="Default contract duration (9 min)")
    max_tick_age_seconds: int = Field(30, ge=1, description="Reject signals if latest tick older than this")
    max_quote_age_seconds: int = Field(90, ge=1, description="Reject signals if quote older than this")
    manual_entry_delay_seconds: int = Field(
        5, ge=0, description="Assumed manual entry delay when labeling outcomes"
    )
    min_label_window_ticks: int = Field(
        5, ge=1, description="Minimum ticks in outcome window for a resolved label"
    )
    lstm_seq_len: int = Field(64, ge=16, description="LSTM price sequence length")
    # Manual "Analyze & Signal": only emit when model confidence clears these bars
    # (even if historical edge evidence is missing). Blind force-create is disabled.
    manual_min_confidence: float = Field(
        0.95, ge=0.5, le=0.99, description="Min calibrated touch probability to emit a manual signal"
    )
    manual_min_margin_over_breakeven: float = Field(
        0.03, ge=0.0, le=0.5, description="Min (cal_prob - quote breakeven) required for manual signal"
    )
    # Live EV / calibration gates (worker auto alerts)
    min_ev_margin: float = Field(
        0.02, ge=0.0, le=0.5, description="Min conservative margin over quote breakeven"
    )
    min_calibration_samples: int = Field(
        50, ge=5, description="Min held-out calibration samples near predicted probability"
    )
    require_touch_confluence: bool = Field(
        True, description="Require direction-matched touch_confluence for alerts"
    )
    confluence_min_score: float = Field(5.0, ge=0.0, description="Touch confluence min score")
    confluence_min_gap: float = Field(1.5, ge=0.0, description="Touch confluence score gap")
    # Training sampling / validation
    train_sampling_interval_seconds: int = Field(
        540, ge=60, description="Entry sample spacing; default = non-overlapping with 540s contracts"
    )
    train_gap_seconds: int = Field(600, ge=0, description="Purge gap between chronological splits")
    train_min_calibration_samples: int = Field(
        50, ge=11, description="Min samples required on calibration split"
    )
    train_edge_min_selected: int = Field(30, ge=5, description="Min selected test signals for edge")
    train_edge_margin: float = Field(
        0.02, ge=0.0, description="Extra margin over mean quote breakeven for edge CI"
    )
    train_max_ticks: int = Field(
        0, ge=0, description="Cap ticks loaded for training (0 = all available)"
    )
    # Live drift / auto-pause
    auto_pause_enabled: bool = Field(True, description="Pause validated alerts on live underperformance")
    auto_pause_min_resolved: int = Field(
        20, ge=5, description="Min resolved validated signals before auto-pause can fire"
    )
    auto_pause_ci_margin: float = Field(
        0.0, ge=-0.1, le=0.1,
        description="Pause when live Wilson CI lower < mean breakeven + this margin",
    )
    # Barrier semantics for relative One-Touch on Deriv synthetic indices:
    # values like 0.09 / 0.2 are relative price-point offsets from spot, not percent.

    # --- Notifications ---
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # --- Security ---
    secret_key: str = Field("dev-only-change-me", description="Secret key for sessions")
    internal_api_secret: str = Field(
        "change_me_internal_secret",
        description="Shared secret for worker → API status/event posts",
    )
    cors_origins: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated allowed CORS origins",
    )

    # --- Data ---
    parquet_data_dir: str = Field("/app/data/parquet", description="Path for Parquet files")
    model_dir: str = Field("/app/data/models", description="Path for saved ML models")

    @property
    def deriv_ws_full_url(self) -> str:
        if self.deriv_app_id and "websockets/v3" in self.deriv_ws_url:
            return f"{self.deriv_ws_url}?app_id={self.deriv_app_id}"
        return self.deriv_ws_url

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "protected_namespaces": ("settings_",),
    }


settings = Settings()
