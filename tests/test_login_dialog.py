"""LoginDialog 測試：連線測試成功/失敗需更新 GUI 狀態。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from stock_order_api.fubon.client import AccountRef  # noqa: E402
from stock_order_api.fubon.errors import FubonLoginError  # noqa: E402
from stock_order_api.gui.login_dialog import LoginDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


def _fill_required_fields(dlg: LoginDialog, cert_path: Path) -> None:
    dlg.ed_id.setText("A123456789")
    dlg.ed_pw.setText("pw")
    dlg.ed_cert.setText(str(cert_path))
    dlg.ed_cert_pw.setText("cp")
    dlg.ed_branch.setText("6460")
    dlg.ed_account.setText("1234567")


def _wait_until(predicate: Any, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def test_login_dialog_test_connection_success_updates_status(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert = tmp_path / "test.pfx"
    cert.write_bytes(b"ok")

    def _fake_login(self: Any) -> list[AccountRef]:
        time.sleep(0.02)
        account = AccountRef(raw=object(), branch_no="6460", account="1234567", account_name="測試")
        self._accounts = [account]
        self._current = account
        self._logged_in = True
        return [account]

    monkeypatch.setattr("stock_order_api.gui.login_dialog.FubonClient.login", _fake_login)

    dlg = LoginDialog()
    try:
        _fill_required_fields(dlg, cert)
        dlg._on_test_login()
        _wait_until(lambda: not dlg._testing)
        assert "連線成功" in dlg.lbl_status.text()
        assert "1234567" in dlg.lbl_status.text()
        resolved = dlg.resolved_settings()
        assert resolved.personal_id == "A123456789"
        assert resolved.branch_no == "6460"
    finally:
        dlg.close()


def test_login_dialog_test_connection_failure_updates_status(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert = tmp_path / "test.pfx"
    cert.write_bytes(b"ok")
    messages: list[str] = []

    def _fake_login(self: Any) -> list[AccountRef]:
        time.sleep(0.02)
        raise FubonLoginError("bad credentials")

    def _fake_warning(
        _parent: Any,
        _title: str,
        message: str,
        _buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        _default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> QMessageBox.StandardButton:
        messages.append(message)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr("stock_order_api.gui.login_dialog.FubonClient.login", _fake_login)
    monkeypatch.setattr(QMessageBox, "warning", _fake_warning)

    dlg = LoginDialog()
    try:
        _fill_required_fields(dlg, cert)
        dlg._on_test_login()
        _wait_until(lambda: not dlg._testing)
        assert "連線失敗" in dlg.lbl_status.text()
        assert "bad credentials" in dlg.lbl_status.text()
        assert messages == ["bad credentials"]
    finally:
        dlg.close()