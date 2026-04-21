"""StatsCollector tests."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from stock_order_api.realtime.stats import StatsCollector, _percentile


def test_percentile_basic() -> None:
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 50.0) == pytest.approx(3.0)
    assert _percentile(vals, 100.0) == pytest.approx(5.0)
    assert _percentile(vals, 0.0) == pytest.approx(1.0)
    assert _percentile([], 50.0) == 0.0


def test_stats_counts_messages() -> None:
    sc = StatsCollector(interval_sec=10.0)
    for _ in range(10):
        sc.record("trades", None)
    for _ in range(3):
        sc.record("books", None)
    snap = {s.channel: s for s in sc.flush()}
    assert snap["trades"].count == 10
    assert snap["books"].count == 3
    # flush 後計數清零
    assert sc.flush() == []


def test_stats_latency_percentiles() -> None:
    sc = StatsCollector(interval_sec=10.0)
    now = datetime.now(UTC)
    # 10 筆，延遲 100ms, 200ms, ..., 1000ms
    for i in range(1, 11):
        sc.record("trades", now - timedelta(milliseconds=100 * i))
    snap = {s.channel: s for s in sc.flush()}
    t = snap["trades"]
    assert t.count == 10
    # p50 應該落在 500~600ms 附近
    assert 400 <= t.latency_p50_ms <= 700
    # p95 應該在 900ms 以上
    assert t.latency_p95_ms >= 850


def test_stats_msg_per_sec() -> None:
    sc = StatsCollector(interval_sec=10.0)
    for _ in range(100):
        sc.record("trades", None)
    time.sleep(0.05)
    snap = {s.channel: s for s in sc.flush()}
    assert snap["trades"].msg_per_sec > 0


def test_stats_skips_absurd_latency() -> None:
    sc = StatsCollector(interval_sec=10.0)
    # 一年前的時間戳 → 應被丟掉
    long_ago = datetime.now(UTC) - timedelta(days=365)
    sc.record("trades", long_ago)
    sc.record("trades", None)
    snap = {s.channel: s for s in sc.flush()}
    assert snap["trades"].count == 2
    assert snap["trades"].latency_p50_ms == 0.0  # 沒有合理樣本


def test_stats_as_dict() -> None:
    sc = StatsCollector(interval_sec=10.0)
    sc.record("trades", None)
    d = sc.as_dict(sc.flush())
    assert "trades" in d
    assert d["trades"]["count"] == 1


def test_stats_invalid_interval() -> None:
    with pytest.raises(ValueError):
        StatsCollector(interval_sec=0)
