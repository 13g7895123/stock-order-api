# 帳務模組（Account）實作計畫 — plan-account.md

> 範圍：本計畫鎖定 **富邦 Neo API 帳務查詢**（不含下單、行情、條件單）。
> 目標：**最小可跑的本機 GUI**，可登入富邦、選擇帳號，顯示「庫存／未實現損益／已實現損益／買進力／交割款／維持率」，並寫完整日誌。
> 母計畫：[plan.md](plan.md) · 憑證取得：[docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md) · API 總覽：[docs/fubon-api-overview.md](docs/fubon-api-overview.md)

---

## 1. 範圍與不在範圍

**在範圍內**
- 登入（憑證 + 可選 API Key）
- 歸戶帳號選擇
- 庫存 `inventories`
- 未實現損益 `unrealized_gains_and_loses`
- 已實現損益 `realized_gains_and_loses`（可指定日期區間）
- 買進力 `buying_power`
- 交割款 `settlements`
- 融資融券維持率 `maintenance`
- 本機 GUI（PySide6）+ 完整日誌 + SQLite 快取
- 手動「重新整理」、自動每 30 秒刷新、CSV 匯出

**不在範圍（後續 plan 處理）**
- 下單 / 改單 / 刪單
- 事件回報（on_order / on_filled）
- 行情 REST / WebSocket
- 條件單
- 期貨選擇權

---

## 2. 交付成果

| 項目 | 成品 |
| --- | --- |
| CLI | `uv run python -m stock_order_api.account_cli inventories` 等子命令 |
| GUI | `uv run stock-order-gui`（帳務頁籤可用，其他暫停用）|
| 日誌 | `logs/app.log`、`logs/app.jsonl`、`logs/error.log`、`logs/audit.sqlite3` |
| 匯出 | 各頁面「匯出 CSV」按鈕，輸出至 `exports/YYYYMMDD_HHMMSS_<type>.csv` |
| 測試 | `pytest` 單元測試覆蓋 mapper 與 cache |

---

## 3. 前置需求（必須先備齊）

依 [docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md) 完成：

- [ ] 富邦證券帳戶（身分證、電子交易密碼）
- [ ] `.pfx` 憑證（TCEM.exe 申請，複製到 `secrets/<ID>.pfx`）
- [ ] 憑證密碼
- [ ] API 使用風險聲明書已簽署 + 連線測試通過
- [ ] （選）API Key / Secret Key（若啟用，設定 IP 白名單）
- [ ] 分公司代號、證券帳號

---

## 4. 模組結構（只列帳務相關）

```
src/stock_order_api/
├── __main__.py                  # GUI 入口
├── account_cli.py               # CLI 入口（本計畫新增）
├── config.py
├── logging_setup.py
├── fubon/
│   ├── client.py                # FubonSDK 單例 + 登入
│   └── stock_account.py         # ★ 本計畫重點：帳務查詢封裝
├── audit/
│   └── store.py                 # SQLite：cache + 審計
├── gui/
│   ├── app.py
│   ├── main_window.py
│   ├── login_dialog.py
│   └── pages/
│       ├── inventory_page.py    # ★
│       ├── pnl_page.py          # ★ 已實現/未實現
│       ├── cash_page.py         # ★ 買進力/交割款/維持率
│       └── log_viewer.py
└── utils/
    ├── cache.py                 # TTL cache
    └── csv_export.py
tests/
├── test_account_mapper.py
├── test_cache.py
└── test_config.py
```

---

## 5. 資料模型（Pydantic）

```python
# src/stock_order_api/fubon/stock_account.py
from decimal import Decimal
from datetime import date
from pydantic import BaseModel

class InventoryItem(BaseModel):
    account: str
    symbol: str
    name: str | None = None
    order_type: str            # Stock / Margin / Short ...
    today_qty: int             # 今日可賣
    total_qty: int             # 總庫存（股數）
    avg_price: Decimal
    market_value: Decimal | None = None

class UnrealizedItem(BaseModel):
    account: str
    symbol: str
    order_type: str
    qty: int
    avg_price: Decimal
    last_price: Decimal | None = None
    pnl: Decimal                # 未實現損益
    pnl_rate: Decimal | None = None

class RealizedItem(BaseModel):
    account: str
    trade_date: date
    symbol: str
    order_type: str
    qty: int
    buy_price: Decimal
    sell_price: Decimal
    pnl: Decimal
    fee: Decimal | None = None
    tax: Decimal | None = None

class BuyingPower(BaseModel):
    account: str
    cash: Decimal
    buying_power: Decimal      # 可用買進餘額
    margin_quota: Decimal | None = None
    short_quota: Decimal | None = None

class Settlement(BaseModel):
    account: str
    t_date: date               # T / T+1 / T+2
    amount: Decimal            # 應收(+)/應付(-)

class Maintenance(BaseModel):
    account: str
    maintenance_rate: Decimal  # %，如 180.5
    margin_value: Decimal
    short_value: Decimal
    warning_line: Decimal | None = None
```

> 欄位以 SDK 回傳實際結構為準，於 M2 實作時以 `logger.debug(repr(raw))` 印出對照，再調整 mapper。

---

## 6. 封裝層 API 設計

```python
# src/stock_order_api/fubon/stock_account.py
class StockAccount:
    def __init__(self, client: "FubonClient"):
        self.client = client
        self.sdk = client.sdk
        self.acc = client.account

    def inventories(self) -> list[InventoryItem]: ...
    def unrealized(self) -> list[UnrealizedItem]: ...
    def realized(self, start: date, end: date) -> list[RealizedItem]: ...
    def buying_power(self) -> BuyingPower: ...
    def settlements(self) -> list[Settlement]: ...
    def maintenance(self) -> Maintenance | None: ...  # 無信用戶時回 None
```

**共通規範**
- 每個方法內呼叫 SDK，收到 `Result` 後：
  1. `is_success=False` → raise `FubonAccountError(message, code)`，並寫 `error.log`。
  2. 成功 → mapper 轉為 pydantic model → 寫 `app.jsonl`（DEBUG）與審計表（INFO）。
- 呼叫前檢查 `request_id`（UUID）、帶入 `logger.bind(request_id=..., event="QUERY_INVENTORY")`。
- 每個方法預設套 **TTL cache**：
  - `inventories / unrealized` → 10 秒
  - `buying_power / settlements / maintenance` → 30 秒
  - `realized` → **不快取**（日期區間變動大）
- `realized` 超過 90 天自動切片多次查詢後合併。

---

## 7. 快取與審計（SQLite）

`logs/audit.sqlite3` 使用單一檔案，含三張表：

```sql
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,               -- ISO8601
  event TEXT NOT NULL,            -- LOGIN / QUERY_* / ERROR
  request_id TEXT,
  account TEXT,
  ok INTEGER NOT NULL,            -- 0/1
  message TEXT,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS cache_entries (
  cache_key TEXT PRIMARY KEY,     -- e.g. "inventories:6460-1234567"
  fetched_at TEXT NOT NULL,
  ttl_sec INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,             -- inventories/unrealized/...
  account TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
```

- `cache_entries`：行程內 LRU + SQLite 雙層；重啟仍可沿用近期資料（TTL 內）。
- `snapshots`：每次成功查詢寫入一筆，便於跨日比對。

---

## 8. GUI（PySide6）

### 8.1 視窗結構（本階段簡化）

```
┌─ 富邦帳務（Account Only）───────────────────────┐
│ 狀態列：登入狀態 / 帳號下拉 / 最後更新時間      │
├──────────────────────────────────────────────────┤
│ Tab： 庫存 | 未實現 | 已實現 | 現金/交割 | 維持率 │
├──────────────────────────────────────────────────┤
│ [ 表格區 ]                                       │
│                                                  │
│ 按鈕：重新整理 / 自動刷新 ☐ / 匯出 CSV           │
├──────────────────────────────────────────────────┤
│ Log 面板（即時）                                 │
└──────────────────────────────────────────────────┘
```

### 8.2 登入流程
1. 啟動讀 `.env` + `keyring`；缺欄位 → 登入對話框。
2. 呼叫 `FubonClient.login()`，成功後把 `accounts.data` 灌入狀態列下拉。
3. 切換帳號即重設 `FubonClient.account` 並清相關 cache。

### 8.3 非阻塞
- 所有查詢用 `QThreadPool` + `QRunnable`；完成後透過 `Signal` 回 UI 更新表格。
- 「自動刷新」以 `QTimer`（預設 30 秒；庫存/未實現頁專用）。

### 8.4 互動細節
- 表格支援排序、欄寬記憶（`QSettings`）。
- 金額欄位右對齊、千分位；損益欄位正紅負綠。
- 「已實現」頁提供日期範圍（預設今日 - 30 天 ~ 今日）、帳號過濾。
- 每頁右上角顯示「最後更新：HH:mm:ss（來源：cache/api）」。

---

## 9. CLI（開發初期主要用這個驗證）

```bash
# 基本驗證
uv run python -m stock_order_api.account_cli login

# 查詢
uv run python -m stock_order_api.account_cli inventories
uv run python -m stock_order_api.account_cli unrealized
uv run python -m stock_order_api.account_cli realized --from 2026-03-01 --to 2026-04-21
uv run python -m stock_order_api.account_cli buying-power
uv run python -m stock_order_api.account_cli settlements
uv run python -m stock_order_api.account_cli maintenance

# 通用參數
--account 6460-1234567   # 指定帳號（歸戶時必要）
--output table|json|csv   # 預設 table
--no-cache                # 強制直打 API
```

CLI 先於 GUI 實作，可快速驗證 mapper 正確性。

---

## 10. 日誌規範（帳務專用）

> 沿用 [plan.md §5](plan.md) 的 Loguru 設定；本模組再追加事件型別：

| event | 時機 | 等級 |
| --- | --- | --- |
| `LOGIN` / `LOGIN_FAILED` | 登入 | INFO / ERROR |
| `QUERY_INVENTORY` | 每次呼叫 inventories | INFO |
| `QUERY_UNREALIZED` | 同上 | INFO |
| `QUERY_REALIZED` | 同上；帶 `from/to` | INFO |
| `QUERY_BUYING_POWER` | 同上 | INFO |
| `QUERY_SETTLEMENTS` | 同上 | INFO |
| `QUERY_MAINTENANCE` | 同上 | INFO |
| `CACHE_HIT` / `CACHE_MISS` | 每次查詢首段 | DEBUG |
| `SNAPSHOT_WRITTEN` | 寫入 `snapshots` | DEBUG |
| `ACCOUNT_SWITCH` | GUI 切換帳號 | INFO |
| `ERROR_*` | 任意例外 | ERROR（含 stacktrace） |

每筆 INFO 事件的 JSON payload 至少含：`request_id`, `account`, `ok`, `elapsed_ms`, `result_count`。

---

## 11. 實作步驟（建議兩週內完成）

### Step 1：骨架（0.5 天）
- [ ] `uv init --package` / `.python-version` / `.gitignore` / `.env.example`
- [ ] 加依賴：`uv add fubon-neo pydantic pydantic-settings loguru keyring PySide6`
- [ ] 加 dev：`uv add --dev pytest pytest-mock mypy ruff`
- [ ] 建立 `logs/`、`secrets/`、`exports/`（`.gitkeep`）

### Step 2：設定 + 日誌（0.5 天）
- [ ] `config.py`（讀 `.env`；SecretStr）
- [ ] `logging_setup.py`（5 sink）
- [ ] 寫 `tests/test_config.py` 確認必填欄位缺漏會 raise

### Step 3：Client（1 天）
- [ ] `FubonClient` 單例：建立、login（憑證 / API Key）、切帳號、健康檢查
- [ ] 憑證到期檢查（OpenSSL / `cryptography` 讀 `.pfx`）
- [ ] CLI：`account_cli login` 驗證能取得 `accounts.data`

### Step 4：帳務封裝（2 天）★本模組核心
- [ ] 各方法先用 `logger.debug(repr(raw))` 印出 SDK 原始結構
- [ ] 寫 mapper → pydantic model
- [ ] 套上 TTL cache、審計寫入、error 處理
- [ ] `tests/test_account_mapper.py` 用 fixture 做純 mapper 測試

### Step 5：CLI 全部子命令（1 天）
- [ ] `inventories / unrealized / realized / buying-power / settlements / maintenance`
- [ ] `--output`、`--no-cache`、`--account`
- [ ] CSV 匯出（table → CSV 重用）

### Step 6：GUI 基本框（1.5 天）
- [ ] QApplication + 主視窗 + 狀態列
- [ ] 登入對話框（含 keyring 記憶）
- [ ] Log 面板（Qt signal sink）

### Step 7：GUI 帳務頁（2 天）
- [ ] 五個 Tab + 非阻塞查詢
- [ ] 自動刷新、CSV 匯出、欄位格式（顏色/千分位）
- [ ] 帳號切換清 cache

### Step 8：收斂（1 天）
- [ ] `ruff check`、`mypy --strict` 綠燈
- [ ] README：啟動指令、疑難排解
- [ ] 手動驗收：實際帳號跑一輪對資料

---

## 12. 驗收條件（DoD）

1. **登入**：`account_cli login` 與 GUI 登入皆可成功並顯示歸戶清單。
2. **資料正確**：六個查詢與富邦 e 點通顯示一致（容許延遲 30 秒內）。
3. **日誌完整**：
   - `logs/app.log` 有所有查詢紀錄
   - `logs/app.jsonl` 每筆有 `request_id / account / elapsed_ms`
   - `logs/audit.sqlite3` 的 `audit_events` 與 `snapshots` 有對應資料
4. **錯誤處理**：拔網路 / 憑證錯密碼時，GUI 顯示友善訊息且 `error.log` 有 stacktrace。
5. **效能**：首次查詢 < 2 秒；cache hit < 50ms。
6. **安全**：`.pfx` 權限 ≤ 400；`.env` 不入 Git（`gitleaks` 掃過）。

---

## 13. 風險與緩解

| 風險 | 緩解 |
| --- | --- |
| SDK 回傳欄位與文件略有差異 | Step 4 先以 `logger.debug(repr(raw))` 實測；mapper 用 `model_validate` 容錯 |
| 已實現損益跨月限制 | 自動切片 ≤ 90 天，合併結果並以 `trade_date` 排序 |
| 歸戶多帳號易混淆 | GUI 狀態列固定顯示目前帳號；切帳號清 cache 並彈提示 |
| 憑證密碼鍵入錯誤 | 登入對話框有「顯示密碼」toggle；錯誤時明確提示憑證路徑與到期日 |
| 雲端同步 `logs/` 造成鎖檔 | `logs/` 加入各雲端排除（Dropbox/OneDrive）|

---

## 14. 下一步（Next）

完成本模組後，下一份子計畫可展開：
- `plan-trading.md`：股票下單 / 改單 / 刪單 + 事件回報
- `plan-marketdata.md`：行情 REST / WebSocket
- `plan-condition-and-futopt.md`：條件單與期權

本階段暫不動這些，專注把 **account** 打穩並有實測資料比對。
