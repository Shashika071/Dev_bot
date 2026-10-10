"""Server-side demo checks for the classic authorize response."""

from __future__ import annotations


class AccountRejected(Exception):
    pass


class AuthFamilyRejected(Exception):
    pass


def assert_classic_token(token: str | None) -> str:
    if token is None or not str(token).strip():
        raise AuthFamilyRejected("Digit Matches credentials are not configured")
    cleaned = str(token).strip()
    if cleaned.lower().startswith("pat_"):
        raise AuthFamilyRejected(
            "This adapter accepts a classic API token only. PAT and OAuth tokens are refused."
        )
    return cleaned


def verify_demo_authorize(authorize: dict) -> dict:
    """Require Deriv's own is_virtual flag. Missing or inconsistent fields are rejected."""
    if not isinstance(authorize, dict):
        raise AccountRejected("demo status cannot be verified")
    if "is_virtual" not in authorize or "loginid" not in authorize:
        raise AccountRejected("demo status cannot be verified")
    flag = authorize.get("is_virtual")
    try:
        is_virtual = int(flag)
    except (TypeError, ValueError):
        raise AccountRejected("demo status cannot be verified")
    if is_virtual != 1:
        raise AccountRejected("real account rejected")
    loginid = str(authorize.get("loginid") or "").strip()
    if not loginid:
        raise AccountRejected("demo status cannot be verified")
    landing = authorize.get("landing_company_name")
    if landing is not None and str(landing).strip().lower() not in {"virtual", ""}:
        raise AccountRejected("demo status cannot be verified")
    account_list = authorize.get("account_list") or []
    for account in account_list:
        if str(account.get("loginid") or "") != loginid:
            continue
        if "is_virtual" not in account:
            raise AccountRejected("demo status cannot be verified")
        try:
            if int(account.get("is_virtual")) != 1:
                raise AccountRejected("real account rejected")
        except AccountRejected:
            raise
        except (TypeError, ValueError):
            raise AccountRejected("demo status cannot be verified")
    scopes = list(authorize.get("scopes") or [])
    return {
        "loginid": loginid,
        "currency": authorize.get("currency"),
        "is_virtual": 1,
        "scopes": scopes,
        "balance": authorize.get("balance"),
    }


def require_trade_scope(scopes: list) -> None:
    if scopes and "trade" not in scopes:
        raise AccountRejected("token does not include the trade scope")
