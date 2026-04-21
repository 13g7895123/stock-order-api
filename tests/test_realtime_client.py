"""測試 RealtimeClient 的訂閱分派與訊息處理（不需真實 SDK）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.errors import ChannelNotAllowedError
from stock_order_api.realtime.models import Channel, RealtimeMode, Trade

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeStock:
    on_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    subscribe_calls: list[dict[str, Any]] = field(default_factory=list)
    unsubscribe_calls: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = False

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.on_handlers[event] = handler

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, payload: dict[str, Any]) -> None:
        self.subscribe_calls.append(payload)

    def unsubscribe(self, payload: dict[str, Any]) -> None:
        self.unsubscribe_calls.append(payload)


class _FakeWSWrapper:
    """回傳預先建立好的 fake stock list（每次 .stock 給下一個）。"""

    def __init__(self, stocks: list[_FakeStock]) -> None:
        self._iter = iter(stocks)
        self._first: _FakeStock | None = None

    @property
    def stock(self) -> _FakeStock:
        if self._first is None:
            self._first = next(self._iter)
        return self._first


@dataclass
class _FakeMarketData:
    websocket_client: Any = None


class _FakeSDK:
    def __init__(self, ws: _FakeWSWrapper) -> None:
        self.marketdata = _FakeMarketData(websocket_client=ws)
        self.init_calls: list[Any] = []

    def init_realtime(self, mode: Any) -> None:
        self.init_calls.append(mode)

    def exchange_realtime_token(self, mode: Any) -> str:
        return "fake-token"


class _FakeClient:
    def __init__(self, stocks: list[_FakeStock]) -> None:
        self._sdk = _FakeSDK(_FakeWSWrapper(stocks))

    @property
    def sdk(self) -> _FakeSDK:
        return self._sdk


def _make_rt(
    mode: RealtimeMode = RealtimeMode.NORMAL, n_stocks: int = 5
) -> tuple[RealtimeClient, list[_FakeStock]]:
    stocks = [_FakeStock() for _ in range(n_stocks)]
    it = iter(stocks)
    client = _FakeClient(stocks)
    rt = RealtimeClient(
        client,  # type: ignore[arg-type]
        mode=mode,
        stock_factory=lambda: next(it),
        enable_stats=False,
    )
    return rt, stocks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_subscribe_opens_one_connection() -> None:
    rt, _ = _make_rt()
    rt.subscribe(Channel.TRADES, ["2330", "2317"])
    assert rt.manager.total_subscriptions == 2
    assert len(rt._conns) == 1
    payload = rt._conns[0].stock.subscribe_calls[0]
    assert payload["channel"] == "trades"
    assert payload["symbols"] == ["2330", "2317"]


def test_subscribe_splits_over_200() -> None:
    rt, _ = _make_rt()
    syms = [f"S{i:04d}" for i in range(201)]
    rt.subscribe(Channel.TRADES, syms)
    assert len(rt._conns) == 2
    assert len(rt._conns[0].stock.subscribe_calls[0]["symbols"]) == 200
    assert len(rt._conns[1].stock.subscribe_calls[0]["symbols"]) == 1


def test_speed_mode_forbids_candles() -> None:
    rt, _ = _make_rt(mode=RealtimeMode.SPEED)
    with pytest.raises(ChannelNotAllowedError):
        rt.subscribe(Channel.CANDLES, ["2330"])


def test_on_data_handler_gets_trade_dto() -> None:
    rt, _ = _make_rt()
    received: list[tuple[Channel, Any]] = []
    rt.on_data(lambda ch, dto: received.append((ch, dto)))
    rt.subscribe(Channel.TRADES, ["2330"])

    message_handler = rt._conns[0].stock.on_handlers["message"]
    message_handler(
        {
            "event": "data",
            "channel": "trades",
            "data": {
                "symbol": "2330",
                "price": "620.5",
                "size": 1000,
                "time": "2026-04-21T13:30:00+08:00",
            },
        }
    )
    assert len(received) == 1
    ch, dto = received[0]
    assert ch == Channel.TRADES
    assert isinstance(dto, Trade)
    assert dto.symbol == "2330"


def test_on_status_receives_subscribed_event() -> None:
    rt, _ = _make_rt()
    events: list[str] = []
    rt.on_status(lambda ev, _p: events.append(ev))
    rt.subscribe(Channel.TRADES, ["2330"])

    message_handler = rt._conns[0].stock.on_handlers["message"]
    message_handler(
        {
            "event": "subscribed",
            "data": {"id": "sub-1", "channel": "trades", "symbol": "2330"},
        }
    )
    assert "subscribed" in events


def test_unsubscribe_sends_sub_id() -> None:
    rt, _ = _make_rt()
    keys = rt.subscribe(Channel.TRADES, ["2330"])

    message_handler = rt._conns[0].stock.on_handlers["message"]
    message_handler(
        {
            "event": "subscribed",
            "data": {"id": "abc-123", "channel": "trades", "symbol": "2330"},
        }
    )
    rt.unsubscribe(keys)
    assert rt._conns[0].stock.unsubscribe_calls == [{"id": "abc-123"}]
    assert rt.manager.total_subscriptions == 0


def test_status_snapshot() -> None:
    rt, _ = _make_rt(mode=RealtimeMode.SPEED)
    rt.subscribe(Channel.TRADES, ["2330", "2317"])
    s = rt.status()
    assert s["mode"] == "speed"
    assert s["subscriptions"] == 2
    assert s["connections"] == [{"idx": 0, "connected": True}]
    assert s["usage"] == [2]


def test_unknown_channel_in_message_is_ignored() -> None:
    rt, _ = _make_rt()
    received: list[Any] = []
    rt.on_data(lambda ch, dto: received.append((ch, dto)))
    rt.subscribe(Channel.TRADES, ["2330"])

    message_handler = rt._conns[0].stock.on_handlers["message"]
    message_handler({"event": "data", "channel": "bogus", "data": {}})
    assert received == []
