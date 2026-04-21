"""測試 realtime.models 的 DTO 轉換。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stock_order_api.realtime.models import (
    SPEED_MODE_FORBIDDEN,
    Book,
    Candle,
    Channel,
    Index,
    RealtimeMode,
    Trade,
    parse_data,
)


def test_trade_from_payload_basic() -> None:
    t = Trade.from_payload(
        {
            "symbol": "2330",
            "price": "620.5",
            "size": "1000",
            "time": "2026-04-21T13:30:00+08:00",
            "bidAskType": "BID_SIDE",
            "totalVolume": "15234000",
        }
    )
    assert t.symbol == "2330"
    assert t.price == Decimal("620.5")
    assert t.size == 1000
    assert t.bid_ask == "bid"
    assert t.total_volume == 15234000
    assert t.time.tzinfo is not None


def test_trade_time_ms_epoch() -> None:
    t = Trade.from_payload(
        {"symbol": "2330", "price": "100", "size": 1, "time": 1745212200000}
    )
    assert t.time == datetime.fromtimestamp(1745212200, tz=UTC)


def test_trade_time_ns_epoch() -> None:
    t = Trade.from_payload(
        {"symbol": "2330", "price": "100", "size": 1, "time": 1745212200000_000_000}
    )
    assert t.time.year == 2025


def test_book_from_payload() -> None:
    b = Book.from_payload(
        {
            "symbol": "2330",
            "time": "2026-04-21T13:30:00+08:00",
            "bids": [
                {"price": "620", "size": 5},
                {"price": "619.5", "size": 10},
            ],
            "asks": [
                {"price": "620.5", "size": 3},
            ],
        }
    )
    assert b.symbol == "2330"
    assert len(b.bids) == 2
    assert b.bids[0].price == Decimal("620")
    assert len(b.asks) == 1


def test_candle_from_payload() -> None:
    c = Candle.from_payload(
        {
            "symbol": "2330",
            "time": "2026-04-21T13:30:00+08:00",
            "open": "620",
            "high": "625",
            "low": "619",
            "close": "623",
            "volume": "100",
        }
    )
    assert c.open == Decimal("620")
    assert c.volume == 100


def test_index_from_payload() -> None:
    idx = Index.from_payload(
        {
            "symbol": "IX0001",
            "time": "2026-04-21T13:30:00+08:00",
            "value": "18000.5",
            "change": "50.5",
            "changePercent": "0.28",
        }
    )
    assert idx.price == Decimal("18000.5")
    assert idx.change == Decimal("50.5")


def test_parse_data_dispatches_to_correct_model() -> None:
    payload = {
        "symbol": "2330",
        "price": "620",
        "size": 1,
        "time": "2026-04-21T13:30:00+08:00",
    }
    dto = parse_data(Channel.TRADES, payload)
    assert isinstance(dto, Trade)


def test_speed_mode_forbidden_channels() -> None:
    assert Channel.AGGREGATES in SPEED_MODE_FORBIDDEN
    assert Channel.CANDLES in SPEED_MODE_FORBIDDEN
    assert Channel.TRADES not in SPEED_MODE_FORBIDDEN


def test_invalid_price_raises() -> None:
    with pytest.raises(ValueError):
        Trade.from_payload({"symbol": "2330", "price": "abc", "size": 1, "time": 0})


def test_mode_enum_values() -> None:
    assert RealtimeMode.SPEED.value == "speed"
    assert RealtimeMode.NORMAL.value == "normal"
