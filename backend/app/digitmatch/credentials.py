"""Use the touch bot's saved Deriv login unless Digit Matches has its own."""

from __future__ import annotations

from app.config import settings
from app.trade_prefs import get_trade_token, resolve_deriv_app_id


def resolve_digitmatch_credentials() -> dict:
    """Return the token and app id without logging either value.

    DM_DERIV_API_TOKEN / DM_DERIV_APP_ID win when they are set.
    Otherwise the Configuration screen token and app id are reused.
    """
    explicit_token = str(settings.dm_deriv_api_token or "").strip()
    explicit_app = str(settings.dm_deriv_app_id or "").strip()
    token = explicit_token or str(get_trade_token() or "").strip()
    app_id = explicit_app or resolve_deriv_app_id()
    if explicit_token:
        source = "digitmatch_env"
    elif token:
        source = "touch_bot"
    else:
        source = "missing"
    kind = "missing"
    if token.lower().startswith("pat_"):
        kind = "pat"
    elif token:
        kind = "classic"
    return {
        "token": token,
        "app_id": app_id,
        "source": source,
        "kind": kind,
        "configured": bool(token),
    }
