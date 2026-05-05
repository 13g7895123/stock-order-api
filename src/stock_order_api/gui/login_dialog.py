"""登入對話框。讀 .env + keyring，缺欄位時手動輸入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

from stock_order_api.config import Settings, get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.gui.app import Worker, start_worker

_KEYRING_SERVICE = "stock-order-api"


class LoginDialog(QDialog):
    """登入資訊輸入對話框。呼叫 `.accept()` 後可從屬性取得欄位。"""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.setWindowTitle("富邦登入")
        self.setMinimumWidth(420)
        self._pool = QThreadPool.globalInstance()
        self._base_settings: Settings | None = None
        self._tested_settings: Settings | None = None
        self._testing = False

        self.ed_id = QLineEdit()
        self.ed_pw = QLineEdit()
        self.ed_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_cert = QLineEdit()
        self.ed_cert_pw = QLineEdit()
        self.ed_cert_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_branch = QLineEdit()
        self.ed_account = QLineEdit()
        self.ed_api_key = QLineEdit()
        self.ed_api_secret = QLineEdit()
        self.ed_api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cb_show_pw = QCheckBox("顯示密碼")
        self.cb_show_pw.stateChanged.connect(self._toggle_pw)
        self.cb_remember = QCheckBox("記住密碼（存入 OS keychain）")
        self.lbl_status = QLabel("尚未測試")
        self.lbl_status.setWordWrap(True)

        for editor in (
            self.ed_id,
            self.ed_pw,
            self.ed_cert,
            self.ed_cert_pw,
            self.ed_branch,
            self.ed_account,
            self.ed_api_key,
            self.ed_api_secret,
        ):
            editor.textChanged.connect(self._invalidate_test_result)

        btn_browse = QPushButton("瀏覽…")
        btn_browse.clicked.connect(self._pick_cert)
        cert_row = QHBoxLayout()
        cert_row.addWidget(self.ed_cert, 1)
        cert_row.addWidget(btn_browse)

        form = QFormLayout()
        form.addRow("身分證字號", self.ed_id)
        form.addRow("電子交易密碼", self.ed_pw)
        form.addRow("憑證 (.pfx)", cert_row)
        form.addRow("憑證密碼", self.ed_cert_pw)
        form.addRow("分公司代號", self.ed_branch)
        form.addRow("證券帳號", self.ed_account)
        form.addRow("API Key（選）", self.ed_api_key)
        form.addRow("Secret Key（選）", self.ed_api_secret)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_test = buttons.addButton("測試連線", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_test.clicked.connect(self._on_test_login)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self.btn_ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.btn_ok is not None:
            self.btn_ok.setText("登入")

        root = QVBoxLayout(self)
        root.addWidget(QLabel("請確認登入資訊（將寫回 .env 或 keyring）："))
        root.addLayout(form)
        root.addWidget(self.cb_show_pw)
        root.addWidget(self.cb_remember)
        root.addWidget(self.lbl_status)
        root.addWidget(buttons)

        self._prefill()

    # ------------------------------------------------------------
    def _prefill(self) -> None:
        try:
            s = get_settings()
            self._base_settings = s
            self.ed_id.setText(s.personal_id)
            self.ed_pw.setText(s.password.get_secret_value())
            self.ed_cert.setText(str(s.cert_path))
            self.ed_cert_pw.setText(s.cert_password.get_secret_value())
            self.ed_branch.setText(s.branch_no)
            self.ed_account.setText(s.account_no)
            if s.api_key:
                self.ed_api_key.setText(s.api_key.get_secret_value())
            if s.api_secret:
                self.ed_api_secret.setText(s.api_secret.get_secret_value())
        except Exception:
            # 沒有 .env 或欄位不完整：就留白讓使用者輸入
            pass

    def _invalidate_test_result(self) -> None:
        if self._testing:
            return
        self._tested_settings = None
        self.lbl_status.setText("尚未測試")

    def _toggle_pw(self, state: int) -> None:
        mode = (
            QLineEdit.EchoMode.Normal
            if state == Qt.CheckState.Checked.value
            else QLineEdit.EchoMode.Password
        )
        self.ed_pw.setEchoMode(mode)
        self.ed_cert_pw.setEchoMode(mode)
        self.ed_api_secret.setEchoMode(mode)

    def _pick_cert(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇 .pfx", "", "PKCS12 (*.pfx *.p12)")
        if path:
            self.ed_cert.setText(path)

    def _validate_inputs(self) -> bool:
        if not all(
            [
                self.ed_id.text(),
                self.ed_pw.text(),
                self.ed_cert.text(),
                self.ed_cert_pw.text(),
                self.ed_branch.text(),
                self.ed_account.text(),
            ]
        ):
            QMessageBox.warning(self, "欄位不完整", "請填寫必要欄位。")
            return False
        if not Path(self.ed_cert.text()).exists():
            QMessageBox.warning(self, "憑證檔案不存在", self.ed_cert.text())
            return False
        return True

    def _build_settings(self) -> Settings:
        payload: dict[str, Any] = {}
        if self._base_settings is not None:
            payload.update(self._base_settings.model_dump())
        payload.update(
            {
                "personal_id": self.ed_id.text().strip(),
                "password": self.ed_pw.text(),
                "cert_path": self.ed_cert.text().strip(),
                "cert_password": self.ed_cert_pw.text(),
                "branch_no": self.ed_branch.text().strip(),
                "account_no": self.ed_account.text().strip(),
                "api_key": self.ed_api_key.text().strip() or None,
                "api_secret": self.ed_api_secret.text().strip() or None,
            }
        )
        return Settings(**payload)

    def _set_testing_state(self, testing: bool) -> None:
        self._testing = testing
        self.btn_test.setEnabled(not testing)
        if self.btn_ok is not None:
            self.btn_ok.setEnabled(not testing)

    def _on_test_login(self) -> None:
        if self._testing:
            return
        if not self._validate_inputs():
            return

        self._tested_settings = None
        self._set_testing_state(True)
        self.lbl_status.setText("連線測試中…")

        def work() -> tuple[Settings, int, str]:
            settings = self._build_settings()
            client = FubonClient(settings)
            try:
                accounts = client.login()
                selected = client.account.display
                return settings, len(accounts), selected
            finally:
                client.logout()

        worker = Worker(work)
        worker.signals.finished.connect(self._on_test_login_ok)
        worker.signals.failed.connect(self._on_test_login_failed)
        start_worker(self, self._pool, worker)

    def _on_test_login_ok(self, result: tuple[Settings, int, str]) -> None:
        settings, account_count, selected = result
        self._tested_settings = settings
        self._set_testing_state(False)
        self.lbl_status.setText(f"連線成功：共 {account_count} 個帳號，預設 {selected}")

    def _on_test_login_failed(self, message: str, _tb: str) -> None:
        self._set_testing_state(False)
        self.lbl_status.setText(f"連線失敗：{message}")
        QMessageBox.warning(self, "連線失敗", message)

    def _on_accept(self) -> None:
        if self._testing:
            QMessageBox.information(self, "連線測試中", "請等待連線測試完成。")
            return
        if not self._validate_inputs():
            return
        if self.cb_remember.isChecked() and keyring is not None:
            try:
                keyring.set_password(_KEYRING_SERVICE, "password", self.ed_pw.text())
                keyring.set_password(_KEYRING_SERVICE, "cert_password", self.ed_cert_pw.text())
            except Exception:
                pass
        self.accept()

    # 方便呼叫端取值
    def values(self) -> dict[str, str]:
        return {
            "personal_id": self.ed_id.text().strip(),
            "password": self.ed_pw.text(),
            "cert_path": self.ed_cert.text().strip(),
            "cert_password": self.ed_cert_pw.text(),
            "branch_no": self.ed_branch.text().strip(),
            "account_no": self.ed_account.text().strip(),
            "api_key": self.ed_api_key.text().strip(),
            "api_secret": self.ed_api_secret.text(),
        }

    def resolved_settings(self) -> Settings:
        return self._tested_settings or self._build_settings()
