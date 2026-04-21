"""設定載入：讀取 .env、環境變數，並提供型別安全的 Settings。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """應用程式設定。

    讀取優先序：環境變數 > .env 檔案 > 預設值。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FUBON_",
        extra="ignore",
        case_sensitive=False,
    )

    # 登入
    personal_id: str = Field(..., description="身分證字號")
    password: SecretStr = Field(..., description="電子交易登入密碼")
    cert_path: Path = Field(..., description=".pfx 憑證路徑")
    cert_password: SecretStr = Field(..., description="憑證密碼")
    branch_no: str = Field(..., description="分公司代號")
    account_no: str = Field(..., description="證券帳號")

    # 可選：API Key / Secret Key
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None

    # 連線
    timeout_sec: int = 30
    reconnect_times: int = 2

    # 模式
    dry_run: bool = False

    # 路徑
    log_dir: Path = Path("logs")
    export_dir: Path = Path("exports")
    audit_db_path: Path = Path("logs/audit.sqlite3")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """取得全域 Settings 單例（快取）。"""
    return Settings()  # type: ignore[call-arg]


def reload_settings() -> Settings:
    """清除快取並重新讀取設定（測試/GUI 切換帳號用）。"""
    get_settings.cache_clear()
    return get_settings()
