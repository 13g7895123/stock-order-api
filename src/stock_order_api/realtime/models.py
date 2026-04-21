"""即時行情 Pydantic DTO。

欄位參考富邦官方 WebSocket payload：
- trades: <https://www.fbs.com.tw/TradeAPI/docs/market-data/websocket-api/market-data-channels/trades>
- books: 最佳五檔
- aggregates / candles / indices

設計原則：
- `extra="ignore"`：對未知欄位容錯
- 時間皆轉為 `datetime`（若 SDK 給 ms epoch 或 ISO 字串皆支援）
- 價格一律 `Decimal`，量一律 `int`
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class Channel(StrEnum):
    """富邦 WebSocket 可訂閱頻道。"""

    TRADES = "trades"
    BOOKS = "books"
    AGGREGATES = "aggregates"
    CANDLES = "candles"
    INDICES = "indices"


class RealtimeMode(StrEnum):
    """行情 Mode。"""

    SPEED = "speed"
    NORMAL = "normal"


#: Speed 模式不支援的 channel（plan §2）
SPEED_MODE_FORBIDDEN: frozenset[Channel] = frozenset(
    {Channel.AGGREGATES, Channel.CANDLES}
)


# ---------------------------------------------------------------------------
# Helper coercers
# ---------------------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError):
            return None


def _to_datetime(value: Any) -> datetime | None:
    """支援 ns/ms epoch、ISO 字串與 datetime 物件。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        v = int(value)
        # 依位數推斷單位：秒 / 毫秒 / 微秒 / 奈秒
        if v > 10**17:  # ns
            return datetime.fromtimestamp(v / 1_000_000_000, tz=UTC)
        if v > 10**14:  # us
            return datetime.fromtimestamp(v / 1_000_000, tz=UTC)
        if v > 10**11:  # ms
            return datetime.fromtimestamp(v / 1000, tz=UTC)
        return datetime.fromtimestamp(v, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _RealtimeDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------


class Trade(_RealtimeDTO):
    """最新成交。"""

    symbol: str
    price: Decimal
    size: int = Field(description="本筆成交量（股）")
    time: datetime
    bid_ask: Literal["bid", "ask", "even"] | None = None
    total_volume: int | None = Field(default=None, description="當日累計成交量")
    is_trial: bool = Field(default=False, description="試算單")

    @field_validator("price", mode="before")
    @classmethod
    def _coerce_price(cls, v: Any) -> Any:
        d = _to_decimal(v)
        if d is None:
            raise ValueError("invalid price")
        return d

    @field_validator("size", "total_volume", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        return _to_int(v) if v is not None else v

    @field_validator("time", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        t = _to_datetime(v)
        if t is None:
            raise ValueError("invalid time")
        return t

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Trade:
        """從 SDK 送來的原始 dict 轉 DTO。"""
        return cls(
            symbol=str(payload.get("symbol") or payload.get("stock_no") or ""),
            price=payload.get("price"),  # type: ignore[arg-type]
            size=payload.get("size") or payload.get("volume") or 0,
            time=payload.get("time") or payload.get("timestamp"),  # type: ignore[arg-type]
            bid_ask=_coerce_bid_ask(payload.get("bidAskType") or payload.get("bid_ask")),
            total_volume=payload.get("totalVolume") or payload.get("total_volume"),
            is_trial=bool(payload.get("isTrial") or payload.get("is_trial") or False),
        )


def _coerce_bid_ask(v: Any) -> Literal["bid", "ask", "even"] | None:
    if v is None:
        return None
    s = str(v).lower()
    if s in ("bid", "ask", "even"):
        return s  # type: ignore[return-value]
    # 富邦可能給 'BID_SIDE' / 'ASK_SIDE'
    if "bid" in s:
        return "bid"
    if "ask" in s:
        return "ask"
    if "even" in s or "mid" in s:
        return "even"
    return None


# ---------------------------------------------------------------------------
# Book (五檔)
# ---------------------------------------------------------------------------


class BookLevel(_RealtimeDTO):
    price: Decimal
    size: int

    @field_validator("price", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        d = _to_decimal(v)
        if d is None:
            raise ValueError("invalid price")
        return d

    @field_validator("size", mode="before")
    @classmethod
    def _s(cls, v: Any) -> Any:
        i = _to_int(v)
        return 0 if i is None else i


class Book(_RealtimeDTO):
    symbol: str
    time: datetime
    bids: list[BookLevel] = Field(default_factory=list)
    asks: list[BookLevel] = Field(default_factory=list)

    @field_validator("time", mode="before")
    @classmethod
    def _t(cls, v: Any) -> Any:
        t = _to_datetime(v)
        if t is None:
            raise ValueError("invalid time")
        return t

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Book:
        raw_bids = payload.get("bids") or []
        raw_asks = payload.get("asks") or []
        return cls(
            symbol=str(payload.get("symbol") or ""),
            time=payload.get("time") or payload.get("timestamp"),  # type: ignore[arg-type]
            bids=[BookLevel.model_validate(b) for b in raw_bids],
            asks=[BookLevel.model_validate(a) for a in raw_asks],
        )


# ---------------------------------------------------------------------------
# Candle / Aggregate
# ---------------------------------------------------------------------------


class Candle(_RealtimeDTO):
    symbol: str
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        d = _to_decimal(v)
        if d is None:
            raise ValueError("invalid price")
        return d

    @field_validator("volume", mode="before")
    @classmethod
    def _v(cls, v: Any) -> Any:
        i = _to_int(v)
        return 0 if i is None else i

    @field_validator("time", mode="before")
    @classmethod
    def _t(cls, v: Any) -> Any:
        t = _to_datetime(v)
        if t is None:
            raise ValueError("invalid time")
        return t

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Candle:
        return cls(
            symbol=str(payload.get("symbol") or ""),
            time=payload.get("time") or payload.get("timestamp"),  # type: ignore[arg-type]
            open=payload.get("open"),  # type: ignore[arg-type]
            high=payload.get("high"),  # type: ignore[arg-type]
            low=payload.get("low"),  # type: ignore[arg-type]
            close=payload.get("close"),  # type: ignore[arg-type]
            volume=payload.get("volume") or 0,
        )


class Aggregate(Candle):
    """分鐘聚合（與 Candle 同欄位）。"""


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class Index(_RealtimeDTO):
    symbol: str
    time: datetime
    price: Decimal
    change: Decimal | None = None
    change_percent: Decimal | None = None

    @field_validator("price", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        d = _to_decimal(v)
        if d is None:
            raise ValueError("invalid price")
        return d

    @field_validator("change", "change_percent", mode="before")
    @classmethod
    def _opt(cls, v: Any) -> Any:
        return _to_decimal(v)

    @field_validator("time", mode="before")
    @classmethod
    def _t(cls, v: Any) -> Any:
        t = _to_datetime(v)
        if t is None:
            raise ValueError("invalid time")
        return t

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Index:
        return cls(
            symbol=str(payload.get("symbol") or ""),
            time=payload.get("time") or payload.get("timestamp"),  # type: ignore[arg-type]
            price=payload.get("price") or payload.get("value"),  # type: ignore[arg-type]
            change=payload.get("change"),
            change_percent=payload.get("changePercent") or payload.get("change_percent"),
        )


# ---------------------------------------------------------------------------
# Channel → DTO 對照
# ---------------------------------------------------------------------------


CHANNEL_TO_MODEL: dict[Channel, type[_RealtimeDTO]] = {
    Channel.TRADES: Trade,
    Channel.BOOKS: Book,
    Channel.AGGREGATES: Aggregate,
    Channel.CANDLES: Candle,
    Channel.INDICES: Index,
}


def parse_data(channel: Channel, payload: dict[str, Any]) -> _RealtimeDTO:
    """依 channel 轉成對應 DTO。"""
    model = CHANNEL_TO_MODEL[channel]
    from_payload = getattr(model, "from_payload", None)
    if callable(from_payload):
        return from_payload(payload)  # type: ignore[no-any-return]
    return model.model_validate(payload)
