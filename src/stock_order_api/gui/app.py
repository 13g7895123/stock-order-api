"""QApplication bootstrap + 共用工作執行緒。"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str, str)  # message, traceback


class Worker(QRunnable):
    """簡易 QRunnable：在背景呼叫 callable 並透過 signal 回報結果。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:  # pragma: no cover - thread entry
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.failed.emit(str(exc), traceback.format_exc())
            return
        self.signals.finished.emit(result)


def run_gui() -> int:
    """啟動整個 GUI（阻塞直到關閉）。"""
    from PySide6.QtWidgets import QApplication

    from stock_order_api.config import get_settings
    from stock_order_api.gui.main_window import MainWindow
    from stock_order_api.logging_setup import setup_logging

    s = get_settings()
    setup_logging(log_dir=s.log_dir, level="INFO")

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
