"""Demo-only Deriv Digit Matches research and execution.

This package is separate from the touch-signal bot. It uses the classic
Deriv WebSocket v3 API with an API token. It does not use PAT or OAuth.
"""

API_FAMILY = "deriv_websocket_v3_api_token"
CONTRACT_TYPE = "DIGITMATCH"
DURATION_TICKS = 5
EXPECTED_LEGACY_SYMBOL = "R_100"
EXPECTED_DISPLAY_NAME = "Volatility 100 Index"
FEATURE_SCHEMA = "dm-features-v1"
TARGET_NOTE = (
    "Historical target is the digit at t+5. This is a research proxy. "
    "A live five-tick Digit Matches contract settles on the broker's entry "
    "and exit ticks, which can differ from this proxy."
)
FRESHNESS_POLICY = (
    "A proposal is stale if its spot epoch is older than the latest tick, "
    "or if its age exceeds the configured maximum. If another tick arrives "
    "after probabilities were computed and before buy, the cycle skips. "
    "It does not submit the old decision."
)
