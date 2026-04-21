"""帳務模組自訂例外。"""

from __future__ import annotations


class FubonError(Exception):
    """富邦 SDK 相關例外基底。"""


class FubonSDKUnavailableError(FubonError):
    """fubon-neo 套件未安裝或無法載入。"""


class FubonLoginError(FubonError):
    """登入失敗。"""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class FubonAccountError(FubonError):
    """帳務查詢失敗。"""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class CertificateError(FubonError):
    """憑證讀取 / 到期等錯誤。"""
