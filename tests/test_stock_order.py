"""股票下單模組測試（純單元；mock 掉 SDK）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from stock_order_api.fubon.errors import FubonError
from stock_order_api.fubon.stock_order import (
    OrderRequest,
    StockOrderService,
    _build_order,
    _map_order_record,
)


class _FakeStock:
    def __init__(self) -> None:
        self.placed: list[Any] = []
        self.canceled: list[Any] = []
        self.mod_price: list[Any] = []
        self.mod_qty: list[Any] = []
        self.orders: list[Any] = []

    def place_order(self, acc: Any, order: Any) -> Any:
        self.placed.append((acc, order))
        return SimpleNamespace(
            is_success=True,
            message="ok",
            data=SimpleNamespace(order_no="A0001", seq_no="S1", status="NEW"),
        )

    def cancel_order(self, acc: Any, rec: Any) -> Any:
        self.canceled.append((acc, rec))
        return SimpleNamespace(is_success=True, message="ok")

    def make_modify_price_obj(self, rec: Any, price: str) -> Any:
        return SimpleNamespace(order=rec, price=price)

    def modify_price(self, acc: Any, mod: Any) -> Any:
        self.mod_price.append((acc, mod))
        return SimpleNamespace(is_success=True, message="ok")

    def make_modify_quantity_obj(self, rec: Any, qty: int) -> Any:
        return SimpleNamespace(order=rec, qty=qty)

    def modify_quantity(self, acc: Any, mod: Any) -> Any:
        self.mod_qty.append((acc, mod))
        return SimpleNamespace(is_success=True, message="ok")

    def get_order_results(self, acc: Any) -> Any:
        return SimpleNamespace(is_success=True, data=self.orders)


class _FakeClient:
    def __init__(self, dry_run: bool = False) -> None:
        self.settings = SimpleNamespace(dry_run=dry_run)
        self.sdk = SimpleNamespace(stock=_FakeStock())
        self.account = SimpleNamespace(raw=SimpleNamespace(account="1234"))


def test_build_order_limit_ok() -> None:
    req = OrderRequest(symbol="2330", side="Buy", quantity=1000, price="600")
    o = _build_order(req)
    text = repr(o)
    assert '2330' in text
    assert '1000' in text
    assert '600' in text


def test_build_order_empty_symbol_raises() -> None:
    with pytest.raises(ValueError):
        _build_order(OrderRequest(symbol="", side="Buy", quantity=1, price="1"))


def test_build_order_zero_qty_raises() -> None:
    with pytest.raises(ValueError):
        _build_order(OrderRequest(symbol="2330", side="Buy", quantity=0, price="1"))


def test_build_order_limit_without_price_raises() -> None:
    with pytest.raises(ValueError):
        _build_order(OrderRequest(symbol="2330", side="Buy", quantity=100, price=None))


def test_build_order_market_allows_no_price() -> None:
    req = OrderRequest(
        symbol="2330", side="Buy", quantity=100, price=None, price_type="Market",
        time_in_force="IOC",
    )
    o = _build_order(req)
    assert 'Market' in repr(o)


def test_place_success() -> None:
    c = _FakeClient()
    svc = StockOrderService(c)
    result = svc.place(OrderRequest(symbol="2330", side="Buy", quantity=1000, price="600"))
    assert result.is_success is True
    assert result.order_no == "A0001"
    assert len(c.sdk.stock.placed) == 1


def test_place_dry_run_skips_sdk() -> None:
    c = _FakeClient(dry_run=True)
    svc = StockOrderService(c)
    result = svc.place(OrderRequest(symbol="2330", side="Buy", quantity=1000, price="600"))
    assert result.is_success is True
    assert result.status == "DRY_RUN"
    assert len(c.sdk.stock.placed) == 0


def test_cancel_and_modify() -> None:
    c = _FakeClient()
    svc = StockOrderService(c)
    rec = SimpleNamespace(order_no="A1")
    assert svc.cancel(rec).is_success
    assert svc.modify_price(rec, "601").is_success
    assert svc.modify_quantity(rec, 500).is_success
    assert c.sdk.stock.canceled and c.sdk.stock.mod_price and c.sdk.stock.mod_qty


def test_list_orders_mapping() -> None:
    c = _FakeClient()
    c.sdk.stock.orders = [
        SimpleNamespace(
            order_no="A1",
            seq_no="S1",
            stock_no="2330",
            buy_sell="Buy",
            price="600",
            quantity=1000,
            filled_qty=0,
            status="NEW",
        )
    ]
    svc = StockOrderService(c)
    rows = svc.list_orders()
    assert len(rows) == 1
    rec, raw = rows[0]
    assert rec.order_no == "A1"
    assert rec.symbol == "2330"
    assert raw is c.sdk.stock.orders[0]


def test_no_account_raises() -> None:
    c = _FakeClient()
    c.account = None
    svc = StockOrderService(c)
    with pytest.raises(FubonError):
        svc.place(OrderRequest(symbol="2330", side="Buy", quantity=1, price="1"))


def test_map_order_record_handles_missing_fields() -> None:
    rec = _map_order_record(SimpleNamespace())
    assert rec.order_no == ""
    assert rec.quantity == 0
