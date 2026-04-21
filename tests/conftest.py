"""pytest 共用 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """切到 tmp_path 以避免污染 cwd 下的 .env / logs。"""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """提供完整的 FUBON_* 環境變數（含一張測試用 .pfx）。"""
    cert = tmp_path / "test.pfx"
    cert.write_bytes(b"")  # 內容不重要，多數測試不會讀憑證
    env = {
        "FUBON_PERSONAL_ID": "A123456789",
        "FUBON_PASSWORD": "pw",
        "FUBON_CERT_PATH": str(cert),
        "FUBON_CERT_PASSWORD": "cp",
        "FUBON_BRANCH_NO": "6460",
        "FUBON_ACCOUNT_NO": "1234567",
        "FUBON_DRY_RUN": "true",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # 清空可能殘留的 FUBON_API_* key
    for extra in ("FUBON_API_KEY", "FUBON_API_SECRET"):
        monkeypatch.delenv(extra, raising=False)

    # 重新載入 config.Settings 快取
    sys.modules.pop("stock_order_api.config", None)
    return env
