"""股票代號 → 中文名稱解析器。

透過富邦 SDK 的 REST marketdata (`sdk.marketdata.rest_client.stock.intraday.ticker`)
取得商品名稱並快取於記憶體。取不到時回傳 None（不 raise），以免拖垮帳務查詢主流程。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from loguru import logger


class SymbolNameResolver:
    """Thread-safe 的 symbol→name 記憶體快取。"""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._rest: Any = None

    # ------------------------------------------------------------------
    def _get_rest(self) -> Any:
        """延遲初始化 REST client；需要 SDK 已 `init_realtime` 或有 token。"""
        if self._rest is not None:
            return self._rest
        sdk = getattr(self._client, "sdk", None)
        if sdk is None:
            return None
        md = getattr(sdk, "marketdata", None)
        if md is None:
            # 嘗試主動 init_realtime（Normal 模式不影響 Speed 使用）
            try:
                from fubon_neo.sdk import Mode

                sdk.init_realtime(Mode.Normal)
                md = getattr(sdk, "marketdata", None)
            except Exception as exc:  # pragma: no cover
                logger.bind(event="NAME_RESOLVE").debug(
                    f"init_realtime failed: {exc}"
                )
                return None
        rc = getattr(md, "rest_client", None) if md is not None else None
        if rc is None:
            return None
        self._rest = rc
        return rc

    # ------------------------------------------------------------------
    def _fetch_one(self, symbol: str) -> str | None:
        rc = self._get_rest()
        if rc is None:
            return None
        try:
            info = rc.stock.intraday.ticker(symbol=symbol)
        except Exception as exc:  # pragma: no cover
            logger.bind(event="NAME_RESOLVE").debug(
                f"ticker({symbol}) failed: {exc}"
            )
            return None
        if isinstance(info, dict):
            for key in ("name", "nameZhTw", "nameEn", "shortName"):
                v = info.get(key)
                if v:
                    return str(v)
        return None

    # ------------------------------------------------------------------
    def resolve(self, symbol: str) -> str | None:
        """取得單一商品名稱，失敗回傳 None。"""
        if not symbol:
            return None
        with self._lock:
            if symbol in self._cache:
                return self._cache[symbol]
        name = self._fetch_one(symbol)
        with self._lock:
            self._cache[symbol] = name
        return name

    def resolve_many(self, symbols: Iterable[str]) -> dict[str, str | None]:
        """批次查詢；回傳 {symbol: name or None}。"""
        out: dict[str, str | None] = {}
        for s in set(symbols):
            if not s:
                continue
            out[s] = self.resolve(s)
        return out

    def prime(self, mapping: dict[str, str | None]) -> None:
        """把外部已知的對照表灌進快取（測試 / 初始 bulk 載入用）。"""
        with self._lock:
            self._cache.update(mapping)
