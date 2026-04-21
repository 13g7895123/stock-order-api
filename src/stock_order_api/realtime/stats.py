"""RealtimeClient 的 STATS 指標蒐集：msg/s + latency p50/p95。

每 channel 各一組計數與延遲樣本；flush 時寫入 loguru 的 STATS event，
並回傳快照供測試與 GUI 使用。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loguru import logger

# 每 channel 保留多少延遲樣本
_MAX_SAMPLES = 2048


def _percentile(sorted_vals: list[float], p: float) -> float:
    """linear interpolation percentile（p 介於 0~100）。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


@dataclass
class ChannelStatsSnapshot:
    channel: str
    count: int
    msg_per_sec: float
    latency_p50_ms: float
    latency_p95_ms: float


@dataclass
class _ChannelBucket:
    count: int = 0
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))


class StatsCollector:
    """收集每 channel 的訊息量與延遲。"""

    def __init__(self, interval_sec: float = 10.0) -> None:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        self.interval_sec = interval_sec
        self._buckets: dict[str, _ChannelBucket] = defaultdict(_ChannelBucket)
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def record(self, channel: str, event_time: datetime | None) -> None:
        """記一筆訊息。event_time 為 payload 帶的時間，用來算延遲。"""
        latency_ms: float | None = None
        if event_time is not None:
            now = datetime.now(UTC)
            ev = event_time if event_time.tzinfo else event_time.replace(tzinfo=UTC)
            delta = (now - ev).total_seconds() * 1000.0
            # 只收合理區間（-60s ~ +10min）避免 epoch 解析錯誤拖垮 p95
            if -60_000.0 <= delta <= 600_000.0:
                latency_ms = delta
        with self._lock:
            bucket = self._buckets[channel]
            bucket.count += 1
            if latency_ms is not None:
                bucket.samples.append(latency_ms)

    # ------------------------------------------------------------------
    def flush(self) -> list[ChannelStatsSnapshot]:
        """取得目前快照並重置計數（延遲樣本也清空）。"""
        with self._lock:
            buckets = self._buckets
            self._buckets = defaultdict(_ChannelBucket)
            elapsed = max(time.monotonic() - self._started_at, 1e-6)
            self._started_at = time.monotonic()

        out: list[ChannelStatsSnapshot] = []
        for ch, b in buckets.items():
            samples = sorted(b.samples)
            out.append(
                ChannelStatsSnapshot(
                    channel=ch,
                    count=b.count,
                    msg_per_sec=b.count / elapsed,
                    latency_p50_ms=_percentile(samples, 50.0),
                    latency_p95_ms=_percentile(samples, 95.0),
                )
            )
        return out

    # ------------------------------------------------------------------
    def log_snapshot(self, snapshots: list[ChannelStatsSnapshot]) -> None:
        for s in snapshots:
            logger.bind(event="STATS").info(
                f"channel={s.channel} count={s.count} "
                f"msg_per_sec={s.msg_per_sec:.2f} "
                f"p50_ms={s.latency_p50_ms:.1f} p95_ms={s.latency_p95_ms:.1f}"
            )

    # ------------------------------------------------------------------
    def start(self) -> None:
        """啟動背景 thread，每 interval_sec flush 並 log 一次。"""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="realtime-stats", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=2.0)

    def _loop(self) -> None:  # pragma: no cover - thread loop
        while not self._stop.wait(self.interval_sec):
            try:
                self.log_snapshot(self.flush())
            except Exception as exc:
                logger.bind(event="STATS_ERR").exception(f"stats flush failed: {exc}")

    # expose for tests / GUI
    def as_dict(self, snapshots: list[ChannelStatsSnapshot]) -> dict[str, Any]:
        return {
            s.channel: {
                "count": s.count,
                "msg_per_sec": round(s.msg_per_sec, 3),
                "latency_p50_ms": round(s.latency_p50_ms, 1),
                "latency_p95_ms": round(s.latency_p95_ms, 1),
            }
            for s in snapshots
        }
