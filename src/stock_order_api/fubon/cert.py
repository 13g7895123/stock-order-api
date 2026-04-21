"""憑證（.pfx）讀取與到期檢查。

以 `cryptography` 讀取 PKCS#12 取得憑證結束日期，供 GUI 顯示警示。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.serialization import pkcs12

from stock_order_api.fubon.errors import CertificateError


@dataclass
class CertInfo:
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime

    @property
    def days_left(self) -> int:
        delta = self.not_after - datetime.now(tz=UTC)
        return delta.days

    @property
    def expired(self) -> bool:
        return self.days_left < 0


def inspect_pfx(cert_path: Path | str, cert_password: str) -> CertInfo:
    """讀取 .pfx 並回傳憑證資訊。失敗時 raise CertificateError。"""
    path = Path(cert_path)
    if not path.exists():
        raise CertificateError(f"憑證檔案不存在：{path}")

    try:
        data = path.read_bytes()
        _key, cert, _chain = pkcs12.load_key_and_certificates(
            data, cert_password.encode("utf-8") if cert_password else None
        )
    except Exception as exc:  # pragma: no cover - 主要是密碼錯誤
        raise CertificateError(f"無法解析憑證（密碼錯誤或格式不符）：{exc}") from exc

    if cert is None:
        raise CertificateError("憑證檔案內未包含 X.509 憑證")

    try:
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
    except AttributeError:  # cryptography <42
        not_before = cert.not_valid_before.replace(tzinfo=UTC)
        not_after = cert.not_valid_after.replace(tzinfo=UTC)

    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_before=not_before,
        not_after=not_after,
    )
