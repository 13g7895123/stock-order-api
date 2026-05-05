---
name: stock-order-api
description: >-
  **領域技能** — 富邦新一代 API（Fubon Neo）股票下單 / 帳務 / 即時行情整合。
  使用時機：實作或修改 `src/stock_order_api/` 內任何模組時；串接 Fubon Neo SDK；
  設計 REST / WebSocket API endpoint；處理憑證登入；訂閱 WebSocket 行情；
  儀表板資料來源對接；審計日誌設計；打板策略系統整合。
  不適用：與富邦 API 無關的純演算法問題、前端框架、資料庫 ORM 遷移。
applyTo:
  - "src/stock_order_api/**"
  - "tests/**"
  - "docs/**"
---

# Stock Order API — 領域技能

> 專案路徑：`40_stock-order-api`  
> 語言：Python 3.11 · 套件管理：uv · SDK：Fubon Neo v2.2.8

---

## 1. 專案架構速覽

```
stock_order_api/
├── config.py              # pydantic-settings，env prefix=FUBON_
├── logging_setup.py       # Loguru 多 sink（app / jsonl / trade / error）
├── fubon/
│   ├── client.py          # FubonClient 單例（SDK 生命週期 + 帳號管理）
│   ├── cert.py            # .pfx 憑證檢查（openssl / cryptography）
│   ├── stock_order.py     # 下單 / 改價 / 改量 / 刪單 / 查詢
│   ├── stock_account.py   # 庫存 / 損益 / 現金 / 維持率
│   ├── errors.py          # FubonError 層次體系
│   └── symbol_names.py    # 股票代號 → 名稱對照
├── realtime/
│   ├── client.py          # RealtimeClient（WebSocket 生命週期 + 訂閱管理）
│   ├── models.py          # Channel enum / DTO（TradeData / BookData / ...）
│   ├── subscription.py    # SubscriptionManager（分片 200/conn × 5 conn）
│   ├── stats.py           # 連線統計
│   └── errors.py          # RealtimeError
├── api/
│   ├── app.py             # FastAPI create_app() + lifespan
│   ├── deps.py            # Depends 注入（FubonClient / StockOrderSvc / ...）
│   └── routers/
│       ├── auth.py        # POST /auth/login · GET /auth/status · accounts
│       ├── account.py     # GET /account/inventories|unrealized|realized|cash|maintenance
│       ├── orders.py      # GET/POST /orders · DELETE/PATCH /orders/{no}
│       └── realtime.py    # WS /ws/quotes
├── audit/store.py         # SQLite 稽核（audit_events / orders / fills）
├── gui/                   # PySide6 GUI（非 API 模式）
└── utils/
    ├── cache.py           # TTL 快取
    ├── csv_export.py      # 匯出
    └── ringbuf.py         # 環形緩衝區（行情 tick 儲存）
```

---

## 2. 富邦 Neo SDK 前置條件（缺一不可）

### 2.1 必要憑證清單

| 環境變數 | 必填 | 說明 |
|---|---|---|
| `FUBON_PERSONAL_ID` | ✅ | 身分證字號（SDK login 第一參數） |
| `FUBON_PASSWORD` | ✅ | 電子交易密碼 |
| `FUBON_CERT_PATH` | ✅ | `.pfx` 憑證絕對路徑（預設 `secrets/<身分證>.pfx`） |
| `FUBON_CERT_PASSWORD` | ✅ | 憑證密碼（v1.3.2+ 可省略；預設 = 身分證字號） |
| `FUBON_BRANCH_NO` | ✅ | 分公司代號 4 碼（如 `6460`） |
| `FUBON_ACCOUNT_NO` | ✅ | 證券帳號 7 碼（如 `1234567`） |
| `FUBON_API_KEY` | ⚪ | 金鑰管理後台取得（建議生產環境使用） |
| `FUBON_API_SECRET` | ⚪ | Secret Key（申請時僅顯示一次） |
| `FUBON_DRY_RUN` | ⚪ | `true` = 不實際下單；預設 `false` |

> **安全**：`.pfx`、API Key、Secret Key 絕不可 commit Git。使用 `.env` + `keyring` 或 Vault。

### 2.2 開通流程（一次性）

1. 開立富邦證券帳戶：<https://www.fubon.com/securities/open-now/>
2. 在 **Windows** 執行 TCEM.exe 申請數位憑證 → 取得 `.pfx`  
   （下載：<https://www.fbs.com.tw/Certificate/Management/>）
3. 線上簽署「API 使用風險暨聲明書」  
   （SOP：<https://www.fbs.com.tw/wcm/new_web/operate_manual/operate_manual_01/API-SignSOP_guide.pdf>）
4. （選用）申請 API Key：<https://www.fbs.com.tw/TradeAPI/docs/key/>

### 2.3 憑證轉移至 Linux

```bash
scp A123456789.pfx user@server:/secrets/fubon/
ssh user@server
chmod 400 /secrets/fubon/A123456789.pfx
# 有效期檢查
openssl pkcs12 -in A123456789.pfx -nokeys -info -passin pass:憑證密碼 \
  | openssl x509 -noout -dates
```

### 2.4 SDK 安裝

SDK 不在 PyPI，需手動下載 wheel：
```bash
# 下載頁：https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk
uv add ./fubon_neo-2.2.8-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
```

---

## 3. 核心模組使用方式

### 3.1 FubonClient 單例登入

```python
from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient

s = get_settings()
client = FubonClient.instance(s)
accounts = client.login()          # 回傳 list[AccountRef]
acc = client.account               # 選中的帳號（AccountRef）
sdk = client.sdk                   # 底層 FubonSDK 物件
```

`AccountRef` 屬性：`account`、`branch_no`、`account_type`、`account_name`、`display`（格式 `6460-1234567 (姓名)`）

### 3.2 SDK 原生登入（直接使用）

```python
from fubon_neo.sdk import FubonSDK, Order
from fubon_neo.constant import TimeInForce, OrderType, PriceType, MarketType, BSAction

sdk = FubonSDK()
accounts = sdk.login(
    personal_id,      # 身分證字號
    password,         # 電子交易密碼
    cert_path,        # .pfx 路徑
    cert_password,    # 憑證密碼
    # api_key, secret_key  # 選用
)
acc = accounts.data[0]
```

### 3.3 下單

```python
from fubon_neo.sdk import Order
from fubon_neo.constant import BSAction, PriceType, TimeInForce, MarketType, OrderType

order = Order(
    buy_sell=BSAction.Buy,          # BSAction.Buy / BSAction.Sell
    symbol="2330",
    price="1100",                   # 限價；None = 市價
    quantity=2000,                  # 2 張（1張 = 1000 股）
    market_type=MarketType.Common,  # Common/Odd/Fixing/AfterHour/Emg
    price_type=PriceType.Limit,     # Limit/LimitUp/LimitDown/Market/Reference
    time_in_force=TimeInForce.ROD,  # ROD/IOC/FOK
    order_type=OrderType.Stock,     # Stock/Margin/Short/DayTrade/DayTradeSell
)
result = sdk.stock.place_order(acc, order)
# result.is_success / result.message / result.data.order_no
```

### 3.4 帳務查詢

```python
sdk.stock.inventories(acc)                          # 庫存
sdk.stock.unrealized_gains_and_loses(acc)           # 未實現損益
sdk.stock.realized_gains_and_loses(acc, from, to)   # 已實現損益
sdk.stock.buying_power(acc)                         # 可用買進力
sdk.stock.bank_remain(acc)                          # 交割銀行餘額
sdk.stock.maintenance(acc)                          # 融資融券維持率
sdk.stock.settlements(acc, range)                   # 交割款
```

### 3.5 委託事件回呼

```python
def on_order(order):      pass  # 委託回報
def on_changed(changed):  pass  # 委託狀態變更
def on_filled(filled):    pass  # 成交回報
def on_event(event):      pass  # 連線事件（Connected/Disconnected/Error）

sdk.set_on_event(on_event)
sdk.set_on_order(on_order)
sdk.set_on_order_changed(on_changed)
sdk.set_on_filled(on_filled)
```

---

## 4. REST API Endpoints

### 認證

所有 endpoint 需帶 Bearer Token（由 `deps.py` 中 `TokenDep` 驗證）。

### Auth

| Method | Path | 說明 |
|---|---|---|
| POST | `/auth/login` | 以 `.env` 憑證執行 SDK 登入 |
| GET | `/auth/status` | 查詢目前登入狀態 |
| GET | `/auth/accounts` | 取得所有歸戶帳號 |
| PUT | `/auth/account` | 切換使用中帳號 |

### Account（帳務）

| Method | Path | 說明 |
|---|---|---|
| GET | `/account/inventories` | 庫存清單（`?force=true` 忽略快取） |
| GET | `/account/unrealized` | 未實現損益 |
| GET | `/account/realized` | 已實現損益（`?from=YYYY-MM-DD&to=YYYY-MM-DD`） |
| GET | `/account/cash` | 現金 / 交割餘額與買進力 |
| GET | `/account/maintenance` | 融資融券維持率 |

### Orders（委託）

| Method | Path | 說明 |
|---|---|---|
| GET | `/orders` | 當日委託列表 |
| POST | `/orders` | 下單 |
| DELETE | `/orders/{order_no}` | 刪單 |
| PATCH | `/orders/{order_no}/price` | 改價 |
| PATCH | `/orders/{order_no}/quantity` | 改量 |

**POST /orders 請求體（`PlaceOrderIn`）**：
```json
{
  "symbol": "2330",
  "side": "Buy",
  "quantity": 2000,
  "price": "1100.00",
  "price_type": "Limit",
  "time_in_force": "ROD",
  "market_type": "Common",
  "order_type": "Stock",
  "user_def": null
}
```

### Realtime（即時行情）

| Type | Path | 說明 |
|---|---|---|
| WebSocket | `/ws/quotes` | 即時行情雙向串流 |
| GET | `/healthz` | 健康檢查 |

---

## 5. WebSocket 行情協定（`/ws/quotes`）

### Client → Server（訂閱控制）

```json
{"action": "subscribe",      "symbols": ["2330", "2317"], "channels": ["trades", "books"]}
{"action": "unsubscribe",    "symbols": ["2330"],          "channels": ["trades"]}
{"action": "unsubscribe_all"}
{"action": "close"}
```

### Server → Client（推播）

```json
{"type": "data",   "channel": "trades", "data": { ... }}
{"type": "status", "event": "connected", "payload": { ... }}
{"type": "error",  "message": "尚未登入，請先呼叫 POST /auth/login"}
```

### 可用 Channel

| Channel | Speed 模式 | Normal 模式 | 說明 |
|---|:-:|:-:|---|
| `trades` | ✅ | ✅ | 逐筆成交（Tick） |
| `books` | ✅ | ✅ | 買賣五檔 |
| `indices` | ✅ | ✅ | 指數 |
| `aggregates` | ❌ | ✅ | 分鐘聚合 |
| `candles` | ❌ | ✅ | K 線 |

### 訂閱限制

- 單一 WebSocket 連線：**最多 200 個** `(channel, symbol)` 對
- 同帳號最大連線數：**5 條**
- 理論天花板：**1000 個訂閱**
- `SubscriptionManager` 自動分片，滿 200 才開新連線

---

## 6. 儀表板串接對照表

### 統計卡列（6 張）

| 卡片 | SDK 方法 | 取得方式 | 計算說明 |
|---|---|---|---|
| 今日損益 | `unrealized_gains_and_loses` + 歷史損益 | Poll | 已實現 + 未實現損益加總 |
| 今日報酬率 | 自行計算 | — | 今日損益 ÷ 今日投入本金 × 100% |
| 已實現損益 | `on_filled` 回呼累計 | Push | 含手續費（0.1425%）與交易稅（0.3%） |
| 持倉檔數 | `inventories` | Poll | 不同股票代號數 |
| 今日交易檔數 | `on_filled` 回呼累計 | Push | 當日買進不同代號數 |
| 可用額度 | `buying_power` | Poll | 可立即下單金額 |

### 即時監控表格

| 欄位 | 來源 | 更新方式 |
|---|---|---|
| 價格 / 漲跌 | `trades` channel | WS Push |
| 委賣張數（漲停板） | `books` channel，`ask[0].price == 漲停價` | WS Push |
| 1 秒成交量 | `trades` 滑動視窗累加 | WS Push |

**委賣張數判斷邏輯**：
```python
if ask[0].price == limit_up_price:
    sell_volume_at_limit = ask[0].volume
else:
    sell_volume_at_limit = 0  # 漲停板已打開或尚未漲停
```

### 持倉部位表格

| 欄位 | 來源 |
|---|---|
| 持股數 / 成本價 | `inventories` Poll |
| 現價 | `trades` WS Push |
| 損益 / 損益率 | 自行計算：(現價 - 成本) × 數量 × 1000 |

### 委託狀態表格

| 欄位 | 來源 |
|---|---|
| 所有欄位 | `on_order` / `on_order_changed` Push |
| 撤單動作 | `sdk.stock.cancel_order(acc, order_no)` |

### 成交記錄表格

| 欄位 | 來源 |
|---|---|
| 所有欄位 | `on_filled` Push |
| 損益計算 | (賣出金額 - 買入金額) - 手續費 × 2 - 交易稅 |

**費用公式**：
```
手續費 = 成交金額 × 0.1425%（最低 20 元）
交易稅 = 成交金額 × 0.3%（現股賣出）
實際損益 = 賣出金額 - 買入金額 - 買入手續費 - 賣出手續費 - 交易稅
```

---

## 7. RealtimeClient 使用方式

```python
from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.models import Channel, RealtimeMode

rt = RealtimeClient(
    client=fubon_client,              # FubonClient 實例
    mode=RealtimeMode.SPEED,          # SPEED（低延遲）或 NORMAL（完整欄位）
    reconnect_max_attempts=5,
    ring_buffer_size=1000,            # 每個 symbol 的環形緩衝區大小
)

def on_data(channel: Channel, dto) -> None:
    if channel == Channel.TRADES:
        print(dto.price, dto.volume)  # TradeData
    elif channel == Channel.BOOKS:
        print(dto.ask[0].price)       # BookData

rt.on_data(on_data)
rt.subscribe(Channel.TRADES, ["2330", "2317"])
rt.subscribe(Channel.BOOKS,  ["2330"])

# 取消 / 關閉
rt.unsubscribe(Channel.TRADES, ["2330"])
rt.unsubscribe_all()
rt.close()
```

---

## 8. 設定（`config.py`）

```python
from stock_order_api.config import get_settings

s = get_settings()
# s.personal_id, s.password（SecretStr）, s.cert_path（Path）
# s.cert_password（SecretStr）, s.branch_no, s.account_no
# s.api_key, s.api_secret（可為 None）
# s.dry_run（bool）, s.timeout_sec, s.reconnect_times
# s.log_dir（Path）, s.audit_db_path（Path）
# s.realtime_ring_buffer, s.realtime_reconnect_max
# s.realtime_reconnect_base_sec, s.realtime_reconnect_max_sec
```

`.env` 載入（env prefix = `FUBON_`），可搭配 OS keyring。

---

## 9. 審計日誌

### Loguru Sink 架構

| Sink | 檔案 | 用途 |
|---|---|---|
| stdout | — | 開發彩色輸出 |
| `logs/app.log` | 每日輪替，保留 30 天 | 人類可讀主日誌 |
| `logs/app.jsonl` | 100MB 輪替，保留 90 天 | 結構化 JSON（ELK/Loki）|
| `logs/trade.log` | 每日，保留 2555 天（7年）| 交易審計（`audit=True`）|
| `logs/error.log` | 10MB 輪替，保留 90 天 | 錯誤診斷 |

### 審計事件寫法

```python
from loguru import logger
import json

logger.bind(audit=True, event="PLACE_ORDER").info(
    json.dumps({
        "request_id": req_id,
        "account": acc.account,
        "symbol": order.symbol,
        "side": order.buy_sell.value,
        "qty": order.quantity,
        "price": order.price,
        "resp_order_no": result.data.order_no if result.is_success else None,
        "is_success": result.is_success,
        "message": result.message,
    }, ensure_ascii=False)
)
```

事件類型（`event` 欄位）：`LOGIN` / `PLACE_ORDER` / `MODIFY_PRICE` / `MODIFY_QTY` / `CANCEL` / `FILLED` / `DISCONNECT` / `RECONNECT`

### SQLite 稽核表

- 路徑：`logs/audit.sqlite3`
- 表：`audit_events`（`id, ts, event, request_id, payload_json`）
- 表：`orders`（`order_no, account, symbol, side, qty, price, status, last_update`）
- 表：`fills`（`order_no, seq, qty, price, ts`）

---

## 10. 錯誤處理

### HTTP 狀態碼對應

| 狀態碼 | 原因 |
|---|---|
| 401 | 未登入或 Token 錯誤 |
| 404 | 委託單不存在 |
| 422 | 請求參數錯誤（Pydantic 驗證失敗） |
| 502 | Fubon SDK 錯誤（`FubonError`） |

### SDK 錯誤碼

| 代碼 | 含意 | 處理方式 |
|---|---|---|
| `01xx` | 登入失敗（密碼錯/憑證過期/未簽聲明書）| 檢查 4 項前置條件 |
| `10xx` | 下單參數錯誤 | 檢查 `price_type` 與 `price` 搭配 |
| `20xx` | 帳務查詢失敗 | 確認 `acc` 物件正確 |
| `429` | 超出速率限制（股票下單約 50 req/s）| 指數退避重試 |
| `5xx` | 系統錯誤 | 重試 + 回報富邦 |

---

## 11. 開發指令速查

```bash
# 環境初始化
uv sync

# 啟動 FastAPI server（含熱重載）
uv run stock-order-api

# 啟動 GUI（PySide6）
uv run stock-order-gui

# CLI 帳務查詢
uv run stock-order-account inventories
uv run stock-order-account unrealized

# CLI 即時行情訂閱
uv run stock-order-quote watch trades,books 2330 2317
uv run stock-order-quote snapshot 2330

# 測試
uv run pytest
uv run pytest tests/test_realtime_client.py -v

# Lint / 型別檢查
uv run ruff check .
uv run mypy src
```

---

## 12. 重要注意事項

1. **dry_run 模式**：`FUBON_DRY_RUN=true` 時所有下單 API 不實際送出，僅寫 log。開發期間請啟用。
2. **憑證有效期**：1 年，到期需在 Windows 上用 TCEM.exe 展期，到期前 30 天主動更新。
3. **WebSocket 跨日重連**：建議每個交易日開盤前重新建立連線（`rt.close()` → 重新初始化）。
4. **零股訂閱**：WebSocket subscribe payload 需加 `"intradayOddLot": True`。
5. **API Key IP 白名單**：部署在雲端時需固定出口 IP 或設 NAT Gateway，並加入富邦後台白名單。
6. **無公開 Sandbox**：以**盤後 / 模擬單**或**現股 1 股**做最小額驗證；生產環境請謹慎。
7. **`quantity` 單位**：整股下單 quantity = 張數 × 1000（2 張 → `2000`）；零股直接填股數。
8. **SDK 載入**：`fubon_neo` 不在 PyPI，使用 `uv.sources` 指向本地 `.whl`。

---

## 13. 對外參考連結

- 官方文件：<https://www.fbs.com.tw/TradeAPI/>
- SDK 下載：<https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk>
- 訂閱限制：<https://www.fbs.com.tw/TradeAPI/docs/market-data/rate-limit>
- API Key 申請：<https://www.fbs.com.tw/TradeAPI/docs/key/>
- 憑證管理：<https://www.fbs.com.tw/Certificate/Management/>
- 聲明書 SOP：<https://www.fbs.com.tw/wcm/new_web/operate_manual/operate_manual_01/API-SignSOP_guide.pdf>
- 客服信箱：service.sec@fubon.com
