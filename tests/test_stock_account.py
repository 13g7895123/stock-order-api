"""測試 StockAccount 在 fake SDK 下的行為。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_order_api.audit.store import AuditStore
from stock_order_api.fubon.client import AccountRef, FubonClient
from stock_order_api.fubon.errors import FubonAccountError
from stock_order_api.fubon.stock_account import StockAccount


class _FakeStock:
    def __init__(self) -> None:
        self.inv_calls = 0

    def inventories(self, acc: object) -> object:
        self.inv_calls += 1
        data = [
            {"stock_no": "2881", "total_qty": "1000", "today_qty": "1000", "avg_price": "70"},
        ]
        return SimpleNamespace(is_success=True, message="", data=data)

    def unrealized_gains_and_loses(self, acc: object) -> object:
        return SimpleNamespace(is_success=True, data=[])

    def buying_power(self, acc: object) -> object:
        return SimpleNamespace(is_success=True, data={"cash": "1000", "buying_power": "2500"})

    def realized_gains_and_loses(self, acc: object, start: str, end: str) -> object:
        return SimpleNamespace(is_success=True, data=[])

    def settlements(self, acc: object) -> object:
        return SimpleNamespace(is_success=True, data=[{"t_date": "2026-04-22", "amount": "500"}])

    def maintenance(self, acc: object) -> object:
        return SimpleNamespace(is_success=False, message="無信用戶")


class _FakeSDK:
    def __init__(self) -> None:
        self.stock = _FakeStock()


def _make_svc(tmp_path: Path) -> tuple[StockAccount, _FakeSDK]:
    FubonClient.reset()
    client = FubonClient.__new__(FubonClient)  # bypass __init__
    client.settings = SimpleNamespace()  # type: ignore[assignment]
    client._sdk = _FakeSDK()
    client._accounts = [AccountRef(raw=object(), account="1234567", branch_no="6460", account_type="S", account_name="")]
    client._current = client._accounts[0]
    client._logged_in = True
    client._cert_info = None
    audit = AuditStore(tmp_path / "a.sqlite3")
    svc = StockAccount(client=client, audit=audit)  # type: ignore[arg-type]
    return svc, client._sdk


def test_inventories_cache_hit(tmp_path: Path) -> None:
    svc, sdk = _make_svc(tmp_path)
    r1 = svc.inventories()
    _ = svc.inventories()
    assert len(r1) == 1
    assert r1[0].symbol == "2881"
    assert sdk.stock.inv_calls == 1  # 第二次命中 cache


def test_inventories_force_refresh(tmp_path: Path) -> None:
    svc, sdk = _make_svc(tmp_path)
    svc.inventories()
    svc.inventories(force=True)
    assert sdk.stock.inv_calls == 2


def test_maintenance_returns_none_when_no_credit(tmp_path: Path) -> None:
    svc, _sdk = _make_svc(tmp_path)
    assert svc.maintenance() is None


def test_error_propagates(tmp_path: Path) -> None:
    svc, sdk = _make_svc(tmp_path)

    def boom(*a: object, **k: object) -> object:
        return SimpleNamespace(is_success=False, message="API error")

    sdk.stock.unrealized_gains_and_loses = boom  # type: ignore[assignment]
    with pytest.raises(FubonAccountError):
        svc.unrealized()


def test_settlements_ok(tmp_path: Path) -> None:
    svc, _sdk = _make_svc(tmp_path)
    r = svc.settlements()
    assert len(r) == 1
    assert str(r[0].amount) == "500"


def test_audit_events_written(tmp_path: Path) -> None:
    svc, _sdk = _make_svc(tmp_path)
    svc.inventories()
    with svc.audit._conn as conn:  # type: ignore[union-attr]
        rows = conn.execute("SELECT event, ok FROM audit_events").fetchall()
    events = [(r[0], r[1]) for r in rows]
    assert ("QUERY_INVENTORY", 1) in events
