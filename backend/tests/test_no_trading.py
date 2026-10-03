import pytest
import os
import ast


def test_no_trading_endpoints_in_ws_client():
    """
    Critical safety check: Ensure the Deriv WS Client explicitly 
    forbids trading messages (buy/sell).
    """
    client_path = os.path.join(os.path.dirname(__file__), "..", "app", "deriv", "client.py")
    
    with open(client_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert "FORBIDDEN_MESSAGES = {\"buy\", \"sell\", \"sell_expired\", \"buy_contract_for_multiple_accounts\"}" in content, "Forbidden messages set is missing or modified"
    assert "msg_type = next((k for k in msg if k in FORBIDDEN_MESSAGES), None)" in content, "Forbidden message check is missing"
    assert "raise RuntimeError(" in content and "BLOCKED: Attempted to send forbidden trading message" in content, "Exception for forbidden message is missing"


def test_no_trading_methods_exist():
    """
    Critical safety check: Scan the client for any methods 
    that might be wrappers for buy or sell.
    """
    client_path = os.path.join(os.path.dirname(__file__), "..", "app", "deriv", "client.py")
    
    with open(client_path, "r", encoding="utf-8") as f:
        source = f.read()
        
    tree = ast.parse(source)
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Ensure no method is named 'buy' or 'sell'
            assert "buy" not in node.name.lower(), f"Method '{node.name}' contains 'buy'"
            assert "sell" not in node.name.lower(), f"Method '{node.name}' contains 'sell'"


def test_daily_cap_limits_signals():
    """
    Ensure the daily cap manager enforces limits properly.
    """
    from app.signal_engine.daily_cap import DailyCapManager
    import asyncio
    
    class MockSession:
        async def execute(self, query):
            class MockResult:
                def scalar(self): return 3 # 3 signals today
                def scalar_one_or_none(self): return None
            return MockResult()
            
    manager = DailyCapManager(max_per_day=3)
    
    async def run_test():
        session = MockSession()
        can_issue, reason = await manager.can_issue_signal(session, "R_100")
        assert can_issue is False
        assert "Daily cap reached" in reason

    asyncio.run(run_test())
