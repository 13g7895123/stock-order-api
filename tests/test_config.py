"""測試 Settings：必填欄位缺漏會拋錯。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_settings_requires_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    for env in (
        "FUBON_PERSONAL_ID",
        "FUBON_PASSWORD",
        "FUBON_CERT_PATH",
        "FUBON_CERT_PASSWORD",
        "FUBON_BRANCH_NO",
        "FUBON_ACCOUNT_NO",
    ):
        monkeypatch.delenv(env, raising=False)
    # 確保不會讀到 cwd 下的 .env
    monkeypatch.chdir("/tmp")
    from stock_order_api.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_reads_env(env_vars: dict[str, str]) -> None:
    from stock_order_api.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.personal_id == env_vars["FUBON_PERSONAL_ID"]
    assert s.password.get_secret_value() == env_vars["FUBON_PASSWORD"]
    assert str(s.cert_path) == env_vars["FUBON_CERT_PATH"]
    assert s.branch_no == env_vars["FUBON_BRANCH_NO"]
    assert s.dry_run is True
    assert s.api_key is None
