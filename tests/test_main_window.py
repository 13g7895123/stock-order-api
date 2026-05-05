"""MainWindow 煙霧測試（確認帳務 Tab × 5 + 即時行情 Tab 都掛上）。"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


def test_main_window_assembles(
    qapp: QApplication, env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 避免真的嘗試登入
    from stock_order_api.config import reload_settings
    from stock_order_api.fubon import client as client_mod
    from stock_order_api.fubon import errors as err_mod

    reload_settings()

    def _noop_login(self: Any) -> Any:
        raise err_mod.FubonError("skipped")

    monkeypatch.setattr(client_mod.FubonClient, "login", _noop_login)

    from stock_order_api.gui.main_window import MainWindow

    win = MainWindow()
    try:
        # 七個 Tab：庫存 / 未實現 / 已實現 / 現金&交割 / 維持率 / 即時行情 / 下單
        assert win.tabs.count() == 7
        titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        assert "即時行情" in titles
        assert "下單" in titles
        assert "庫存" in titles
        assert hasattr(win, "page_quote")
        assert hasattr(win, "page_order")
        # 即時行情分頁需有 mode 下拉與訂閱按鈕
        assert win.page_quote.cbx_mode.count() == 2
        assert win.page_quote.btn_sub is not None
        # 下單頁需有 submit 按鈕
        assert win.page_order.btn_submit is not None
    finally:
        win.close()


def test_do_login_uses_dialog_settings(
    qapp: QApplication, env_vars: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_order_api.config import Settings, reload_settings
    from stock_order_api.fubon.client import AccountRef, FubonClient
    from stock_order_api.gui import main_window as main_window_mod

    reload_settings()

    monkeypatch.setattr(main_window_mod.MainWindow, "_auto_login", lambda self: None)
    monkeypatch.setattr(main_window_mod.MainWindow, "_refresh_all", lambda self: None)

    custom = Settings(
        personal_id="B987654321",
        password="pw2",
        cert_path=env_vars["FUBON_CERT_PATH"],
        cert_password="cp2",
        branch_no="9667",
        account_no="0312174",
        dry_run=False,
    )

    class _StubLoginDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, _parent: Any) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

        def resolved_settings(self) -> Settings:
            return custom

    def _fake_login(self: FubonClient) -> list[AccountRef]:
        account = AccountRef(
            raw=object(),
            branch_no=self.settings.branch_no,
            account=self.settings.account_no,
            account_name="測試帳號",
        )
        self._accounts = [account]
        self._current = account
        self._logged_in = True
        return [account]

    monkeypatch.setattr(main_window_mod, "LoginDialog", _StubLoginDialog)
    monkeypatch.setattr(FubonClient, "login", _fake_login)

    win = main_window_mod.MainWindow()
    try:
        win._do_login()
        assert win.client.settings.personal_id == "B987654321"
        assert win.client.settings.branch_no == "9667"
        assert win.client.settings.account_no == "0312174"
        assert "B987654321" in win.lbl_login.text()
    finally:
        win.close()
