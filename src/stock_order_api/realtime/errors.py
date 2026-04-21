"""即時行情模組例外類別。"""

from __future__ import annotations


class RealtimeError(Exception):
    """行情服務基底例外。"""


class RealtimeConnectionError(RealtimeError):
    """WebSocket 連線失敗。"""


class SubscriptionLimitError(RealtimeError):
    """訂閱數超過富邦限制（單連線 200、最多 5 連線 → 1000 pair）。"""


class ChannelNotAllowedError(RealtimeError):
    """該 channel 在目前 Mode 不支援（例如 Speed 模式不支援 candles/aggregates）。"""


class SubscribeRejectedError(RealtimeError):
    """Server 拒絕單筆訂閱（例如標的不存在、無權限）。"""
