"""Start the trade session only when the server execution gate is open."""

from __future__ import annotations

from app.execution.gates import server_execution_allowed


async def start_if_allowed() -> bool:
    if not server_execution_allowed():
        return False
    from app.deriv.trade_session import trade_account_session
    from app.trade_prefs import token_status

    if not token_status().get("token_configured"):
        return False
    await trade_account_session.start()
    return True


async def restart_if_allowed() -> bool:
    if not server_execution_allowed():
        return False
    from app.deriv.trade_session import trade_account_session

    await trade_account_session.restart()
    return True


async def stop_session() -> None:
    from app.deriv.trade_session import trade_account_session

    await trade_account_session.stop()
