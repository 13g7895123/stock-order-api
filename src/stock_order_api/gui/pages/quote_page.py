"""即時行情分頁：訂閱 trades/books，即時更新報價表與五檔深度。

SDK callback 來自背景執行緒，透過 Qt Signal 轉送回主執行緒再更新 UI。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stock_order_api.fubon.client import FubonClient
from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.errors import RealtimeError
from stock_order_api.realtime.models import Book, Channel, RealtimeMode, Trade

# 報價表欄位
QUOTE_COLUMNS = [
    ("symbol", "代號"),
    ("last", "成交"),
    ("size", "量"),
    ("bid1", "買一"),
    ("ask1", "賣一"),
    ("total_volume", "總量"),
    ("time", "時間"),
]

# 五檔表欄位
DEPTH_COLUMNS = [
    ("bid_size", "買量"),
    ("bid_price", "買價"),
    ("ask_price", "賣價"),
    ("ask_size", "賣量"),
]


class _RTBridge(QObject):
    """把 SDK 背景執行緒的資料轉成 Qt signal 傳回主執行緒。"""

    trade_arrived = Signal(object)  # Trade
    book_arrived = Signal(object)  # Book
    status_changed = Signal(str, object)  # event, payload


class QuotePage(QWidget):
    """即時行情分頁。"""

    def __init__(
        self,
        client: FubonClient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.rt: RealtimeClient | None = None
        self.bridge = _RTBridge()
        self.bridge.trade_arrived.connect(self._on_trade_ui, Qt.ConnectionType.QueuedConnection)
        self.bridge.book_arrived.connect(self._on_book_ui, Qt.ConnectionType.QueuedConnection)
        self.bridge.status_changed.connect(self._on_status_ui, Qt.ConnectionType.QueuedConnection)

        # 最新快照
        self._quotes: dict[str, dict[str, Any]] = {}
        self._books: dict[str, Book] = {}

        # --- top bar
        self.cbx_mode = QComboBox()
        self.cbx_mode.addItems(["speed", "normal"])
        self.ed_symbols = QLineEdit()
        self.ed_symbols.setPlaceholderText("以逗號分隔代號，例如：2330,2317,2454")
        self.btn_sub = QPushButton("訂閱")
        self.btn_sub.clicked.connect(self._on_subscribe_clicked)
        self.btn_unsub = QPushButton("全部取消")
        self.btn_unsub.clicked.connect(self._on_unsub_clicked)
        self.btn_close = QPushButton("關閉連線")
        self.btn_close.clicked.connect(self._on_close_clicked)
        self.lbl_status = QLabel("未連線")

        bar = QHBoxLayout()
        bar.addWidget(QLabel("<b>即時行情</b>"))
        bar.addSpacing(12)
        bar.addWidget(QLabel("Mode："))
        bar.addWidget(self.cbx_mode)
        bar.addSpacing(8)
        bar.addWidget(QLabel("商品："))
        bar.addWidget(self.ed_symbols, 2)
        bar.addWidget(self.btn_sub)
        bar.addWidget(self.btn_unsub)
        bar.addWidget(self.btn_close)
        bar.addStretch(1)
        bar.addWidget(self.lbl_status)

        # --- tables
        self.tbl_quote = QTableWidget(0, len(QUOTE_COLUMNS))
        self.tbl_quote.setHorizontalHeaderLabels([c[1] for c in QUOTE_COLUMNS])
        self.tbl_quote.setEditTriggers(self.tbl_quote.EditTrigger.NoEditTriggers)
        self.tbl_quote.setSelectionBehavior(self.tbl_quote.SelectionBehavior.SelectRows)
        self.tbl_quote.setSelectionMode(self.tbl_quote.SelectionMode.SingleSelection)
        self.tbl_quote.itemSelectionChanged.connect(self._refresh_depth)

        self.tbl_depth = QTableWidget(5, len(DEPTH_COLUMNS))
        self.tbl_depth.setHorizontalHeaderLabels([c[1] for c in DEPTH_COLUMNS])
        self.tbl_depth.setEditTriggers(self.tbl_depth.EditTrigger.NoEditTriggers)
        self.tbl_depth.verticalHeader().setVisible(False)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.tbl_quote)
        split.addWidget(self.tbl_depth)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

    # ------------------------------------------------------------------
    # subscribe / unsubscribe
    # ------------------------------------------------------------------
    def _ensure_rt(self) -> RealtimeClient:
        mode = RealtimeMode(self.cbx_mode.currentText())
        if self.rt is None or self.rt.mode != mode:
            if self.rt is not None:
                try:
                    self.rt.close()
                except Exception as exc:  # pragma: no cover
                    logger.bind(event="GUI_RT").warning(f"close old rt: {exc}")
            from stock_order_api.config import get_settings

            s = get_settings()
            self.rt = RealtimeClient(
                client=self.client,
                mode=mode,
                reconnect_base_sec=s.realtime_reconnect_base_sec,
                reconnect_max_sec=s.realtime_reconnect_max_sec,
                reconnect_max_attempts=s.realtime_reconnect_max,
                ring_buffer_size=s.realtime_ring_buffer,
                stats_interval_sec=s.realtime_stats_interval,
            )
            self.rt.on_data(self._dispatch_data)
            self.rt.on_status(self._dispatch_status)
        return self.rt

    def _dispatch_data(self, channel: Channel, dto: Any) -> None:
        """背景執行緒：只做 emit，不動 UI。"""
        if channel == Channel.TRADES and isinstance(dto, Trade):
            self.bridge.trade_arrived.emit(dto)
        elif channel == Channel.BOOKS and isinstance(dto, Book):
            self.bridge.book_arrived.emit(dto)

    def _dispatch_status(self, event: str, payload: dict[str, Any]) -> None:
        self.bridge.status_changed.emit(event, payload)

    def _on_subscribe_clicked(self) -> None:
        raw = self.ed_symbols.text().strip()
        if not raw:
            self.lbl_status.setText("請先輸入商品代號")
            return
        symbols = [s.strip() for s in raw.replace(" ", ",").split(",") if s.strip()]
        if not symbols:
            return
        try:
            rt = self._ensure_rt()
            rt.subscribe(Channel.TRADES, symbols)
            rt.subscribe(Channel.BOOKS, symbols)
        except RealtimeError as exc:
            self.lbl_status.setText(f"訂閱失敗：{exc}")
            logger.bind(event="GUI_RT").error(f"subscribe failed: {exc}")
            return
        self.lbl_status.setText(f"已訂閱 {len(symbols)} 檔")

    def _on_unsub_clicked(self) -> None:
        if self.rt is None:
            return
        try:
            self.rt.unsubscribe_all()
        except Exception as exc:  # pragma: no cover
            logger.bind(event="GUI_RT").warning(f"unsubscribe_all: {exc}")
        self._quotes.clear()
        self._books.clear()
        self.tbl_quote.setRowCount(0)
        for r in range(self.tbl_depth.rowCount()):
            for c in range(self.tbl_depth.columnCount()):
                self.tbl_depth.setItem(r, c, QTableWidgetItem(""))
        self.lbl_status.setText("已取消全部訂閱")

    def _on_close_clicked(self) -> None:
        if self.rt is None:
            return
        try:
            self.rt.close()
        except Exception as exc:  # pragma: no cover
            logger.bind(event="GUI_RT").warning(f"close: {exc}")
        self.rt = None
        self.lbl_status.setText("已關閉連線")

    # ------------------------------------------------------------------
    # UI update (主執行緒)
    # ------------------------------------------------------------------
    def _on_trade_ui(self, trade: Trade) -> None:
        q = self._quotes.setdefault(trade.symbol, {"symbol": trade.symbol})
        q["last"] = trade.price
        q["size"] = trade.size
        q["total_volume"] = trade.total_volume
        q["time"] = trade.time
        q["bid_ask"] = trade.bid_ask
        self._render_quote_row(trade.symbol)

    def _on_book_ui(self, book: Book) -> None:
        self._books[book.symbol] = book
        q = self._quotes.setdefault(book.symbol, {"symbol": book.symbol})
        q["bid1"] = book.bids[0].price if book.bids else None
        q["ask1"] = book.asks[0].price if book.asks else None
        q["time"] = book.time
        self._render_quote_row(book.symbol)
        if self._current_symbol() == book.symbol:
            self._render_depth(book)

    def _on_status_ui(self, event: str, payload: Any) -> None:
        # 顯示連線狀態（只對重要事件反應）
        if event in ("connect", "disconnect", "connection_failed", "error"):
            self.lbl_status.setText(f"{event}: {payload}")

    # ------------------------------------------------------------------
    # render helpers
    # ------------------------------------------------------------------
    def _current_symbol(self) -> str | None:
        row = self.tbl_quote.currentRow()
        if row < 0:
            return None
        item = self.tbl_quote.item(row, 0)
        return item.text() if item else None

    def _row_index_for(self, symbol: str) -> int:
        for r in range(self.tbl_quote.rowCount()):
            item = self.tbl_quote.item(r, 0)
            if item is not None and item.text() == symbol:
                return r
        # 新增一列
        r = self.tbl_quote.rowCount()
        self.tbl_quote.insertRow(r)
        return r

    def _render_quote_row(self, symbol: str) -> None:
        q = self._quotes.get(symbol)
        if q is None:
            return
        r = self._row_index_for(symbol)
        for c, (key, _label) in enumerate(QUOTE_COLUMNS):
            val = q.get(key)
            text = "" if val is None else self._fmt(key, val)
            item = QTableWidgetItem(text)
            if key in ("last", "size", "total_volume", "bid1", "ask1"):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if key == "last":
                ba = q.get("bid_ask")
                if ba == "ask":
                    item.setForeground(QBrush(QColor("#c62828")))
                elif ba == "bid":
                    item.setForeground(QBrush(QColor("#2e7d32")))
            self.tbl_quote.setItem(r, c, item)

    def _render_depth(self, book: Book) -> None:
        rows = max(len(book.bids), len(book.asks), 5)
        self.tbl_depth.setRowCount(rows)
        for i in range(rows):
            bid = book.bids[i] if i < len(book.bids) else None
            ask = book.asks[i] if i < len(book.asks) else None
            cells = [
                str(bid.size) if bid else "",
                self._fmt_price(bid.price) if bid else "",
                self._fmt_price(ask.price) if ask else "",
                str(ask.size) if ask else "",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 1:
                    item.setForeground(QBrush(QColor("#2e7d32")))
                elif c == 2:
                    item.setForeground(QBrush(QColor("#c62828")))
                self.tbl_depth.setItem(i, c, item)

    def _refresh_depth(self) -> None:
        sym = self._current_symbol()
        if sym and sym in self._books:
            self._render_depth(self._books[sym])

    @staticmethod
    def _fmt(key: str, val: Any) -> str:
        if key in ("last", "bid1", "ask1") and isinstance(val, Decimal):
            return f"{val:.2f}"
        if key == "time":
            return str(val)[:19]
        return str(val)

    @staticmethod
    def _fmt_price(p: Decimal) -> str:
        return f"{p:.2f}"
