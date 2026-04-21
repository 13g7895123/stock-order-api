"""FubonClient：FubonSDK 單例封裝、登入、帳號選擇。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from stock_order_api.config import Settings, get_settings
from stock_order_api.fubon.cert import CertInfo, inspect_pfx
from stock_order_api.fubon.errors import (
    FubonLoginError,
    FubonSDKUnavailableError,
)


def _load_sdk() -> Any:
    """動態載入 fubon_neo；未安裝時丟出 FubonSDKUnavailableError。"""
    try:
        from fubon_neo.sdk import FubonSDK
    except Exception as exc:  # pragma: no cover - 需實際 SDK
        raise FubonSDKUnavailableError(
            "fubon-neo 套件未安裝或無法載入。請至 https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk "
            "取得對應平台 wheel 後以 `uv add ./fubon_neo-*.whl` 安裝。"
        ) from exc
    return FubonSDK


@dataclass
class AccountRef:
    """精簡版帳號描述，抽離 SDK 物件以便 GUI/CLI 顯示。"""

    raw: Any = field(repr=False)
    account: str = ""
    branch_no: str = ""
    account_type: str = ""
    account_name: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> AccountRef:
        def g(*names: str) -> str:
            for n in names:
                v = getattr(raw, n, None)
                if v:
                    return str(v)
            return ""

        return cls(
            raw=raw,
            account=g("account", "account_no"),
            branch_no=g("branch_no", "branch"),
            account_type=g("account_type", "type"),
            account_name=g("account_name", "name"),
        )

    @property
    def display(self) -> str:
        parts = [self.branch_no, self.account]
        base = "-".join(p for p in parts if p)
        if self.account_name:
            base += f" ({self.account_name})"
        return base or "<unknown>"


class FubonClient:
    """FubonSDK 單例 + 登入狀態管理。

    使用方式：
        client = FubonClient.instance()
        client.login()
        acc = client.account    # 目前選中的帳號（AccountRef）
        sdk = client.sdk        # 底層 FubonSDK 物件
    """

    _singleton: FubonClient | None = None
    _lock = threading.Lock()

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._sdk: Any = None
        self._accounts: list[AccountRef] = []
        self._current: AccountRef | None = None
        self._logged_in = False
        self._cert_info: CertInfo | None = None

    # ------------------------------------------------------------ singleton
    @classmethod
    def instance(cls, settings: Settings | None = None) -> FubonClient:
        with cls._lock:
            if cls._singleton is None:
                cls._singleton = cls(settings)
            return cls._singleton

    @classmethod
    def reset(cls) -> None:
        """清除 singleton（測試用）。"""
        with cls._lock:
            cls._singleton = None

    # ------------------------------------------------------------ props
    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            raise FubonLoginError("尚未呼叫 login()，SDK 未初始化")
        return self._sdk

    @property
    def account(self) -> AccountRef:
        if self._current is None:
            raise FubonLoginError("尚未選定帳號")
        return self._current

    @property
    def accounts(self) -> list[AccountRef]:
        return list(self._accounts)

    @property
    def cert_info(self) -> CertInfo | None:
        return self._cert_info

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    # ------------------------------------------------------------ login
    def login(self) -> list[AccountRef]:
        """執行憑證登入，並回傳歸戶帳號列表。"""
        s = self.settings
        cert_path = Path(s.cert_path)
        cert_pass = s.cert_password.get_secret_value()

        # 先檢查憑證資訊（可早期警示）
        self._cert_info = inspect_pfx(cert_path, cert_pass)
        logger.bind(event="CERT_INFO").info(
            f"cert ok: subject={self._cert_info.subject} "
            f"not_after={self._cert_info.not_after.isoformat()} "
            f"days_left={self._cert_info.days_left}"
        )
        if self._cert_info.expired:
            raise FubonLoginError(f"憑證已過期於 {self._cert_info.not_after.isoformat()}")

        FubonSDK = _load_sdk()
        self._sdk = FubonSDK(s.timeout_sec, s.reconnect_times)

        api_key = s.api_key.get_secret_value() if s.api_key else None
        api_secret = s.api_secret.get_secret_value() if s.api_secret else None

        t0 = time.perf_counter()
        if api_key and api_secret:
            mode = "login+apikey"
        elif api_key:
            mode = "apikey_login"
        else:
            mode = "login"
        logger.bind(event="LOGIN").info(f"logging in as {s.personal_id} (mode={mode})…")
        try:
            if api_key and api_secret:
                # 舊版（Key + Secret 成對）：六參數 login
                result = self._sdk.login(
                    s.personal_id,
                    s.password.get_secret_value(),
                    str(cert_path),
                    cert_pass,
                    api_key,
                    api_secret,
                )
            elif api_key:
                # 新版：只有 API Key（沒有 Secret），走 apikey_login
                result = self._sdk.apikey_login(
                    s.personal_id,
                    api_key,
                    str(cert_path),
                    cert_pass,
                )
            else:
                result = self._sdk.login(
                    s.personal_id,
                    s.password.get_secret_value(),
                    str(cert_path),
                    cert_pass,
                )
        except Exception as exc:
            logger.bind(event="LOGIN_FAILED").exception(str(exc))
            raise FubonLoginError(f"SDK 登入拋錯：{exc}") from exc

        elapsed = int((time.perf_counter() - t0) * 1000)
        if not getattr(result, "is_success", False):
            message = getattr(result, "message", "unknown error")
            logger.bind(event="LOGIN_FAILED").error(f"login failed: {message}")
            raise FubonLoginError(f"登入失敗：{message}")

        data = getattr(result, "data", []) or []
        self._accounts = [AccountRef.from_raw(a) for a in data]
        if not self._accounts:
            raise FubonLoginError("登入成功但未取得任何帳號")

        # 預設：依 .env 的 branch_no / account_no 挑；找不到就用第一筆
        preferred = self._match_account(s.branch_no, s.account_no)
        self._current = preferred or self._accounts[0]
        self._logged_in = True

        logger.bind(event="LOGIN").info(
            f"login ok: accounts={len(self._accounts)} "
            f"selected={self._current.display} elapsed_ms={elapsed}"
        )
        return list(self._accounts)

    def logout(self) -> None:
        if self._sdk is not None and hasattr(self._sdk, "logout"):
            try:
                self._sdk.logout()
            except Exception:  # pragma: no cover
                logger.bind(event="LOGOUT").warning("SDK logout raised; ignored")
        self._sdk = None
        self._accounts = []
        self._current = None
        self._logged_in = False

    def select_account(self, branch_no: str, account_no: str) -> AccountRef:
        acc = self._match_account(branch_no, account_no)
        if acc is None:
            raise FubonLoginError(f"找不到帳號 {branch_no}-{account_no}")
        self._current = acc
        logger.bind(event="ACCOUNT_SWITCH").info(f"switched to {acc.display}")
        return acc

    def _match_account(self, branch_no: str, account_no: str) -> AccountRef | None:
        for a in self._accounts:
            if a.branch_no == branch_no and a.account == account_no:
                return a
        return None
