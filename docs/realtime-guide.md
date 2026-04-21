# 即時行情（WebSocket）使用指南

> 母計畫：[../plan-realtime.md](../plan-realtime.md) · 相關：[fubon-api-overview.md](fubon-api-overview.md)

---

## 1. 架構總覽

```
┌─────────────────────────────────────────────────────────────────┐
│ stock_order_api.realtime                                        │
│                                                                 │
│  ┌────────────────┐   ┌──────────────────┐   ┌──────────────┐   │
│  │ RealtimeClient │──▶│ _Connection (×N) │──▶│ Fugle WS     │   │
│  │  ─ on_data     │   │  ─ stock.on()    │   │ StockClient  │   │
│  │  ─ on_status   │   │  ─ reconnect     │   └──────────────┘   │
│  │  ─ stats       │   └──────────────────┘                      │
│  │  ─ ticks(rb)   │         ▲                                   │
│  └───────┬────────┘         │                                   │
│          │ parse_data       │ SubscriptionManager               │
│          ▼                  │  ─ 200/conn · 5 conns · 1000 max  │
│   ┌─────────────┐           │                                   │
│   │ DTO (trade/ │           │                                   │
│   │  book/...)  │───────────┘                                   │
│   └─────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Channel × Mode 對照

| Channel | 說明 | Speed | Normal |
| --- | --- | :-: | :-: |
| `trades` | 逐筆成交 | ✅ | ✅ |
| `books` | 五檔 | ✅ | ✅ |
| `indices` | 指數 | ✅ | ✅ |
| `aggregates` | 聚合 | ❌ | ✅ |
| `candles` | K 線 | ❌ | ✅ |

Speed 模式訂閱 `aggregates` / `candles` 會 raise `ChannelNotAllowedError`。

---

## 3. 訂閱限制

來源：<https://www.fbs.com.tw/TradeAPI/docs/market-data/rate-limit>

| 項目 | 上限 |
| --- | --- |
| 單一 WebSocket 連線 | 200 訂閱 |
| 同帳號最大連線數 | 5 條 |
| 理論天花板 | 1000 個 `(channel, symbol)` pair |

`SubscriptionManager` 自動分片；滿了才會開下一條連線。

---

## 4. Python API 範例

```python
from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.models import Channel, RealtimeMode

s = get_settings()
client = FubonClient.instance(s)
client.login()

rt = RealtimeClient(
    client=client,
    mode=RealtimeMode.SPEED,
    reconnect_max_attempts=s.realtime_reconnect_max,
    ring_buffer_size=s.realtime_ring_buffer,
    stats_interval_sec=s.realtime_stats_interval,
)

def on_data(channel: Channel, dto) -> None:
    print(channel.value, dto.model_dump(mode="json"))

rt.on_data(on_data)
rt.subscribe(Channel.TRADES, ["2330", "2317"])
rt.subscribe(Channel.BOOKS, ["2330"])

# … 主程式做別的事 …

rt.unsubscribe_all()
rt.close()
```

---

## 5. CLI（`stock-order-quote`）

| 子命令 | 用途 |
| --- | --- |
| `watch <channels> <symbols...>` | 持續訂閱；`--output table/jsonl/csv`；`--duration 秒` |
| `snapshot <symbol>` | 取到第一筆資料即結束 |
| `status <symbols>` | Debug：dump 連線與分片狀態 |

通用參數：`--mode speed|normal` · `--odd-lot`（盤中零股）

---

## 6. 設定（`.env`）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `FUBON_REALTIME_MODE` | `speed` | `speed` / `normal` |
| `FUBON_REALTIME_RECONNECT_MAX` | 5 | 連續重連失敗上限 |
| `FUBON_REALTIME_RECONNECT_BASE_SEC` | 2 | 指數退避起始秒 |
| `FUBON_REALTIME_RECONNECT_MAX_SEC` | 60 | 退避秒數上限 |
| `FUBON_REALTIME_RING_BUFFER` | 500 | 每檔保留 tick 數 |
| `FUBON_REALTIME_STATS_INTERVAL` | 10 | STATS log 秒數 |

---

## 7. 錯誤對照

| 例外 | 觸發時機 | 處理方式 |
| --- | --- | --- |
| `ChannelNotAllowedError` | Speed 模式訂 `aggregates/candles` | 改 `RealtimeMode.NORMAL` |
| `SubscriptionLimitError` | 訂閱總數 > 1000 或超過單連線 200 | 減少商品或拆 process |
| `RealtimeConnectionError` | `connect()` 抛錯 | 檢查網路 / token |
| `SubscribeRejectedError` | Server `error` 事件（1001 等） | 看 log 訊息 |
| `RealtimeError` | 其他（含 subscribe 本身 raise） | 看 traceback |

---

## 8. 日誌

- `logs/app.log` / `logs/app.jsonl`：含 `RT_INIT` / `RT_CONN` / `RT_SUB` / `RT_UNSUB` / `RT_DISCONN` / `RT_RECONN` / `RT_ERR`
- `STATS` event：每 `FUBON_REALTIME_STATS_INTERVAL` 秒一筆
  ```
  STATS channel=trades count=1234 msg_per_sec=123.40 p50_ms=25.3 p95_ms=98.1
  ```

---

## 9. 常見問題

**Q1. 為何第二條 WebSocket 連線要 `exchange_realtime_token`？**
`sdk.marketdata.websocket_client.stock` 只提供一個 client；要再開得透過 `build_websocket_client(mode, token)` 拿新 wrapper。`RealtimeClient.init()` 會自動做這件事。

**Q2. 為什麼 `time` 欄位有時是秒、有時是毫秒？**
富邦/Fugle 的 payload 會用 s / ms / µs / ns epoch 混用；`models._to_datetime()` 以量級判斷自動轉 `datetime`（UTC）。

**Q3. 重連後會自動還原訂閱嗎？**
會。`_reconnect_loop` 成功後會 call `_resubscribe(conn)`，以 `SubscriptionManager` 的記錄把該條連線原本的 `(channel, symbols)` 重送。

**Q4. GUI 顯示會不會被 SDK callback 卡住？**
不會。`QuotePage` 用 `_RTBridge` (QObject) 把資料以 `QueuedConnection` 送回主執行緒再更新 QTableWidget。
