"""Realtime WebSocket router：即時行情推播。

Protocol
--------
Client → Server（JSON 訊息）:
    {"action": "subscribe",   "symbols": ["2330", "2317"], "channels": ["trades", "books"]}
    {"action": "unsubscribe", "symbols": ["2330"],          "channels": ["trades"]}
    {"action": "unsubscribe_all"}
    {"action": "close"}

Server → Client（JSON 訊息）:
    {"type": "data",   "channel": "trades", "data": {...}}
    {"type": "status", "event": "connected", "payload": {...}}
    {"type": "error",  "message": "..."}

每個 WebSocket 連線擁有獨立的 RealtimeClient 實例。
SDK callback 來自背景執行緒，透過 asyncio Queue 橋接回 async 迴圈。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from stock_order_api.api.deps import get_client
from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.errors import RealtimeError
from stock_order_api.realtime.models import Channel, RealtimeMode

router = APIRouter(tags=["realtime"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dto_to_dict(dto: Any) -> Any:
    if hasattr(dto, "model_dump"):
        return dto.model_dump(mode="json")
    return str(dto)


def _build_rt(client: FubonClient, mode_str: str) -> RealtimeClient:
    s = get_settings()
    mode = RealtimeMode(mode_str) if mode_str in RealtimeMode._value2member_map_ else RealtimeMode.SPEED
    return RealtimeClient(
        client=client,
        mode=mode,
        reconnect_base_sec=s.realtime_reconnect_base_sec,
        reconnect_max_sec=s.realtime_reconnect_max_sec,
        reconnect_max_attempts=s.realtime_reconnect_max,
        ring_buffer_size=s.realtime_ring_buffer,
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/quotes")
async def ws_quotes(websocket: WebSocket) -> None:
    """即時行情 WebSocket 端點。"""
    await websocket.accept()

    client: FubonClient = get_client()
    if not client.is_logged_in:
        await websocket.send_json({"type": "error", "message": "尚未登入，請先呼叫 POST /auth/login"})
        await websocket.close(code=4001)
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)

    rt: RealtimeClient | None = None
    active_sub_keys: list[Any] = []

    def on_data(channel: Channel, dto: Any) -> None:
        msg = {"type": "data", "channel": channel.value, "data": _dto_to_dict(dto)}
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    def on_status(event: str, payload: dict[str, Any]) -> None:
        msg = {"type": "status", "event": event, "payload": payload}
        loop.call_soon_threadsafe(queue.put_nowait, msg)

    async def send_loop() -> None:
        while True:
            msg = await queue.get()
            try:
                await websocket.send_json(msg)
            except Exception:
                break

    send_task = asyncio.create_task(send_loop())

    try:
        while True:
            raw = await websocket.receive_json()
            action = raw.get("action", "")

            if action == "subscribe":
                symbols: list[str] = raw.get("symbols", [])
                channels_raw: list[str] = raw.get("channels", ["trades", "books"])
                mode_str: str = raw.get("mode", "speed")

                # 若模式改變，重建 RT client
                current_mode = rt.mode.value if rt is not None else None
                if rt is None or current_mode != mode_str:
                    if rt is not None:
                        try:
                            rt.close()
                        except Exception as exc:
                            logger.warning(f"RT close: {exc}")
                        active_sub_keys.clear()
                    rt = _build_rt(client, mode_str)
                    rt.on_data(on_data)
                    rt.on_status(on_status)

                channels = []
                for c in channels_raw:
                    try:
                        channels.append(Channel(c))
                    except ValueError:
                        await websocket.send_json({"type": "error", "message": f"不支援的 channel: {c}"})

                for ch in channels:
                    try:
                        keys = rt.subscribe(ch, symbols)
                        active_sub_keys.extend(keys)
                    except RealtimeError as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "unsubscribe":
                symbols = raw.get("symbols", [])
                channels_raw = raw.get("channels", ["trades", "books"])
                if rt is not None:
                    channels = [Channel(c) for c in channels_raw if c in Channel._value2member_map_]
                    keys_to_remove = [
                        k for k in active_sub_keys
                        if k.symbol in symbols and k.channel in channels
                    ]
                    if keys_to_remove:
                        rt.unsubscribe(keys_to_remove)
                        for k in keys_to_remove:
                            active_sub_keys.remove(k)

            elif action == "unsubscribe_all":
                if rt is not None and active_sub_keys:
                    rt.unsubscribe(list(active_sub_keys))
                    active_sub_keys.clear()

            elif action == "close":
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.bind(event="WS_QUOTES").exception(f"ws error: {exc}")
    finally:
        send_task.cancel()
        if rt is not None:
            try:
                rt.close()
            except Exception as exc:
                logger.warning(f"RT cleanup: {exc}")
