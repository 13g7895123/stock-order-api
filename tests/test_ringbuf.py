"""RingBuffer / PerSymbolRingBuffer tests."""

from __future__ import annotations

import threading

import pytest

from stock_order_api.utils.ringbuf import PerSymbolRingBuffer, RingBuffer


def test_ring_buffer_basic() -> None:
    rb: RingBuffer[int] = RingBuffer(3)
    rb.append(1)
    rb.append(2)
    rb.append(3)
    assert rb.snapshot() == [1, 2, 3]
    assert len(rb) == 3


def test_ring_buffer_overflow_drops_oldest() -> None:
    rb: RingBuffer[int] = RingBuffer(3)
    for i in range(5):
        rb.append(i)
    assert rb.snapshot() == [2, 3, 4]
    assert len(rb) == 3


def test_ring_buffer_clear() -> None:
    rb: RingBuffer[int] = RingBuffer(5)
    rb.extend([1, 2, 3])
    rb.clear()
    assert rb.snapshot() == []


def test_ring_buffer_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RingBuffer(0)


def test_ring_buffer_thread_safety() -> None:
    rb: RingBuffer[int] = RingBuffer(500)

    def worker(start: int) -> None:
        for i in range(100):
            rb.append(start + i)

    threads = [threading.Thread(target=worker, args=(k * 1000,)) for k in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(rb) == 500


def test_per_symbol_buffer() -> None:
    psb: PerSymbolRingBuffer[int] = PerSymbolRingBuffer(2)
    psb.append("trades", "2330", 1)
    psb.append("trades", "2330", 2)
    psb.append("trades", "2330", 3)
    psb.append("books", "2330", 99)
    assert psb.snapshot("trades", "2330") == [2, 3]
    assert psb.snapshot("books", "2330") == [99]
    assert set(psb.keys()) == {("trades", "2330"), ("books", "2330")}
    assert psb.snapshot("trades", "9999") == []
