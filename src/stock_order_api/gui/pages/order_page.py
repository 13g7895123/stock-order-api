"""下單頁：下單表單 + 委託單列表（可改價/改量/刪單）。"""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stock_order_api.fubon.client import FubonClient
from stock_order_api.fubon.stock_order import (
    MARKET_TYPE_CHOICES,
    ORDER_TYPE_CHOICES,
    PRICE_TYPE_CHOICES,
    SIDE_CHOICES,
    TIF_CHOICES,
    OrderRecord,
    OrderRequest,
    StockOrderService,
)
from stock_order_api.gui.app import Worker, start_worker


class OrderPage(QWidget):
    """股票下單頁。"""

    ORDER_COLUMNS = [
        ("order_no", "委託單號"),
        ("symbol", "代號"),
        ("side", "買賣"),
        ("price", "價"),
        ("quantity", "委託量"),
        ("filled_qty", "成交量"),
        ("remain_qty", "剩餘"),
        ("price_type", "價別"),
        ("time_in_force", "TIF"),
        ("market_type", "盤別"),
        ("order_type", "類別"),
        ("status", "狀態"),
        ("last_time", "時間"),
    ]

    def __init__(self, client: FubonClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.svc = StockOrderService(client)
        self._pool = QThreadPool.globalInstance()
        self._order_rows: list[tuple[OrderRecord, Any]] = []

        root = QVBoxLayout(self)
        root.addWidget(self._build_form())
        root.addWidget(self._build_orders_box(), 1)

    # ---------------------------------------------------------------- form
    def _build_form(self) -> QWidget:
        box = QGroupBox("下單")
        form = QFormLayout(box)

        self.ed_symbol = QLineEdit()
        self.ed_symbol.setPlaceholderText("例如 2330")
        self.ed_symbol.setMaxLength(10)

        self.cbx_side = QComboBox()
        self.cbx_side.addItems(SIDE_CHOICES)

        self.sp_qty = QSpinBox()
        self.sp_qty.setRange(1, 10_000_000)
        self.sp_qty.setValue(1000)
        self.sp_qty.setSingleStep(1000)

        self.sp_price = QDoubleSpinBox()
        self.sp_price.setDecimals(2)
        self.sp_price.setRange(0.0, 100_000.0)
        self.sp_price.setSingleStep(0.5)
        self.sp_price.setValue(0.0)

        self.cbx_price_type = QComboBox()
        self.cbx_price_type.addItems(PRICE_TYPE_CHOICES)
        self.cbx_price_type.currentTextChanged.connect(self._on_price_type_changed)

        self.cbx_tif = QComboBox()
        self.cbx_tif.addItems(TIF_CHOICES)

        self.cbx_market = QComboBox()
        self.cbx_market.addItems(MARKET_TYPE_CHOICES)

        self.cbx_order_type = QComboBox()
        self.cbx_order_type.addItems(ORDER_TYPE_CHOICES)

        form.addRow("股票代號", self.ed_symbol)
        form.addRow("買賣", self.cbx_side)
        form.addRow("股數", self.sp_qty)
        form.addRow("價格", self.sp_price)
        form.addRow("價別", self.cbx_price_type)
        form.addRow("TIF", self.cbx_tif)
        form.addRow("盤別", self.cbx_market)
        form.addRow("類別", self.cbx_order_type)

        # 按鈕列
        btn_bar = QHBoxLayout()
        self.lbl_dry = QLabel()
        self._refresh_dry_label()
        btn_bar.addWidget(self.lbl_dry)
        btn_bar.addStretch(1)
        self.btn_preview = QPushButton("預覽")
        self.btn_preview.clicked.connect(self._preview)
        btn_bar.addWidget(self.btn_preview)
        self.btn_submit = QPushButton("送出委託")
        self.btn_submit.setStyleSheet("font-weight: bold;")
        self.btn_submit.clicked.connect(self._submit)
        btn_bar.addWidget(self.btn_submit)
        form.addRow(btn_bar)
        return box

    def _on_price_type_changed(self, value: str) -> None:
        # 市價/漲跌停/參考價：價格欄停用
        self.sp_price.setEnabled(value == "Limit")

    def _refresh_dry_label(self) -> None:
        if self.client.settings.dry_run:
            self.lbl_dry.setText(
                '<span style="color:#b00; font-weight:bold;">DRY_RUN 模式：不會實際送單</span>'
            )
        else:
            self.lbl_dry.setText(
                '<span style="color:#080;">LIVE 模式：會實際送出委託</span>'
            )

    # ---------------------------------------------------------- orders box
    def _build_orders_box(self) -> QWidget:
        box = QGroupBox("當日委託")
        lay = QVBoxLayout(box)

        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("重新整理")
        self.btn_refresh.clicked.connect(self._refresh_orders)
        self.btn_cancel = QPushButton("刪單")
        self.btn_cancel.clicked.connect(self._cancel_selected)
        self.btn_mod_px = QPushButton("改價")
        self.btn_mod_px.clicked.connect(self._modify_price_selected)
        self.btn_mod_qty = QPushButton("改量")
        self.btn_mod_qty.clicked.connect(self._modify_qty_selected)
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_mod_px)
        bar.addWidget(self.btn_mod_qty)
        bar.addWidget(self.btn_cancel)
        bar.addStretch(1)
        self.lbl_status = QLabel("尚未查詢")
        bar.addWidget(self.lbl_status)
        lay.addLayout(bar)

        self.tbl = QTableWidget(0, len(self.ORDER_COLUMNS))
        self.tbl.setHorizontalHeaderLabels([c[1] for c in self.ORDER_COLUMNS])
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl, 1)
        return box

    # --------------------------------------------------------------- helpers
    def _build_request(self) -> OrderRequest:
        price_type = self.cbx_price_type.currentText()
        price: str | None
        if price_type == "Limit":
            if self.sp_price.value() <= 0:
                raise ValueError("限價單價格需大於 0")
            price = f"{self.sp_price.value():.2f}"
        else:
            price = None
        return OrderRequest(
            symbol=self.ed_symbol.text().strip().upper(),
            side=self.cbx_side.currentText(),
            quantity=self.sp_qty.value(),
            price=price,
            price_type=price_type,
            time_in_force=self.cbx_tif.currentText(),
            market_type=self.cbx_market.currentText(),
            order_type=self.cbx_order_type.currentText(),
        )

    def _selected_record(self) -> tuple[OrderRecord, Any] | None:
        row = self.tbl.currentRow()
        if row < 0 or row >= len(self._order_rows):
            QMessageBox.information(self, "提示", "請先選擇一筆委託")
            return None
        return self._order_rows[row]

    # --------------------------------------------------------------- actions
    def _preview(self) -> None:
        try:
            req = self._build_request()
        except ValueError as exc:
            QMessageBox.warning(self, "參數錯誤", str(exc))
            return
        dry = "（DRY_RUN）" if self.client.settings.dry_run else ""
        text = (
            f"確認下單內容{dry}：\n\n"
            f"商品：{req.symbol}\n"
            f"方向：{req.side}\n"
            f"股數：{req.quantity:,}\n"
            f"價格：{req.price or '市價'}\n"
            f"價別：{req.price_type}\n"
            f"TIF：{req.time_in_force}\n"
            f"盤別：{req.market_type}\n"
            f"類別：{req.order_type}"
        )
        QMessageBox.information(self, "預覽", text)

    def _submit(self) -> None:
        try:
            req = self._build_request()
        except ValueError as exc:
            QMessageBox.warning(self, "參數錯誤", str(exc))
            return
        dry = "（DRY_RUN）" if self.client.settings.dry_run else ""
        if (
            QMessageBox.question(
                self,
                f"確認送出{dry}",
                f"即將{req.side} {req.symbol} {req.quantity:,} 股\n價格：{req.price or '市價'}\n\n確定送出？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self.btn_submit.setEnabled(False)
        self.lbl_status.setText("送出中…")

        def work() -> Any:
            return self.svc.place(req)

        w = Worker(work)
        w.signals.finished.connect(self._on_submit_ok)
        w.signals.failed.connect(self._on_submit_err)
        start_worker(self, self._pool, w)

    def _on_submit_ok(self, result: Any) -> None:
        self.btn_submit.setEnabled(True)
        if result.is_success:
            self.lbl_status.setText(
                f"OK order_no={result.order_no or '-'} status={result.status or '-'}"
            )
            QMessageBox.information(
                self,
                "下單完成",
                f"委託單號：{result.order_no or '-'}\n狀態：{result.status or '-'}\n訊息：{result.message or '-'}",
            )
            self._refresh_orders()
        else:
            self.lbl_status.setText(f"FAIL {result.message}")
            QMessageBox.warning(self, "下單失敗", str(result.message))

    def _on_submit_err(self, message: str, tb: str) -> None:
        self.btn_submit.setEnabled(True)
        self.lbl_status.setText(f"ERROR {message}")
        logger.bind(event="ORDER_UI_ERROR").error(f"{message}\n{tb}")
        QMessageBox.critical(self, "錯誤", message)

    # --------------------------------------------------------- orders list
    def _refresh_orders(self) -> None:
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("查詢委託…")

        def work() -> Any:
            return self.svc.list_orders()

        w = Worker(work)
        w.signals.finished.connect(self._on_orders_ok)
        w.signals.failed.connect(self._on_orders_err)
        start_worker(self, self._pool, w)

    def _on_orders_ok(self, rows: Any) -> None:
        self.btn_refresh.setEnabled(True)
        self._order_rows = list(rows)
        self.tbl.setRowCount(len(self._order_rows))
        for r, (rec, _raw) in enumerate(self._order_rows):
            for c, (key, _label) in enumerate(self.ORDER_COLUMNS):
                value = getattr(rec, key, "")
                item = QTableWidgetItem(str(value))
                if key == "side":
                    color = "#c00" if str(value).lower().startswith("b") else "#080"
                    item.setForeground(QBrush(QColor(color)))
                elif key in ("filled_qty", "quantity"):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.tbl.setItem(r, c, item)
        self.tbl.resizeColumnsToContents()
        self.lbl_status.setText(f"共 {len(self._order_rows)} 筆")

    def _on_orders_err(self, message: str, tb: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText(f"ERROR {message}")
        QMessageBox.critical(self, "錯誤", message)

    def _cancel_selected(self) -> None:
        picked = self._selected_record()
        if picked is None:
            return
        rec, raw = picked
        if (
            QMessageBox.question(
                self,
                "確認刪單",
                f"刪除委託 {rec.order_no} ({rec.symbol} {rec.side} {rec.quantity})？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._run_mod_action(lambda: self.svc.cancel(raw), "刪單")

    def _modify_price_selected(self) -> None:
        picked = self._selected_record()
        if picked is None:
            return
        rec, raw = picked
        current = 0.0
        with suppress(TypeError, ValueError):
            current = float(rec.price)
        value, ok = QInputDialog.getDouble(
            self, "改價", f"新價格（{rec.symbol}）:", current, 0.0, 100_000.0, 2
        )
        if not ok:
            return
        self._run_mod_action(
            lambda: self.svc.modify_price(raw, Decimal(f"{value:.2f}")), "改價"
        )

    def _modify_qty_selected(self) -> None:
        picked = self._selected_record()
        if picked is None:
            return
        rec, raw = picked
        remain = rec.remain_qty or rec.quantity
        value, ok = QInputDialog.getInt(
            self, "改量", f"新股數（{rec.symbol}，剩餘 {remain}）:", remain, 0, remain
        )
        if not ok:
            return
        self._run_mod_action(lambda: self.svc.modify_quantity(raw, value), "改量")

    def _run_mod_action(self, fn: Any, label: str) -> None:
        self.lbl_status.setText(f"{label}中…")
        w = Worker(fn)

        def _ok(res: Any) -> None:
            if getattr(res, "is_success", False):
                self.lbl_status.setText(f"{label} OK")
                QMessageBox.information(
                    self, label, f"成功\n{getattr(res, 'message', '') or ''}"
                )
                self._refresh_orders()
            else:
                self.lbl_status.setText(f"{label} FAIL")
                QMessageBox.warning(self, label, str(getattr(res, "message", "")))

        def _err(message: str, tb: str) -> None:
            self.lbl_status.setText(f"{label} ERROR")
            QMessageBox.critical(self, label, message)

        w.signals.finished.connect(_ok)
        w.signals.failed.connect(_err)
        start_worker(self, self._pool, w)
