"""RealtimeClient：WebSocket 連線池 + 事件分派。

行為概要（plan-realtime.md §6-§7）：
- `init_realtime()` 只需登入過的 FubonClient 即可
- 依需求開啟多條 stock WebSocket（最多 5 條；每條 200 訂閱）
- 把 SDK callback 轉成 Pydantic DTO 後，廣播給所有 `on_data` handler
- 斷線自動重連並還原訂閱（指數退避最多 60 秒）

此檔案不做 GUI 相關處理；Qt 端請把 handler 包成 `QMetaObject.invokeMethod`。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from loguru import logger

from stock_order_api.fubon.client import FubonClient
from stock_order_api.realtime.errors import (
    ChannelNotAllowedError,
    RealtimeConnectionError,
    RealtimeError,
)
from stock_order_api.realtime.models import (
    SPEED_MODE_FORBIDDEN,
    Channel,
    RealtimeMode,
    parse_data,
)
from stock_order_api.realtime.stats import StatsCollector
from stock_order_api.realtime.subscription import (
    ShardPlan,
    SubKey,
    SubscriptionManager,
)
from stock_order_api.utils.ringbuf import PerSymbolRingBuffer

DataHandler = Callable[[Channel, Any], None]
StatusHandler = Callable[[str, dict[str, Any]], None]


def _to_sdk_mode(mode: RealtimeMode) -> Any:
    from fubon_neo.sdk import Mode

    return Mode.Speed if mode == RealtimeMode.SPEED else Mode.Normal


class _Connection:
    """一條 WebSocket stock 連線的封裝。"""

    def __init__(self, idx: int, stock_client: Any, mode: RealtimeMode) -> None:
        self.idx = idx
        self.stock = stock_client
        self.mode = mode
        self.connected = False
        # key: (channel, symbol, odd) → sub_id；server 回 subscribed 事件後填入
        self.active_subs: dict[SubKey, str] = {}


class RealtimeClient:
    """即時行情單例封裝。"""

    def __init__(
        self,
        client: FubonClient,
        mode: RealtimeMode = RealtimeMode.SPEED,
        *,
        stock_factory: Callable[[], Any] | None = None,
        reconnect_base_sec: float = 2.0,
        reconnect_max_sec: float = 60.0,
        reconnect_max_attempts: int = 5,
        ring_buffer_size: int = 500,
        stats_interval_sec: float = 10.0,
        enable_stats: bool = True,
    ) -> None:
        self.client = client
        self.mode = mode
        self.manager = SubscriptionManager()

        self._sdk_marketdata: Any = None
        self._sdk_token: str | None = None
        self._stock_factory = stock_factory
        self._conns: list[_Connection] = []
        self._data_handlers: list[DataHandler] = []
        self._status_handlers: list[StatusHandler] = []
        self._lock = threading.RLock()

        # 重連相關
        self._reconnect_base_sec: float = reconnect_base_sec
        self._reconnect_max_sec: float = reconnect_max_sec
        self._reconnect_max_attempts: int = reconnect_max_attempts
        self._stopped = False

        # Stats + tick 緩衝
        self.stats = StatsCollector(interval_sec=stats_interval_sec)
        self.ticks: PerSymbolRingBuffer[Any] = PerSymbolRingBuffer(ring_buffer_size)
        self._enable_stats = enable_stats
        if enable_stats:
            self.stats.start()

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------
    def on_data(self, handler: DataHandler) -> None:
        """註冊資料 handler：`handler(channel, dto)`。"""
        with self._lock:
            self._data_handlers.append(handler)

    def on_status(self, handler: StatusHandler) -> None:
        """註冊狀態 handler：`handler(event_name, payload)`；含 connect/disconnect/error/subscribed 等。"""
        with self._lock:
            self._status_handlers.append(handler)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def init(self) -> None:
        """呼叫 SDK 的 init_realtime 取得 marketdata。只跑一次。"""
        if self._sdk_marketdata is not None:
            return
        sdk = self.client.sdk
        sdk_mode = _to_sdk_mode(self.mode)
        sdk.init_realtime(sdk_mode)
        self._sdk_marketdata = sdk.marketdata
        # 第二條以後的連線用 token 另開 websocket client
        try:
            self._sdk_token = sdk.exchange_realtime_token(sdk_mode)
        except Exception:  # pragma: no cover - 舊版 SDK 可能不支援
            self._sdk_token = None
        logger.bind(event="RT_INIT").info(
            f"realtime initialized mode={self.mode.value}"
        )

    def close(self) -> None:
        """關閉所有連線，釋放訂閱。"""
        self._stopped = True
        with self._lock:
            for conn in self._conns:
                try:
                    conn.stock.disconnect()
                except Exception as exc:  # pragma: no cover
                    logger.bind(event="RT_CLOSE").warning(
                        f"disconnect conn#{conn.idx} failed: {exc}"
                    )
            self._conns.clear()
        self.manager.release_all()
        if self._enable_stats:
            self.stats.stop()
        logger.bind(event="RT_CLOSE").info("all connections closed")

    # ------------------------------------------------------------------
    # subscribe / unsubscribe
    # ------------------------------------------------------------------
    def subscribe(
        self,
        channel: Channel,
        symbols: list[str],
        *,
        intraday_odd_lot: bool = False,
    ) -> list[SubKey]:
        """訂閱一批 symbols；回傳最終得以送出的 keys。"""
        if self.mode == RealtimeMode.SPEED and channel in SPEED_MODE_FORBIDDEN:
            raise ChannelNotAllowedError(
                f"Speed 模式不支援 channel={channel.value}；"
                "請改用 RealtimeMode.NORMAL"
            )

        self.init()
        plans = self.manager.allocate(
            channel, symbols, intraday_odd_lot=intraday_odd_lot
        )
        for plan in plans:
            self._ensure_connection(plan.conn_idx)
            self._send_subscribe(plan)

        return [
            SubKey(channel, s, intraday_odd_lot)
            for plan in plans
            for s in plan.symbols
        ]

    def unsubscribe(self, keys: list[SubKey]) -> None:
        """取消訂閱。"""
        released = self.manager.release(keys)
        for key in released:
            # 找 sub_id 送 unsubscribe
            for conn in self._conns:
                sub_id = conn.active_subs.pop(key, None)
                if sub_id is not None:
                    try:
                        conn.stock.unsubscribe({"id": sub_id})
                    except Exception as exc:  # pragma: no cover
                        logger.bind(event="RT_UNSUB").warning(
                            f"unsubscribe {key} failed: {exc}"
                        )
                    break

    def unsubscribe_all(self) -> None:
        self.unsubscribe([slot.key for slot in self.manager.all_slots()])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _ensure_connection(self, idx: int) -> _Connection:
        with self._lock:
            while len(self._conns) <= idx:
                self._open_new_connection()
            return self._conns[idx]

    def _create_stock_client(self, idx: int) -> Any:
        """建立第 idx 條 WebSocket stock 連線的 client。

        - 可由 `stock_factory` 注入（測試用）
        - 第一條直接用 `sdk.marketdata.websocket_client.stock`
        - 第二條以後透過 `build_websocket_client(token)` 另開 wrapper
        """
        if self._stock_factory is not None:
            return self._stock_factory()
        if idx == 0:
            return self._sdk_marketdata.websocket_client.stock
        if not self._sdk_token:
            raise RealtimeConnectionError(
                "無法建立第 2 條以上連線：SDK 未提供 realtime token"
            )
        from fubon_neo.sdk import build_websocket_client  # pragma: no cover

        wrapper = build_websocket_client(_to_sdk_mode(self.mode), self._sdk_token)
        return wrapper.stock

    def _open_new_connection(self) -> _Connection:
        assert self._sdk_marketdata is not None
        idx = len(self._conns)
        stock = self._create_stock_client(idx)
        conn = _Connection(idx=idx, stock_client=stock, mode=self.mode)

        stock.on("connect", lambda: self._on_connect(conn))
        stock.on(
            "disconnect",
            lambda code=None, message=None: self._on_disconnect(conn, code, message),
        )
        stock.on("error", lambda error: self._on_error(conn, error))
        stock.on("message", lambda msg: self._on_message(conn, msg))

        try:
            stock.connect()
        except Exception as exc:
            raise RealtimeConnectionError(f"WebSocket connect #{idx} 失敗：{exc}") from exc

        conn.connected = True
        self._conns.append(conn)
        logger.bind(event="RT_CONN").info(f"ws#{idx} connected")
        return conn

    def _send_subscribe(self, plan: ShardPlan) -> None:
        payload: dict[str, Any] = {
            "channel": plan.channel.value,
            "symbols": plan.symbols,
        }
        if plan.intraday_odd_lot:
            payload["intradayOddLot"] = True
        conn = self._conns[plan.conn_idx]
        try:
            conn.stock.subscribe(payload)
        except Exception as exc:
            # 失敗則回收配額
            for s in plan.symbols:
                self.manager.release(
                    [SubKey(plan.channel, s, plan.intraday_odd_lot)]
                )
            raise RealtimeError(f"subscribe 失敗：{exc}") from exc
        logger.bind(event="RT_SUB").info(
            f"ws#{plan.conn_idx} subscribe channel={plan.channel.value} "
            f"count={len(plan.symbols)}"
        )

    # ------------------------------------------------------------------
    # callbacks
    # ------------------------------------------------------------------
    def _on_connect(self, conn: _Connection) -> None:
        conn.connected = True
        self._emit_status("connect", {"conn": conn.idx})
        logger.bind(event="RT_CONN").info(f"ws#{conn.idx} on_connect")

    def _on_disconnect(
        self, conn: _Connection, code: Any = None, message: Any = None
    ) -> None:
        conn.connected = False
        self._emit_status(
            "disconnect", {"conn": conn.idx, "code": code, "message": message}
        )
        logger.bind(event="RT_DISCONN").warning(
            f"ws#{conn.idx} disconnected code={code} msg={message}"
        )
        if not self._stopped:
            threading.Thread(
                target=self._reconnect_loop, args=(conn,), daemon=True
            ).start()

    def _on_error(self, conn: _Connection, error: Any) -> None:
        self._emit_status("error", {"conn": conn.idx, "error": str(error)})
        logger.bind(event="RT_ERR").error(f"ws#{conn.idx} error: {error}")

    def _on_message(self, conn: _Connection, raw: Any) -> None:
        msg = raw
        if isinstance(raw, (bytes, bytearray)):
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                logger.bind(event="RT_MSG").debug(
                    f"ws#{conn.idx} unparsable bytes ({len(raw)} B)"
                )
                return
        if isinstance(raw, str):
            try:
                msg = json.loads(raw)
            except Exception:
                return

        if not isinstance(msg, dict):
            return

        event = str(msg.get("event", ""))
        data = msg.get("data")

        if event == "data":
            channel_s = str(msg.get("channel") or (data or {}).get("channel") or "")
            try:
                channel = Channel(channel_s)
            except ValueError:
                logger.bind(event="RT_MSG").debug(f"unknown channel: {channel_s}")
                return
            try:
                dto = parse_data(channel, data or {})
            except Exception as exc:
                logger.bind(event="RT_MSG").warning(
                    f"ws#{conn.idx} parse failed channel={channel_s}: {exc}"
                )
                return
            # stats + ring buffer
            self.stats.record(channel.value, getattr(dto, "time", None))
            symbol = getattr(dto, "symbol", None)
            if isinstance(symbol, str) and symbol:
                self.ticks.append(channel.value, symbol, dto)
            for h in list(self._data_handlers):
                try:
                    h(channel, dto)
                except Exception as exc:  # pragma: no cover
                    logger.bind(event="RT_HANDLER").exception(
                        f"data handler raised: {exc}"
                    )
            return

        # 控制事件：authenticated / subscribed / unsubscribed / error / heartbeat / pong
        self._emit_status(event or "unknown", msg if isinstance(msg, dict) else {})

        if event == "subscribed":
            self._record_subscribed(conn, data)

    def _record_subscribed(self, conn: _Connection, data: Any) -> None:
        items = data if isinstance(data, list) else [data] if data else []
        for item in items:
            if not isinstance(item, dict):
                continue
            sub_id = str(item.get("id") or "")
            channel_s = str(item.get("channel") or "")
            symbol = str(item.get("symbol") or "")
            if not sub_id or not channel_s or not symbol:
                continue
            try:
                channel = Channel(channel_s)
            except ValueError:
                continue
            # 找對應 key（odd_lot 不在 server 回應，用 False/True 都試）
            for odd in (False, True):
                key = SubKey(channel, symbol, odd)
                if self.manager.get_slot(key) is not None:
                    self.manager.bind_sub_id(key, sub_id)
                    conn.active_subs[key] = sub_id
                    break

    def _emit_status(self, event: str, payload: dict[str, Any]) -> None:
        for h in list(self._status_handlers):
            try:
                h(event, payload)
            except Exception as exc:  # pragma: no cover
                logger.bind(event="RT_HANDLER").exception(
                    f"status handler raised: {exc}"
                )

    # ------------------------------------------------------------------
    # reconnect
    # ------------------------------------------------------------------
    def _reconnect_loop(self, conn: _Connection) -> None:
        delay = self._reconnect_base_sec
        for attempt in range(1, self._reconnect_max_attempts + 1):
            if self._stopped:
                return
            time.sleep(delay)
            try:
                logger.bind(event="RT_RECONN").info(
                    f"ws#{conn.idx} reconnect attempt {attempt}/"
                    f"{self._reconnect_max_attempts}"
                )
                conn.stock.connect()
                conn.connected = True
                self._resubscribe(conn)
                return
            except Exception as exc:
                logger.bind(event="RT_RECONN").warning(
                    f"ws#{conn.idx} attempt {attempt} failed: {exc}"
                )
                delay = min(delay * 2, self._reconnect_max_sec)
        logger.bind(event="RT_RECONN").error(
            f"ws#{conn.idx} reconnect giving up after "
            f"{self._reconnect_max_attempts} attempts"
        )
        self._emit_status("connection_failed", {"conn": conn.idx})

    def _resubscribe(self, conn: _Connection) -> None:
        """重連後還原該 conn 已有的訂閱。"""
        by_channel: dict[tuple[Channel, bool], list[str]] = {}
        for slot in self.manager.all_slots():
            if slot.conn_idx != conn.idx:
                continue
            key = (slot.key.channel, slot.key.intraday_odd_lot)
            by_channel.setdefault(key, []).append(slot.key.symbol)
        for (channel, odd), symbols in by_channel.items():
            payload: dict[str, Any] = {
                "channel": channel.value,
                "symbols": symbols,
            }
            if odd:
                payload["intradayOddLot"] = True
            try:
                conn.stock.subscribe(payload)
            except Exception as exc:  # pragma: no cover
                logger.bind(event="RT_RESUB").warning(
                    f"ws#{conn.idx} resubscribe {channel.value} failed: {exc}"
                )

    # ------------------------------------------------------------------
    # stats
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "connections": [
                {"idx": c.idx, "connected": c.connected}
                for c in self._conns
            ],
            "subscriptions": self.manager.total_subscriptions,
            "usage": self.manager.usage_snapshot(),
            "stats": self.stats.as_dict(self.stats.flush()),
        }
