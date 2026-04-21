"""Thread-safe per-symbol ring buffer。"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable
from typing import Generic, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """固定容量、thread-safe 的 ring buffer。"""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._buf: deque[T] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def append(self, item: T) -> None:
        with self._lock:
            self._buf.append(item)

    def snapshot(self) -> list[T]:
        with self._lock:
            return list(self._buf)

    def extend(self, items: Iterable[T]) -> None:
        with self._lock:
            self._buf.extend(items)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


class PerSymbolRingBuffer(Generic[T]):
    """以 (channel, symbol) 為 key 的 ring buffer 集合。"""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._bufs: dict[tuple[str, str], RingBuffer[T]] = {}
        self._lock = threading.Lock()

    def append(self, channel: str, symbol: str, item: T) -> None:
        with self._lock:
            key = (channel, symbol)
            buf = self._bufs.get(key)
            if buf is None:
                buf = RingBuffer[T](self._capacity)
                self._bufs[key] = buf
        buf.append(item)

    def snapshot(self, channel: str, symbol: str) -> list[T]:
        with self._lock:
            buf = self._bufs.get((channel, symbol))
        return buf.snapshot() if buf is not None else []

    def keys(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._bufs.keys())

    def clear(self) -> None:
        with self._lock:
            self._bufs.clear()
