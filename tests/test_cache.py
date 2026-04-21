"""測試 TTL cache（含 SQLite 持久層）。"""

from __future__ import annotations

import time
from pathlib import Path

from stock_order_api.audit.store import AuditStore
from stock_order_api.utils.cache import TTLCache


def test_memory_cache_hit_and_expire(tmp_path: Path) -> None:
    cache = TTLCache(store=None)
    calls = {"n": 0}

    def loader() -> dict[str, int]:
        calls["n"] += 1
        return {"v": calls["n"]}

    v1, src1 = cache.get_or_fetch("k", ttl_sec=60, loader=loader)
    v2, src2 = cache.get_or_fetch("k", ttl_sec=60, loader=loader)
    assert v1 == v2 == {"v": 1}
    assert src1 == "api"
    assert src2 == "memory"

    # force refresh
    v3, src3 = cache.get_or_fetch("k", ttl_sec=60, loader=loader, force_refresh=True)
    assert v3 == {"v": 2}
    assert src3 == "api"


def test_ttl_expiry() -> None:
    cache = TTLCache(store=None)
    cache.set("k", ttl_sec=0, value={"x": 1})
    time.sleep(0.01)
    assert cache.get("k", ttl_sec=0) is None


def test_disk_fallback(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "a.sqlite3")
    c1 = TTLCache(store=store)
    c1.set("k1", ttl_sec=60, value={"v": 99})
    # 模擬新進程：新的 TTLCache 共用同一 store
    c2 = TTLCache(store=store)
    hit = c2.get("k1", ttl_sec=60)
    assert hit is not None
    assert hit.value == {"v": 99}
    assert hit.source == "disk"


def test_invalidate_prefix(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "a.sqlite3")
    c = TTLCache(store=store)
    c.set("inventories:X", 60, [1])
    c.set("inventories:Y", 60, [2])
    c.set("buying_power:X", 60, [3])
    removed = c.invalidate("inventories:")
    assert removed == 2
    assert c.get("inventories:X", 60) is None
    assert c.get("buying_power:X", 60) is not None
