"""Analysis routes must not import order placement."""

import ast
import os

from app.execution.gates import permission_status


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))


def _source(relative: str) -> str:
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def test_analysis_modules_do_not_import_order_code():
    protected = [
        os.path.join("api", "signals.py"),
        os.path.join("api", "market.py"),
        os.path.join("api", "analysis.py"),
        os.path.join("signal_engine", "analyze_watch.py"),
    ]
    for folder in ("analysis", "market_data"):
        base = os.path.join(ROOT, folder)
        for dirpath, _, files in os.walk(base):
            for name in files:
                if name.endswith(".py"):
                    protected.append(os.path.relpath(os.path.join(dirpath, name), ROOT))

    banned = {"app.deriv.auto_trade", "app.deriv.trade_client", "app.deriv.trade_session"}
    for relative in protected:
        tree = ast.parse(_source(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in banned:
                raise AssertionError(f"{relative} imports {node.module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "place_one_touch", relative


def test_execution_requires_server_gate_and_user_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.model_dir", str(tmp_path))
    monkeypatch.setattr("app.execution.risk.settings.model_dir", str(tmp_path))
    blocked = permission_status({
        "server_execution_allowed": False,
        "auto_trade_enabled": True,
        "execution_confirmed": True,
        "daily_loss_limit": 25,
        "trade_stake": 1,
        "trade_stake_use_full_balance": False,
    })
    assert blocked["allowed"] is False
    assert blocked["reason"] == "server_execution_disabled"

    monkeypatch.setattr("app.execution.gates.get_trade_token", lambda: "token-value")
    missing_confirm = permission_status({
        "server_execution_allowed": True,
        "auto_trade_enabled": True,
        "execution_confirmed": False,
        "daily_loss_limit": 25,
        "trade_stake": 1,
        "trade_stake_use_full_balance": False,
    })
    assert missing_confirm["reason"] == "execution_not_confirmed"

    missing_limit = permission_status({
        "server_execution_allowed": True,
        "auto_trade_enabled": True,
        "execution_confirmed": True,
        "daily_loss_limit": 0,
        "trade_stake": 1,
        "trade_stake_use_full_balance": False,
    })
    assert missing_limit["reason"] == "daily_loss_limit_required"

    allowed = permission_status({
        "server_execution_allowed": True,
        "auto_trade_enabled": True,
        "execution_confirmed": True,
        "daily_loss_limit": 25,
        "trade_stake": 1,
        "trade_stake_use_full_balance": False,
    })
    assert allowed["allowed"] is True
    assert allowed["reason"] == "allowed"
