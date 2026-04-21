"""測試 SubscriptionManager 分片演算法。"""

from __future__ import annotations

import pytest

from stock_order_api.realtime.errors import SubscriptionLimitError
from stock_order_api.realtime.models import Channel
from stock_order_api.realtime.subscription import (
    MAX_CONNECTIONS,
    MAX_SUB_PER_CONN,
    MAX_TOTAL,
    SubKey,
    SubscriptionManager,
)


def test_small_fits_one_connection() -> None:
    m = SubscriptionManager()
    plans = m.allocate(Channel.TRADES, ["2330", "2317", "2454"])
    assert len(plans) == 1
    assert plans[0].conn_idx == 0
    assert plans[0].symbols == ["2330", "2317", "2454"]
    assert m.total_subscriptions == 3
    assert m.connection_count == 1


def test_exact_200_stays_one_connection() -> None:
    m = SubscriptionManager()
    syms = [f"S{i:04d}" for i in range(200)]
    plans = m.allocate(Channel.TRADES, syms)
    assert len(plans) == 1
    assert len(plans[0].symbols) == 200
    assert m.connection_count == 1


def test_201_splits_two_connections() -> None:
    m = SubscriptionManager()
    syms = [f"S{i:04d}" for i in range(201)]
    plans = m.allocate(Channel.TRADES, syms)
    assert len(plans) == 2
    assert len(plans[0].symbols) == 200
    assert len(plans[1].symbols) == 1
    assert m.connection_count == 2


def test_fills_existing_connection_before_opening_new() -> None:
    m = SubscriptionManager()
    m.allocate(Channel.TRADES, [f"A{i}" for i in range(150)])
    # 再訂 100 檔 → 先塞滿第一條（50 個空位），剩下 50 開第二條
    plans = m.allocate(Channel.TRADES, [f"B{i}" for i in range(100)])
    assert len(plans) == 2
    assert plans[0].conn_idx == 0
    assert len(plans[0].symbols) == 50
    assert plans[1].conn_idx == 1
    assert len(plans[1].symbols) == 50


def test_exceed_max_total_raises() -> None:
    m = SubscriptionManager()
    syms = [f"S{i:04d}" for i in range(MAX_TOTAL)]
    m.allocate(Channel.TRADES, syms)
    with pytest.raises(SubscriptionLimitError):
        m.allocate(Channel.TRADES, ["OVERFLOW"])


def test_1001_raises_and_rolls_back() -> None:
    m = SubscriptionManager()
    syms = [f"S{i:04d}" for i in range(MAX_TOTAL + 1)]
    with pytest.raises(SubscriptionLimitError):
        m.allocate(Channel.TRADES, syms)
    # 失敗時不應留下部分狀態
    assert m.total_subscriptions == 0
    assert m.connection_count == 0


def test_duplicate_subscription_skipped() -> None:
    m = SubscriptionManager()
    m.allocate(Channel.TRADES, ["2330", "2317"])
    plans = m.allocate(Channel.TRADES, ["2330", "2454"])
    assert len(plans) == 1
    assert plans[0].symbols == ["2454"]
    assert m.total_subscriptions == 3


def test_different_channels_use_independent_slots() -> None:
    m = SubscriptionManager()
    m.allocate(Channel.TRADES, ["2330"])
    m.allocate(Channel.BOOKS, ["2330"])
    assert m.total_subscriptions == 2


def test_odd_lot_treated_as_different_key() -> None:
    m = SubscriptionManager()
    m.allocate(Channel.TRADES, ["2330"], intraday_odd_lot=False)
    plans = m.allocate(Channel.TRADES, ["2330"], intraday_odd_lot=True)
    assert len(plans) == 1
    assert m.total_subscriptions == 2


def test_release_frees_slots() -> None:
    m = SubscriptionManager()
    m.allocate(Channel.TRADES, ["2330", "2317"])
    released = m.release([SubKey(Channel.TRADES, "2330")])
    assert released == [SubKey(Channel.TRADES, "2330")]
    assert m.total_subscriptions == 1
    # 釋放後該位置應可被新訂閱填回
    m.allocate(Channel.TRADES, ["2454"])
    assert m.total_subscriptions == 2


def test_release_unknown_key_is_noop() -> None:
    m = SubscriptionManager()
    released = m.release([SubKey(Channel.TRADES, "9999")])
    assert released == []


def test_constants_match_plan() -> None:
    assert MAX_SUB_PER_CONN == 200
    assert MAX_CONNECTIONS == 5
    assert MAX_TOTAL == 1000
