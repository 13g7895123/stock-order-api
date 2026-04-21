# 富邦證券 新一代 API（Fubon Neo API）功能總覽

> 來源：富邦證券 TradeAPI 官方文件 <https://www.fbs.com.tw/TradeAPI/>
> 本文為 zh-tw 整理版，供本專案 `40_stock-order-api` 串接規劃使用。

---

## 1. 產品概述

| 項目 | 說明 |
| --- | --- |
| 產品名稱 | 富邦新一代 API（Fubon Neo API） |
| 服務範圍 | **交易（Trading）** + **行情（Market Data）** |
| 支援平台 | Windows / macOS / Linux |
| 支援語言 | Python、C#、JavaScript (Node.js)、C++、Go（C++/Go 僅支援證券交易帳務及條件單） |
| 登入方式 | **憑證登入**（必要）＋ 可選 **API Key** |
| Python 版本 | 3.7（~v1.3.2）、3.8–3.13（v2.0.1~，不含 3.14） |
| Node.js 版本 | Node.js 16 以上 |
| .NET 版本 | .NET Standard 2.0 / .NET Core 3.1+ / .NET Framework 4.7.2+ |

Fubon Neo API 為富邦證券提供給程式交易／量化使用者的官方 SDK，直連交易所，支援股票、權證、期貨、選擇權、條件單，並提供 REST + WebSocket 的即時行情。

---

## 2. 使用前置需求（必要條件）

使用任何 API 功能前，必須先完成以下 4 個步驟：

1. **開立富邦證券帳戶**
   - 尚未開戶者至 <https://www.fubon.com/securities/open-now/> 線上開戶。
   - 取得資料：**身分證字號**、**分公司代號**、**帳號**、**電子交易密碼**。

2. **申請數位憑證（必要）**
   - 至 <https://www.fbs.com.tw/Certificate/Management/> 下載 **富邦證券憑證 e 總管（TCEM.exe，Windows 專用）**。
   - 申請流程：登入 → 身份驗證 → 收取 OTP → 完成後憑證存於 `C:\CAFubon\<身分證字號>\<身分證字號>.pfx`。
   - **憑證密碼**：可選擇自訂或使用「預設密碼」（預設密碼＝登入 ID）。
   - **有效期限**：1 年，到期需展期。
   - 跨平台使用：將 `.pfx` 檔案複製到目標主機（Linux/macOS）即可。

3. **簽署「API 使用風險暨聲明書」** + **連線測試**
   - 線上簽署 SOP：<https://www.fbs.com.tw/wcm/new_web/operate_manual/operate_manual_01/API-SignSOP_guide.pdf>
   - 連線測試小幫手（Windows）：<https://www.fbs.com.tw/TradeAPI_SDK/sample_code/API_Sign_Test.zip>
   - **未完成簽署無法登入 API**。

4. **（選用）申請 API Key / Secret Key**
   - 入口：<https://www.fbs.com.tw/TradeAPI/docs/key/>
   - 須先申請網頁版憑證，再於後台新增金鑰。
   - 可設定 **IP 白名單**、**有效期限**、**權限範圍**（僅行情 / 僅下單 / 全部）。
   - 每個身分證最多 **30 把 Key**；Secret Key 僅顯示一次，關閉頁面後無法再看到。
   - V2.2.8 以後：僅受影響的 Key session 會被強制斷線。

---

## 3. 登入所需憑證資訊一覽

| 資料 | 來源 | 是否必要 | 用途 |
| --- | --- | --- | --- |
| 身分證字號（personal_id）| 本人 | ✅ 必要 | `sdk.login` 第 1 參數 |
| 電子交易登入密碼（password）| 富邦證券開戶後取得 | ✅ 必要 | `sdk.login` 第 2 參數 |
| 憑證檔案路徑（cert_path）| `C:\CAFubon\<ID>\<ID>.pfx` 或複製到 Linux/macOS | ✅ 必要 | `sdk.login` 第 3 參數 |
| 憑證密碼（cert_pass）| 申請憑證時設定；或預設＝登入 ID | ✅ 必要 | `sdk.login` 第 4 參數；v1.3.2+ 可省略 |
| API Key | 金鑰管理後台 | ⚪ 選用 | `FubonSDK(30, 2, "api_key")` 初始化 |
| Secret Key | 金鑰管理後台（僅顯示一次）| ⚪ 選用 | `sdk.login(..., api_key, secret_key)` |

> **安全提醒**：憑證檔、API Key、Secret Key 絕對不可提交 Git，請使用環境變數或秘密管理服務（Vault、AWS Secrets Manager、Doppler 等）。

---

## 4. SDK 核心物件與登入流程

### 4.1 Python 登入範例

```python
from fubon_neo.sdk import FubonSDK, Order
from fubon_neo.constant import TimeInForce, OrderType, PriceType, MarketType, BSAction

# 1) 建立 SDK
sdk = FubonSDK()                              # 預設連線
# sdk = FubonSDK(30, 2, "https://...")        # 自訂 timeout / reconnect / url

# 2) 憑證登入
accounts = sdk.login(
    "A123456789",            # 身分證字號
    "your_login_password",   # 電子交易密碼
    "/path/to/A123456789.pfx",
    "cert_password",          # 若使用預設密碼可省略 (v1.3.2+)
)

# 3) 若使用 API Key
accounts = sdk.login(
    "A123456789", "your_login_password",
    "/path/to/A123456789.pfx", "cert_password",
    "your_api_key", "your_secret_key",
)

acc = accounts.data[0]        # 歸戶時會有多筆帳號
```

### 4.2 回傳結構

SDK 所有方法多數回傳 `Result { is_success, message, data }` 物件；`data` 視功能不同而變化（可能是 `Account`, `OrderResult`, `List[Inventory]` ...）。

---

## 5. 交易 API 功能清單

### 5.1 股票 `sdk.stock.*`

#### 5.1.1 委託管理
| 功能 | 方法 | 說明 |
| --- | --- | --- |
| 下單 | `place_order(acc, order)` | 新單（現股、融資、融券、當沖、零股等）|
| 改價 | `modify_price(acc, order_no, price, price_type)` | 修改委託價 |
| 改量 | `modify_quantity(acc, order_no, quantity)` | 修改委託量（只可減量）|
| 刪單 | `cancel_order(acc, order_no)` | 取消委託 |
| 查詢當日委託 | `get_order_results(acc)` | 當日所有委託明細（含狀態）|
| 查詢單筆委託 | `get_order_result(acc, order_no)` | 依委託書號查單筆 |
| 查詢歷史成交 | `filled_history(acc, start_date, end_date)` | 歷史成交明細 |

**Order 物件欄位**（`fubon_neo.sdk.Order`）：
- `buy_sell`: `BSAction.Buy` / `BSAction.Sell`
- `symbol`: 股票代號（如 `"2881"`）
- `price`: 委託價（`str` 或 `None`，若 `price_type` 為 `LimitUp/LimitDown/Market/Reference` 可為 `None`）
- `quantity`: 張數 ×1000（如 2000 = 2 張）；零股直接填股數
- `market_type`: `Common`（整股）/ `Fixing`（盤後定價）/ `Odd`（盤中零股）/ `AfterHour`（盤後零股）/ `Emg`（興櫃）
- `price_type`: `Limit` / `LimitUp` / `LimitDown` / `Reference` / `Market`
- `time_in_force`: `ROD` / `IOC` / `FOK`
- `order_type`: `Stock`（現股）/ `Margin`（融資）/ `Short`（融券）/ `DayTrade`（當沖）/ `DayTradeSell`
- `user_def`: 使用者自定義標記（optional）

#### 5.1.2 帳務查詢
| 功能 | 方法 | 說明 |
| --- | --- | --- |
| 庫存 | `inventories(acc)` | 現有庫存股票明細 |
| 未實現損益 | `unrealized_gains_and_loses(acc)` | 庫存損益 |
| 已實現損益 | `realized_gains_and_loses(acc, start, end)` | 指定期間已實現 |
| 維持率 | `maintenance(acc)` | 融資融券維持率 |
| 交割款 | `settlements(acc, range)` | 未交割 T+1 / T+2 金額 |
| 銀行餘額 | `bank_remain(acc)` | 交割銀行餘額 |
| 買進力 | `buying_power(acc)` | 可用買進餘額 |
| 信用額度 | `margin_quota(acc)` | 融資融券額度 |

#### 5.1.3 條件單（Condition Order）
| 功能 | 方法 | 說明 |
| --- | --- | --- |
| 建立條件單 | `sdk.stock.make_condition_order(...)` / `create_condition_order(...)` | 觸價後自動掛單（支援 OCO、OSO、止損停利）|
| 查詢條件單 | `get_condition_orders(acc)` | 當日條件單列表 |
| 取消條件單 | `cancel_condition_order(acc, cond_id)` | 取消 |

### 5.2 期貨 / 選擇權 `sdk.futopt.*`

| 功能 | 方法 | 說明 |
| --- | --- | --- |
| 下單 | `place_order(acc, order)` | 期貨、選擇權、組合單 |
| 改價 / 改量 / 刪單 | `modify_price / modify_quantity / cancel_order` | 同股票 |
| 當日委託 / 成交 | `get_order_results(acc)` / `filled_history(...)` | 查詢 |
| 未平倉 | `open_positions(acc)` | 當前持倉 |
| 保證金 | `margin(acc)` | 權益數、維持保證金、風險指標 |
| 當沖損益 | `settlement_profit_loss(acc)` | 平倉損益 |

### 5.3 事件回呼（Event Handlers）

SDK 提供以下事件註冊點，用於即時接收委託 / 成交 / 連線狀態：

```python
sdk.set_on_event(on_event)              # 連線事件（Connected / Disconnected / Error）
sdk.set_on_order(on_order)              # 委託回報
sdk.set_on_order_changed(on_changed)    # 委託狀態變更
sdk.set_on_filled(on_filled)            # 成交回報
```

> 這些事件基於 WebSocket 長連線，需維持 session 不中斷；建議實作 reconnect 與心跳偵測。

---

## 6. 行情 API（Market Data）

### 6.1 初始化

```python
sdk.init_realtime()                         # 預設 Standard plan
sdk.init_realtime(Mode.Speed)               # 高速模式（需額外訂閱）
rest = sdk.marketdata.rest_client
ws   = sdk.marketdata.websocket_client
```

### 6.2 REST 行情

| 分類 | 範例方法 | 說明 |
| --- | --- | --- |
| 盤中快照 | `rest.stock.intraday.quote(symbol="2881")` | 單一標的即時報價 |
| 多檔快照 | `rest.stock.intraday.tickers(type="EQUITY", exchange="TWSE")` | 清單 |
| 分 K / 日 K | `rest.stock.intraday.candles(symbol=..., timeframe="1"/"5"/"D")` | K 線 |
| 逐筆明細 | `rest.stock.intraday.trades(symbol=...)` | Tick |
| 五檔 | `rest.stock.intraday.quote(...)` 的 `asks/bids` 欄位 | 買賣五檔 |
| 歷史 K 線 | `rest.stock.historical.candles(symbol=..., from_=..., to=...)` | 日/週/月 K |
| 期貨行情 | `rest.futopt.intraday.*` / `historical.*` | 同上，標的為期權 |

### 6.3 WebSocket 即時行情

```python
def handle_message(msg):
    print(msg)

ws.stock.on("message", handle_message)
ws.stock.connect()
ws.stock.subscribe({
    "channel": "trades",        # trades / aggregates / books / candles
    "symbols": ["2881", "2330"],
})
# unsubscribe / disconnect 同名呼叫
```

支援頻道：`trades`（逐筆成交）、`books`（買賣五檔）、`aggregates`（分 K 聚合）、`candles`（K 線）。

---

## 7. 限制與注意事項

| 項目 | 限制 |
| --- | --- |
| 交易速率（Rate Limit） | 依商品有別，股票下單約 **50 req/sec**；過高會收 `429` |
| Session 同時登入 | 同一帳號可多 session，但 API Key 權限變更會斷其相關 session |
| 憑證到期 | 1 年；過期需重新展期 |
| 測試環境 | 富邦無公開 sandbox；以**盤後 / 模擬單**測試，或用**現股 1 股**做最小額驗證 |
| 跨日 session | WebSocket 建議開盤前重連 |
| 跨平台憑證 | `.pfx` 檔可在 Linux/macOS 使用；但憑證「申請」必須在 Windows 完成 |

---

## 8. 常見錯誤碼摘要

| 代碼 | 含意 | 處理建議 |
| --- | --- | --- |
| `01xx` | 登入失敗（密碼錯、憑證過期、聲明書未簽）| 檢查 4 項前置條件 |
| `10xx` | 下單參數錯誤 | 檢查 `price_type` 與 `price` 搭配 |
| `20xx` | 帳務查詢失敗 | 確認 `acc` 物件正確 |
| `429` | 超出速率限制 | 指數退避重試 |
| `5xx` | 系統錯誤 | 重試 + 回報富邦 |

---

## 9. 參考連結

- 官方文件首頁：<https://www.fbs.com.tw/TradeAPI/>
- 交易 API 簡介：<https://www.fbs.com.tw/TradeAPI/docs/trading/introduction>
- 事前準備：<https://www.fbs.com.tw/TradeAPI/docs/trading/prepare>
- 快速開始：<https://www.fbs.com.tw/TradeAPI/docs/trading/quickstart>
- API Key 申請：<https://www.fbs.com.tw/TradeAPI/docs/trading/api-key-apply>
- SDK 下載：<https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk>
- 安裝相容性：<https://www.fbs.com.tw/TradeAPI/docs/install-compatibility/>
- 金鑰管理：<https://www.fbs.com.tw/TradeAPI/docs/key/>
- 客服信箱：service.sec@fubon.com
