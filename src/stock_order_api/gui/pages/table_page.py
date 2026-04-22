"""通用表格頁：單次／定時刷新、CSV 匯出、錯誤提示。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from loguru import logger
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stock_order_api.gui.app import Worker
from stock_order_api.utils.csv_export import export_rows, models_to_rows


class TablePage(QWidget):
    """可共用的資料表頁框。

    Parameters
    ----------
    title: 頁面顯示名稱（按鈕列標題）
    columns: 欄位 key 與顯示名對照 [(key, label)]
    fetcher: 無參數的 callable，回傳 list[BaseModel] / 單一 model / None
    auto_refresh_ms: 設定 >0 時顯示「自動刷新」勾選框
    money_keys: 需千分位右對齊的欄位 key
    color_keys: 需正紅負綠的欄位 key（例如 pnl）
    export_kind: CSV 檔名 kind
    post_load: 資料載入完成後在主執行緒呼叫的 callback(result)，可用於更新外部摘要元件
    """

    def __init__(
        self,
        *,
        title: str,
        columns: Sequence[tuple[str, str]],
        fetcher: Callable[[bool], Any],
        auto_refresh_ms: int = 0,
        money_keys: Iterable[str] = (),
        color_keys: Iterable[str] = (),
        export_kind: str = "data",
        post_load: Callable[[Any], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.money_keys = set(money_keys)
        self.color_keys = set(color_keys)
        self.fetcher = fetcher
        self.export_kind = export_kind
        self._post_load = post_load
        self._rows: list[dict[str, Any]] = []
        self._pool = QThreadPool.globalInstance()

        # --- toolbar
        self.lbl_status = QLabel("尚未查詢")
        self.btn_refresh = QPushButton("重新整理")
        self.btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        self.btn_export = QPushButton("匯出 CSV")
        self.btn_export.clicked.connect(self._export)
        self.cb_auto = QCheckBox(f"自動刷新（{auto_refresh_ms // 1000}s）") if auto_refresh_ms else None
        self._timer: QTimer | None = None
        if self.cb_auto is not None:
            self._timer = QTimer(self)
            self._timer.setInterval(auto_refresh_ms)
            self._timer.timeout.connect(lambda: self.refresh(force=False))
            self.cb_auto.stateChanged.connect(self._toggle_timer)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(f"<b>{title}</b>"))
        bar.addStretch(1)
        bar.addWidget(self.lbl_status)
        bar.addSpacing(12)
        bar.addWidget(self.btn_refresh)
        if self.cb_auto is not None:
            bar.addWidget(self.cb_auto)
        bar.addWidget(self.btn_export)

        # --- table
        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.columns])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        root = QVBoxLayout(self)
        root.addLayout(bar)
        root.addWidget(self.table, 1)

    # ------------------------------------------------------------
    def _toggle_timer(self, state: int) -> None:
        if self._timer is None:
            return
        if state == Qt.CheckState.Checked.value:
            self._timer.start()
        else:
            self._timer.stop()

    # ------------------------------------------------------------
    def refresh(self, force: bool = False) -> None:
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText("查詢中…")
        worker = Worker(self.fetcher, force)
        worker.signals.finished.connect(self._on_loaded)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)

    def _on_loaded(self, result: Any) -> None:
        self.btn_refresh.setEnabled(True)
        if result is None:
            self._rows = []
        elif isinstance(result, list):
            self._rows = models_to_rows(result)
        else:
            self._rows = models_to_rows([result])
        self._render()
        from datetime import datetime

        self.lbl_status.setText(
            f"最後更新：{datetime.now().strftime('%H:%M:%S')}（筆數：{len(self._rows)}）"
        )
        if self._post_load is not None:
            self._post_load(result)

    def _on_failed(self, message: str, tb: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText("查詢失敗")
        logger.bind(event="GUI_QUERY_FAILED").error(f"{message}\n{tb}")
        QMessageBox.critical(self, "查詢失敗", message)

    # ------------------------------------------------------------
    def _render(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            for c, (key, _) in enumerate(self.columns):
                v = row.get(key)
                item = QTableWidgetItem(self._fmt(key, v))
                if key in self.money_keys:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if key in self.color_keys and v is not None:
                    try:
                        f = float(v)
                        if f > 0:
                            item.setForeground(QBrush(QColor("#c62828")))
                        elif f < 0:
                            item.setForeground(QBrush(QColor("#2e7d32")))
                    except (TypeError, ValueError):
                        pass
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)

    def _fmt(self, key: str, v: Any) -> str:
        if v is None:
            return "-"
        if key in self.money_keys:
            try:
                return f"{float(v):,.2f}"
            except (TypeError, ValueError):
                return str(v)
        return str(v)

    # ------------------------------------------------------------
    def _export(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "匯出", "無資料可匯出")
            return
        fieldnames = [c[0] for c in self.columns]
        path = export_rows(self._rows, kind=self.export_kind, fieldnames=fieldnames)
        QMessageBox.information(self, "匯出完成", f"已寫入 {path}")


__all__ = ["TablePage"]
