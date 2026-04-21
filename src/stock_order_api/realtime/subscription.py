"""WebSocket 訂閱管理器 — 處理分片、註冊、取消。

富邦官方限制（plan-realtime.md §2）：
- 單連線最多 **200** 訂閱
- 同帳號最多 **5** 條連線 → 天花板 **1000** pair

本模組只負責「**規劃**」：給定 (channel, symbols)，回傳「哪幾條連線要訂哪些 symbol」。
真正呼叫 SDK 在 `client.py` 做。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import NamedTuple

from stock_order_api.realtime.errors import SubscriptionLimitError
from stock_order_api.realtime.models import Channel

MAX_SUB_PER_CONN: int = 200
MAX_CONNECTIONS: int = 5
MAX_TOTAL: int = MAX_SUB_PER_CONN * MAX_CONNECTIONS  # 1000


class SubKey(NamedTuple):
    """訂閱主鍵：同一 (channel, symbol, odd_lot) 視為同一個訂閱。"""

    channel: Channel
    symbol: str
    intraday_odd_lot: bool = False


@dataclass(frozen=True)
class SubscriptionSlot:
    """一筆訂閱的位置資訊。"""

    key: SubKey
    conn_idx: int
    sub_id: str | None = None  # 由 SDK 回 subscribed 事件後填入


@dataclass
class ShardPlan:
    """分片結果：哪條連線要訂哪些 symbol。"""

    conn_idx: int
    channel: Channel
    symbols: list[str]
    intraday_odd_lot: bool = False


@dataclass
class SubscriptionManager:
    """訂閱位置配置器（執行緒安全）。"""

    max_per_conn: int = MAX_SUB_PER_CONN
    max_connections: int = MAX_CONNECTIONS

    _slots: dict[SubKey, SubscriptionSlot] = field(default_factory=dict)
    #: 每條連線目前用了幾個 slot
    _usage: list[int] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ---- properties ----

    @property
    def max_total(self) -> int:
        return self.max_per_conn * self.max_connections

    @property
    def total_subscriptions(self) -> int:
        with self._lock:
            return len(self._slots)

    @property
    def connection_count(self) -> int:
        """目前有訂閱的連線數。"""
        with self._lock:
            return sum(1 for u in self._usage if u > 0)

    def usage_snapshot(self) -> list[int]:
        with self._lock:
            return list(self._usage)

    # ---- allocate ----

    def allocate(
        self,
        channel: Channel,
        symbols: list[str],
        *,
        intraday_odd_lot: bool = False,
    ) -> list[ShardPlan]:
        """預先配位：回傳要呼叫 SDK 的分片計畫。

        - 同 key 若已訂閱則跳過（不重複配）
        - 優先塞滿既有連線（index 小的先用）
        - 必要時開新連線，超過 max_connections 時 raise
        - 只有全數配位成功才會真的改動狀態
        """
        # 1) 去重 + 過濾已存在
        new_keys: list[SubKey] = []
        seen: set[SubKey] = set()
        for sym in symbols:
            key = SubKey(channel, sym, intraday_odd_lot)
            if key in seen:
                continue
            seen.add(key)
            with self._lock:
                if key in self._slots:
                    continue
            new_keys.append(key)

        if not new_keys:
            return []

        # 2) 計算配位（整包放進 lock）
        plans: dict[int, ShardPlan] = {}
        new_slots: dict[SubKey, SubscriptionSlot] = {}
        with self._lock:
            usage = list(self._usage)  # 模擬佔位，失敗則丟棄

            for key in new_keys:
                target_idx: int | None = None
                # 先找既有連線還有空位的
                for i, used in enumerate(usage):
                    if used < self.max_per_conn:
                        target_idx = i
                        break
                # 沒有 → 開新連線
                if target_idx is None:
                    if len(usage) >= self.max_connections:
                        raise SubscriptionLimitError(
                            f"超過最大連線數 {self.max_connections}；"
                            f"已使用 {sum(self._usage)} 個訂閱（上限 {self.max_total}）"
                        )
                    usage.append(0)
                    target_idx = len(usage) - 1

                usage[target_idx] += 1
                plan = plans.get(target_idx)
                if plan is None:
                    plan = ShardPlan(
                        conn_idx=target_idx,
                        channel=channel,
                        symbols=[],
                        intraday_odd_lot=intraday_odd_lot,
                    )
                    plans[target_idx] = plan
                plan.symbols.append(key.symbol)

                new_slots[key] = SubscriptionSlot(key=key, conn_idx=target_idx)

            # 全數配位成功才 commit
            self._slots.update(new_slots)
            self._usage = usage

        # 3) 回傳依 conn_idx 排序
        return [plans[i] for i in sorted(plans)]

    # ---- release ----

    def release(self, keys: list[SubKey]) -> list[SubKey]:
        """釋放訂閱；回傳「實際有釋放到」的 keys。"""
        released: list[SubKey] = []
        with self._lock:
            for key in keys:
                slot = self._slots.pop(key, None)
                if slot is None:
                    continue
                self._usage[slot.conn_idx] = max(0, self._usage[slot.conn_idx] - 1)
                released.append(key)
        return released

    def release_all(self) -> list[SubKey]:
        with self._lock:
            keys = list(self._slots.keys())
        return self.release(keys)

    def bind_sub_id(self, key: SubKey, sub_id: str) -> None:
        """SDK 回 `subscribed` 事件後，把 server 給的 id 記進來。"""
        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                return
            self._slots[key] = SubscriptionSlot(
                key=key, conn_idx=slot.conn_idx, sub_id=sub_id
            )

    def get_slot(self, key: SubKey) -> SubscriptionSlot | None:
        with self._lock:
            return self._slots.get(key)

    def all_slots(self) -> list[SubscriptionSlot]:
        with self._lock:
            return list(self._slots.values())
