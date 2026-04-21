"""TTL 快取（行程內 + 透傳到 AuditStore 持久化）。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Generic, TypeVar

from stock_order_api.audit.store import AuditStore

T = TypeVar("T")


@dataclass
class CacheHit(Generic[T]):
    value: T
    fetched_at: float  # unix ts
    source: str        # "memory" | "disk"


class TTLCache:
    """雙層 TTL 快取：記憶體 dict + 選用的 SQLite 持久層。

    - `get_or_fetch(key, ttl, loader)` 為主要介面。
    - 值必須是 JSON-可序列化（以 payload_json 儲存於 SQLite）。
    """

    def __init__(self, store: AuditStore | None = None) -> None:
        self._store = store
        self._lock = RLock()
        self._mem: dict[str, tuple[float, int, Any]] = {}  # key -> (fetched_ts, ttl, value)

    # ------------------------------------------------------------
    def get(self, key: str, ttl_sec: int) -> CacheHit[Any] | None:
        now = time.time()
        with self._lock:
            entry = self._mem.get(key)
            if entry is not None:
                fetched_ts, ttl, value = entry
                if now - fetched_ts <= ttl:
                    return CacheHit(value=value, fetched_at=fetched_ts, source="memory")
                self._mem.pop(key, None)

        if self._store is not None:
            row = self._store.cache_get(key)
            if row is not None:
                fetched_iso, stored_ttl, payload_json = row
                fetched_ts = _iso_to_ts(fetched_iso)
                effective_ttl = min(stored_ttl, ttl_sec)
                if now - fetched_ts <= effective_ttl:
                    value = json.loads(payload_json)
                    with self._lock:
                        self._mem[key] = (fetched_ts, effective_ttl, value)
                    return CacheHit(value=value, fetched_at=fetched_ts, source="disk")
        return None

    def set(self, key: str, ttl_sec: int, value: Any) -> None:
        now = time.time()
        with self._lock:
            self._mem[key] = (now, ttl_sec, value)
        if self._store is not None:
            self._store.cache_set(key, ttl_sec, value)

    def invalidate(self, prefix: str = "") -> int:
        with self._lock:
            if prefix:
                keys = [k for k in self._mem if k.startswith(prefix)]
                for k in keys:
                    self._mem.pop(k, None)
                removed = len(keys)
            else:
                removed = len(self._mem)
                self._mem.clear()
        if self._store is not None:
            self._store.cache_invalidate(prefix)
        return removed

    def get_or_fetch(
        self,
        key: str,
        ttl_sec: int,
        loader: Callable[[], T],
        *,
        force_refresh: bool = False,
    ) -> tuple[T, str]:
        """取得或填充。回傳 (value, source)；source ∈ {memory, disk, api}。"""
        if not force_refresh:
            hit = self.get(key, ttl_sec)
            if hit is not None:
                return hit.value, hit.source
        value = loader()
        self.set(key, ttl_sec, value)
        return value, "api"


def _iso_to_ts(iso: str) -> float:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()
