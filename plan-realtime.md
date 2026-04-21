# 即時行情模組（Realtime / WebSocket）實作計畫 — plan-realtime.md

> 範圍：本計畫鎖定 **富邦 Neo API WebSocket 行情服務**（不含 REST 歷史行情、下單、條件單）。
> 目標：在既有 `FubonClient` 之上，提供可訂閱 `trades / books / aggregates / candles / indices` 的即時行情封裝，含自動分片、斷線重連、GUI 即時報價頁。
> 母計畫：[plan.md](plan.md) · 帳務計畫：[plan-account.md](plan-account.md) · 憑證取得：[docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md) · API 總覽：[docs/fubon-api-overview.md](docs/fubon-api-overview.md)

---

## 1. 範圍與不在範圍

**在範圍內**
- `sdk.init_realtime(Mode)` 生命週期（建立／重連／關閉）
- 5 個 WebSocket channel：`trades` / `books` / `aggregates` / `candles` / `indices`
- `Speed` / `Normal` 兩種 Mode 切換
- **訂閱管理器**：自動分片（單連線 200 檔、最多 5 連線）
- **事件分派**：callback JSON → Pydantic DTO → 多訂閱者廣播
- 斷線自動重連 + 已訂閱清單還原
- CLI：`stock-order-quote watch` / `snapshot`
- GUI：即時報價分頁（商品清單 + 最新價 + 五檔 + K 線縮圖）
- 全流程寫入 loguru + 可選 SQLite 存檔（snapshot 用，非逐筆）

**不在範圍（後續 plan 處理）**
- REST 行情（日內／歷史／快照）→ 另立 `plan-marketdata-rest.md`
- 期貨選擇權行情（`sdk.futopt` 的 WebSocket）→ 版本二
- 策略引擎 / 訊號生成
- 下單與委託回報聯動

---

## 2. 官方限制（關鍵！）

來源：<https://www.fbs.com.tw/TradeAPI/docs/market-data/rate-limit>

| 項目 | 上限 |
| --- | --- |
| **單一 WebSocket 連線** | **200 訂閱** |
| **同帳號最大連線數** | **5 條** |
| 理論天花板 | **1000 個「頻道 × 商品」訂閱** |
| 日內行情（REST） | 300 次 / 分鐘 |
| 行情快照（REST） | 300 次 / 分鐘 |
| 歷史行情（REST） | 60 次 / 分鐘 |

> **訂閱計算方式：1 個 (channel, symbol) pair = 1 個訂閱**
> 範例：訂 2330 的 `trades` + `books` = 2 個訂閱；訂 50 檔 × 4 channel = 200 個訂閱（剛好塞滿一條連線）。

超過上限會收到：
```json
{"event":"error","data":{"code":1001,"message":"Maximum number of connections reached"}}
```

短時間大量重連會被判定為攻擊 → 擋 IP。

---

## 3. Mode 與 Channel 對照

| Mode | 延遲 | 可用 channel |
| --- | --- | --- |
| `Speed`（預設） | 低延遲（聚合推送） | `trades` / `books` / `indices` |
| `Normal` | 完整欄位 | 全部（`trades` / `books` / `aggregates` / `candles` / `indices`） |

| Channel | 說明 | 建議 TTL |
| --- | --- | --- |
| `trades` | 最新成交（逐筆/合併）| 無（stream） |
| `books` | 最佳五檔委買/委賣 | 無 |
| `aggregates` | 分鐘聚合（僅 Normal）| 無 |
| `candles` | K 線（僅 Normal）| 初始 snapshot 可快取 60 秒 |
| `indices` | 指數（加權、OTC 等）| 無 |

> 訂閱盤中零股需在 payload 多帶 `"intradayOddLot": True`。

---

## 4. 前置需求

**承自 [plan-account.md](plan-account.md) §3**，無額外需求：
- 已登入的 `FubonClient`（憑證登入即可取得行情權限）
- **不需** API Key / Secret
- 伺服器可對外 wss 443 出站（富邦會檢查 IP，若要走白名單模式須於後台綁 IP）

---

## 5. 模組結構（只列即時行情相關）

```
src/stock_order_api/
├── quote_cli.py                 # ★ 新增 CLI 入口：stock-order-quote
├── realtime/                    # ★ 新增
│   ├── __init__.py
│   ├── client.py                # RealtimeClient：包裝 init_realtime + 連線池
│   ├── subscription.py          # SubscriptionManager：分片/註冊/取消
│   ├── dispatcher.py            # EventDispatcher：callback → DTO → 廣播
│   ├── models.py                # Pydantic DTO：Trade/Book/Candle/Index/Aggregate
│   └── errors.py                # RealtimeError / SubscriptionLimitError
├── gui/pages/
│   └── quote_page.py            # ★ 新增：即時報價分頁
└── utils/
    └── ringbuf.py               # ★ 新增：每檔商品保留最近 N 筆（GUI 畫圖用）
```

---

## 6. 核心類別設計

### 6.1 `RealtimeClient`

```python
class RealtimeClient:
    """單例；依附在 FubonClient 之上。"""

    MAX_SUB_PER_CONN: int = 200
    MAX_CONNECTIONS: int = 5

    def __init__(self, client: FubonClient, mode: Mode = Mode.Speed): ...
    def connect(self) -> None: ...           # 建立第一條連線
    def close(self) -> None: ...             # 關所有連線
    def subscribe(
        self,
        channel: Channel,
        symbols: list[str],
        *,
        intraday_odd_lot: bool = False,
    ) -> list[SubscriptionId]: ...           # 自動分片
    def unsubscribe(self, ids: list[SubscriptionId]) -> None: ...
    def on(self, event: EventType, handler: Callable[[Any], None]) -> None: ...
```

### 6.2 `SubscriptionManager`

- 維護 `dict[SubscriptionId, (channel, symbol, conn_idx)]`
- `allocate(channel, symbols)` → 回傳 `list[(conn_idx, symbols_for_that_conn)]`
- 新連線策略：**先塞滿既有連線**，不夠才開下一條；開到第 6 條直接 raise `SubscriptionLimitError`
- 支援動態取消後回收配額（重新 compact 待後續 PR）

### 6.3 `EventDispatcher`

- 綁定 `stock.on("message", self._on_message)`
- 依 `msg["event"]` 分流：`data` / `subscribed` / `unsubscribed` / `error` / `heartbeat` / `pong`
- `data` 事件依 `channel` 轉成對應 DTO 後呼叫註冊的 handler（support 多訂閱者）
- Qt 端用 `QObject` 的 signal 橋接，避免跨 thread 存取 widget

### 6.4 DTO（Pydantic）

```python
class Trade(BaseModel):
    symbol: str
    price: Decimal
    size: int
    time: datetime          # ns 精度
    bid_ask: Literal["bid", "ask", "even"] | None = None

class Book(BaseModel):
    symbol: str
    time: datetime
    bids: list[BookLevel]   # 最多 5 檔
    asks: list[BookLevel]

class BookLevel(BaseModel):
    price: Decimal
    size: int

class Candle(BaseModel): ...       # OHLCV + time
class Aggregate(BaseModel): ...
class Index(BaseModel): ...
```

欄位名稱與富邦官方 payload 對齊；`extra="ignore"` 保險。

---

## 7. 斷線重連策略

| 事件 | 行為 |
| --- | --- |
| `on("connect")` | log INFO；若是 reconnect，重放訂閱 |
| `on("disconnect", code, msg)` | log WARN；2 秒後 `connect()`，指數退避最多 60 秒 |
| `on("error", err)` | log ERROR；若是訂閱限制，raise 向上（不重連） |
| 連續 5 次重連失敗 | log ERROR，停止嘗試，發 `connection_failed` 事件 |

---

## 8. CLI 設計

```bash
# 訂閱成交（持續輸出到終端機）
uv run stock-order-quote watch trades 2330 2317 2454

# 同時訂閱成交與五檔，JSON Lines 輸出
uv run stock-order-quote watch trades,books 2330 --output jsonl

# 一次性 snapshot（內部改用 REST/快照）
uv run stock-order-quote snapshot 2330

# 切 Normal 模式並訂閱 K 線
uv run stock-order-quote watch candles 2330 --mode normal

# 切分示例（自動分片）：訂 1000 檔 × trades → 用滿 5 條連線
uv run stock-order-quote watch trades $(cat symbols.txt)
```

- 輸出格式：`table`（rich live table 即時更新） / `jsonl` / `csv`
- `Ctrl-C` 優雅關閉：先 `unsubscribe_all()` 再 `close()`

---

## 9. GUI 設計（Quote 分頁）

- 左側：訂閱清單（可新增／刪除商品代號）
- 中央：**即時報價表**（symbol / 最新價 / 漲跌 / 量 / 買一 / 賣一 / 時間）
- 右側：**五檔深度**（選中商品切換）
- 底部：**近 300 筆 tick 折線圖**（`pyqtgraph`，後續可關可開）
- 右上：連線狀態燈（綠/黃/紅）+ 連線數指示（`2/5`）+ Mode 切換
- 訂閱上限告警：接近 1000 或訂滿時彈 `QMessageBox` 警告

執行緒模型：
- SDK callback 在 SDK 背景 thread → `QMetaObject.invokeMethod` 丟到主 thread
- 表格模型用 `QAbstractTableModel`，單一 symbol 更新時只發 `dataChanged(row, row)` 避免重繪整表

---

## 10. 日誌 / 稽核 / 效能

- **不** 把每一筆 tick 寫入 SQLite（量太大）
- `logs/realtime.jsonl`：只寫 `subscribe/unsubscribe/connect/disconnect/error/rate_limit` 等控制事件
- 每 10 秒寫一筆 `STATS`：各 channel 的訊息速率（msg/s）、延遲 p50/p95
- tick 本身保存在記憶體 ring buffer（`utils/ringbuf.py`，每檔商品最多 500 筆）
- 若使用者按「匯出 tick」才 dump CSV 到 `exports/`

---

## 11. 設定（新增至 `.env`）

```env
# ==== 行情 ====
FUBON_REALTIME_MODE=speed           # speed | normal
FUBON_REALTIME_RECONNECT_MAX=5      # 連續失敗幾次後停止
FUBON_REALTIME_RECONNECT_BASE_SEC=2 # 指數退避起始秒數
FUBON_REALTIME_RING_BUFFER=500      # 每檔保留 tick 數
FUBON_REALTIME_STATS_INTERVAL=10    # STATS 秒
```

---

## 12. 測試策略

- **單元測試**：
  - `SubscriptionManager` 分片（199→1 條、201→2 條、1001→raise）
  - `EventDispatcher` payload 轉 DTO 的欄位對齊
  - 斷線重連指數退避（用 fake timer）
- **整合測試**（需憑證 + 盤中）：標 `@pytest.mark.live`
  - 訂 `2330` trades/books 10 秒，assert 收到至少 N 筆
  - 訂滿 200 再加一檔，觀察是否自動開第二條連線
- **壓力測試**（手動）：訂 1000 檔 × trades，觀察 CPU / 記憶體 / 丟包

---

## 13. 實作步驟（依序 PR）

1. **Step R-1** `realtime/models.py` + `realtime/errors.py`（純 DTO，含 unit test）
2. **Step R-2** `realtime/subscription.py` 分片演算法（含 unit test）
3. **Step R-3** `realtime/client.py` 連線生命週期（unit test + live smoke test）
4. **Step R-4** `realtime/dispatcher.py` callback → DTO 分派
5. **Step R-5** CLI `stock-order-quote watch/snapshot` + rich live table
6. **Step R-6** GUI Quote 分頁（表格 + 五檔 + 連線狀態）
7. **Step R-7** 斷線重連與訂閱還原、STATS 指標
8. **Step R-8** 文件：使用手冊、限制說明、故障排除

---

## 14. 風險與邊界

| 風險 | 緩解 |
| --- | --- |
| 訂閱超過 1000 導致服務被拒 | `SubscriptionManager` 在 961 檔時警告、1000 時 raise；CLI/GUI 都擋 |
| SDK callback 在背景 thread 誤觸 Qt | 一律透過 signal 橋接 |
| tick 量大造成 GUI 卡頓 | 表格只發 partial `dataChanged`；折線圖可關閉；批次 flush（每 50ms） |
| 斷線造成漏單 | `on("disconnect")` 後重連立即 re-subscribe；跨 disconnect 期間 tick 視為遺失（即時行情本來就不保證 replay） |
| Speed 模式不支援 `candles/aggregates` | 呼叫時先檢查 mode，不符 raise `RealtimeError("channel X not allowed in Speed mode")` |
| IP 未加白名單（若啟用 API Key 模式）| 登入階段就會擋；REST 錯誤碼 401 時提示到後台加 IP |
| 券商限制變動 | 常數 `MAX_SUB_PER_CONN / MAX_CONNECTIONS` 抽到設定檔，方便調整 |

---

## 15. 驗收條件（Definition of Done）

- [ ] `uv run stock-order-quote watch trades 2330 2317` 可於盤中持續輸出成交
- [ ] 訂閱 201 檔自動開 2 條連線；訂閱 1001 檔 raise `SubscriptionLimitError`
- [ ] 拔網路 → 接回網路，自動重連並還原訂閱，log 可見完整流程
- [ ] GUI Quote 分頁可新增/刪除商品、切換 Speed/Normal、顯示連線數 `N/5`
- [ ] `pytest -q` 全綠（含 realtime 新測試，live 測試以 `-m "not live"` 跳過）
- [ ] `ruff check` + `mypy --strict` 零錯誤
- [ ] 本文件與 `docs/fubon-api-overview.md` 行情章節互相連結
