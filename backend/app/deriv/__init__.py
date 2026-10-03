"""Deriv API constants and message schemas."""

# --- Deriv API v3 WebSocket ---
# Documentation: https://developers.deriv.com/
# Base URL: wss://ws.derivws.com/websockets/v3?app_id={APP_ID}
# Auth method: API token via "authorize" message (only needed for account endpoints)
# Permissions used: read (public data), NO trade scope

API_VERSION = "v3"
WS_BASE_URL = "wss://ws.derivws.com/websockets/v3"

# Volatility 100 symbols
SYMBOL_R_100 = "R_100"          # Volatility 100 Index
SYMBOL_R_100_1S = "1HZ100V"     # Volatility 100 (1s) Index

# Contract types
CONTRACT_ONETOUCH = "ONETOUCH"
CONTRACT_NOTOUCH = "NOTOUCH"

# Duration units
DURATION_SECONDS = "s"
DURATION_MINUTES = "m"
DURATION_HOURS = "h"
DURATION_DAYS = "d"

# Heartbeat interval
HEARTBEAT_INTERVAL = 30  # seconds
