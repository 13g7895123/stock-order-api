"""即時行情模組。

依 [plan-realtime.md](../../../plan-realtime.md) 實作：
- models: Pydantic DTO
- errors: 例外類別
- subscription: 訂閱分片管理器
- client: RealtimeClient 單例 + 連線池
- dispatcher: callback → DTO → 多訂閱者廣播
"""

from __future__ import annotations

from stock_order_api.realtime.errors import (
    ChannelNotAllowedError,
    RealtimeConnectionError,
    RealtimeError,
    SubscribeRejectedError,
    SubscriptionLimitError,
)
from stock_order_api.realtime.models import (
    Aggregate,
    Book,
    BookLevel,
    Candle,
    Channel,
    Index,
    RealtimeMode,
    Trade,
)

__all__ = [
    "Aggregate",
    "Book",
    "BookLevel",
    "Candle",
    "Channel",
    "ChannelNotAllowedError",
    "Index",
    "RealtimeConnectionError",
    "RealtimeError",
    "RealtimeMode",
    "SubscribeRejectedError",
    "SubscriptionLimitError",
    "Trade",
]
