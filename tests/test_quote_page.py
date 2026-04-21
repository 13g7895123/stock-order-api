"""QuotePage 煙霧測試（offscreen Qt）。"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stock_order_api.gui.pages.quote_page import QuotePage  # noqa: E402
from stock_order_api.realtime.models import Book, BookLevel, Channel, Trade  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


class _StubFubonClient:
    pass


class _StubRealtimeClient:
    def __init__(self) -> None:
        self.mode = "speed"
        self.subscribed: list[tuple[Channel, list[str]]] = []
        self.closed = False
        self.unsubscribed = False

    def on_data(self, handler: Any) -> None:
        pass

    def on_status(self, handler: Any) -> None:
        pass

    def subscribe(
        self, channel: Channel, symbols: list[str], *, intraday_odd_lot: bool = False
    ) -> list[Any]:
        self.subscribed.append((channel, list(symbols)))
        return []

    def unsubscribe_all(self) -> None:
        self.unsubscribed = True

    def close(self) -> None:
        self.closed = True


def _mk_trade(sym: str, price: str, size: int) -> Trade:
    return Trade(
        symbol=sym,
        price=Decimal(price),
        size=size,
        time=datetime(2025, 1, 1, 9, 0, 0),
        bid_ask="bid",
        total_volume=100,
        is_trial=False,
    )


def _mk_book(sym: str) -> Book:
    return Book(
        symbol=sym,
        time=datetime(2025, 1, 1, 9, 0, 0),
        bids=[
            BookLevel(price=Decimal("100.5"), size=10),
            BookLevel(price=Decimal("100.0"), size=20),
        ],
        asks=[
            BookLevel(price=Decimal("101.0"), size=15),
            BookLevel(price=Decimal("101.5"), size=25),
        ],
    )


def test_trade_updates_quote_table(qapp: QApplication) -> None:
    page = QuotePage(client=_StubFubonClient())  # type: ignore[arg-type]
    page._on_trade_ui(_mk_trade("2330", "650.00", 5))
    QCoreApplication.processEvents()
    assert page.tbl_quote.rowCount() == 1
    assert page.tbl_quote.item(0, 0).text() == "2330"
    assert "650.00" in page.tbl_quote.item(0, 1).text()


def test_book_updates_depth(qapp: QApplication) -> None:
    page = QuotePage(client=_StubFubonClient())  # type: ignore[arg-type]
    page._on_book_ui(_mk_book("2330"))
    QCoreApplication.processEvents()
    assert page.tbl_quote.rowCount() == 1
    # 選中該列後 depth 應顯示
    page.tbl_quote.selectRow(0)
    QCoreApplication.processEvents()
    assert "100.50" in page.tbl_depth.item(0, 1).text()
    assert "101.00" in page.tbl_depth.item(0, 2).text()


def test_subscribe_empty_symbols_shows_message(qapp: QApplication) -> None:
    page = QuotePage(client=_StubFubonClient())  # type: ignore[arg-type]
    page.ed_symbols.setText("")
    page._on_subscribe_clicked()
    assert "請先輸入" in page.lbl_status.text()


def test_subscribe_calls_rt(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    page = QuotePage(client=_StubFubonClient())  # type: ignore[arg-type]
    stub = _StubRealtimeClient()
    monkeypatch.setattr(page, "_ensure_rt", lambda: stub)
    page.ed_symbols.setText("2330, 2317")
    page._on_subscribe_clicked()
    channels = [c for c, _ in stub.subscribed]
    assert Channel.TRADES in channels
    assert Channel.BOOKS in channels
    syms = stub.subscribed[0][1]
    assert syms == ["2330", "2317"]
    assert "2" in page.lbl_status.text()  # "已訂閱 2 檔"


def test_unsubscribe_clears_tables(qapp: QApplication) -> None:
    page = QuotePage(client=_StubFubonClient())  # type: ignore[arg-type]
    page.rt = _StubRealtimeClient()  # type: ignore[assignment]
    page._on_trade_ui(_mk_trade("2330", "100", 1))
    page._on_unsub_clicked()
    assert page.tbl_quote.rowCount() == 0
    assert page.rt.unsubscribed is True  # type: ignore[union-attr]
