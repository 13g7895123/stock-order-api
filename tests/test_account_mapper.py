"""測試 mapper 純函式：不依賴 SDK。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from stock_order_api.fubon.client import AccountRef
from stock_order_api.fubon.stock_account import (
    map_buying_power,
    map_inventory,
    map_maintenance,
    map_realized,
    map_settlement,
    map_unrealized,
)


def _acc() -> AccountRef:
    return AccountRef(raw=None, account="1234567", branch_no="6460", account_type="S", account_name="")


def test_map_inventory_dict() -> None:
    raw = {
        "stock_no": "2881",
        "stock_name": "富邦金",
        "order_type": "Stock",
        "today_qty": "2000",
        "total_qty": "3000",
        "avg_price": "70.5",
    }
    item = map_inventory(raw, _acc())
    assert item.symbol == "2881"
    assert item.total_qty == 3000
    assert item.avg_price == Decimal("70.5")
    assert item.account == "6460-1234567"


def test_map_inventory_object() -> None:
    raw = SimpleNamespace(
        symbol="2330", name="台積電", order_type="Stock",
        today_qty=1000, total_qty=1000, avg_price="850",
    )
    item = map_inventory(raw, _acc())
    assert item.symbol == "2330"
    assert item.name == "台積電"
    assert item.avg_price == Decimal("850")


def test_map_unrealized() -> None:
    raw = {"symbol": "2330", "qty": "1000", "avg_price": "800", "last_price": "850", "pnl": "50000"}
    item = map_unrealized(raw, _acc())
    assert item.qty == 1000
    assert item.pnl == Decimal("50000")


def test_map_realized_parses_date() -> None:
    raw = {"trade_date": "2026-04-10", "symbol": "2330", "qty": 1000, "sell_price": 900, "buy_price": 800, "pnl": 100000}
    item = map_realized(raw, _acc())
    assert item.trade_date.isoformat() == "2026-04-10"
    assert item.pnl == Decimal("100000")


def test_map_buying_power() -> None:
    raw = {"cash": "10000", "buying_power": "25000"}
    bp = map_buying_power(raw, _acc())
    assert bp.cash == Decimal("10000")
    assert bp.buying_power == Decimal("25000")


def test_map_settlement_date_formats() -> None:
    for val in ("2026-04-20", "2026/04/21", "20260422"):
        item = map_settlement({"t_date": val, "amount": "1234"}, _acc())
        assert item.t_date.year == 2026
        assert item.amount == Decimal("1234")


def test_map_maintenance_decimal() -> None:
    raw = {"maintenance_rate": "180.5", "margin_value": "100000", "short_value": "0"}
    m = map_maintenance(raw, _acc())
    assert m.maintenance_rate == Decimal("180.5")
