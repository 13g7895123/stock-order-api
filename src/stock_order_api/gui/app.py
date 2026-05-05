"""QApplication bootstrap + 共用工作執行緒。"""

from __future__ import annotations

import sys
import traceback
import weakref
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, QThreadPool


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


def start_worker(owner: Any, pool: QThreadPool, worker: Worker) -> Worker:
    """保留背景 worker 直到 finished/failed，避免 UI callback 遺失。"""

    pending = getattr(owner, "_active_workers", None)
    if pending is None:
        pending = set()
        setattr(owner, "_active_workers", pending)
    pending.add(worker)
    owner_ref = weakref.ref(owner)

    def _release(*_args: Any) -> None:
        current_owner = owner_ref()
        if current_owner is None:
            return
        current_pending = getattr(current_owner, "_active_workers", None)
        if current_pending is not None:
            current_pending.discard(worker)

    worker.signals.finished.connect(_release)
    worker.signals.failed.connect(_release)
    pool.start(worker)
    return worker


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
