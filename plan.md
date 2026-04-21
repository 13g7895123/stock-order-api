# 富邦 API 串接實作規劃（plan.md）

> 專案：`40_stock-order-api`
> 目標：以 **Python 3.11 + uv** 封裝 Fubon Neo API，提供 **本機簡易 GUI** 進行登入、查詢與下單，並寫出**完整日誌**供事後稽核。
>
> 相關文件：
> - [docs/fubon-api-overview.md](docs/fubon-api-overview.md)：API 功能總覽
> - [docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md)：**憑證 / 密碼 / API Key 取得指南**

---

## 0. 技術選型

| 項目 | 選擇 | 理由 |
| --- | --- | --- |
| 語言 | **Python 3.11** | Fubon Neo SDK 功能最完整 |
| 套件管理 | **[uv](https://github.com/astral-sh/uv)** | 快速、可鎖版本；取代 pip/poetry/virtualenv |
| GUI 框架 | **PySide6 (Qt 6)**（主選）或 **Flet** / **Tkinter**（備選） | 跨平台、控件成熟、可打包 exe/app |
| 設定與秘密 | `pydantic-settings` + `.env` + `keyring` | 型別安全；敏感資料可存 OS keychain |
| 日誌 | **Loguru** + 檔案輪替 | 簡潔；支援 JSON、多 sink |
| 排程 | `APScheduler` | 憑證到期檢查、對帳 |
| 打包 | `uv` + `PyInstaller`（選配）| 產生單檔 exe/app |
| 測試 | `pytest` + `pytest-mock` | 單元 + 整合 |
| 類型檢查 | `mypy` | 下單參數型別嚴謹 |
| 程式風格 | `ruff` + `ruff format` | 取代 black/flake8/isort |

---

## 1. 專案結構

```
40_stock-order-api/
├── pyproject.toml            # uv 管理，PEP 621 格式
├── uv.lock                   # 鎖定版本（進 Git）
├── .python-version           # 指定 3.11
├── .env.example              # 變數範本（進 Git）
├── .env                      # 實際秘密（不進 Git）
├── .gitignore
├── plan.md                   # 本檔
├── docs/
│   ├── fubon-api-overview.md
│   └── fubon-credentials-guide.md
├── logs/                     # 執行期日誌（不進 Git）
│   ├── app.log
│   ├── app.jsonl             # 結構化日誌
│   ├── trade.log             # 下單 / 成交審計
│   └── error.log
├── secrets/                  # .pfx 憑證放置區（不進 Git，權限 700）
│   └── A123456789.pfx
├── src/
│   └── stock_order_api/
│       ├── __init__.py
│       ├── __main__.py              # python -m stock_order_api → 啟動 GUI
│       ├── config.py                # pydantic-settings
│       ├── logging_setup.py         # Loguru 設定
│       ├── fubon/
│       │   ├── client.py            # FubonSDK 單例、登入、重連
│       │   ├── events.py            # on_order / on_filled 等回呼
│       │   ├── stock_order.py       # 股票下單 / 改 / 刪 / 查
│       │   ├── stock_account.py     # 庫存 / 損益
│       │   ├── futopt.py            # 期貨選擇權
│       │   ├── condition.py         # 條件單
│       │   └── marketdata.py        # 行情
│       ├── gui/
│       │   ├── app.py               # QApplication 入口
│       │   ├── main_window.py       # 主視窗
│       │   ├── login_dialog.py      # 登入對話框
│       │   ├── pages/
│       │   │   ├── order_page.py    # 下單頁
│       │   │   ├── orders_page.py   # 委託查詢
│       │   │   ├── inventory_page.py
│       │   │   ├── quote_page.py    # 行情
│       │   │   └── log_viewer.py    # 即時日誌檢視
│       │   └── widgets/
│       ├── audit/
│       │   └── store.py             # SQLite 稽核表
│       └── utils/
│           ├── ratelimit.py
│           └── retry.py
└── tests/
    ├── test_config.py
    ├── test_order_mapper.py
    └── test_ratelimit.py
```

---

## 2. 使用 uv 管理環境

### 2.1 安裝 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或 brew install uv
```

### 2.2 初始化專案

```bash
cd 40_stock-order-api
uv init --package                 # 建立 pyproject.toml + src 目錄
echo "3.11" > .python-version
uv python install 3.11
```

### 2.3 加入依賴

```bash
# 核心
uv add fubon-neo pydantic pydantic-settings loguru apscheduler keyring
# GUI
uv add PySide6
# 工具
uv add --dev pytest pytest-mock mypy ruff pyinstaller
```

> `fubon-neo` 套件名稱以 PyPI 實際為準；若 PyPI 未提供，則從 [SDK 下載頁](https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk) 取得 `.whl` 並 `uv add ./fubon_neo-*.whl`（Linux 請確認有 manylinux wheel）。

### 2.4 執行

```bash
uv sync                            # 同步依賴
uv run python -m stock_order_api   # 啟動 GUI
uv run pytest                      # 測試
uv run ruff check .                # lint
uv run mypy src                    # 型別檢查
```

### 2.5 鎖檔管理

- `uv.lock` **進 Git**，確保所有人 / CI / 生產版本一致。
- 升級：`uv lock --upgrade-package fubon-neo`。

### 2.6 `pyproject.toml` 範例骨架

```toml
[project]
name = "stock-order-api"
version = "0.1.0"
requires-python = ">=3.11,<3.14"
dependencies = [
  "fubon-neo",
  "PySide6>=6.6",
  "pydantic>=2",
  "pydantic-settings>=2",
  "loguru>=0.7",
  "apscheduler>=3.10",
  "keyring>=24",
]

[project.scripts]
stock-order-gui = "stock_order_api.__main__:main"

[tool.uv]
dev-dependencies = ["pytest", "pytest-mock", "mypy", "ruff", "pyinstaller"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

---

## 3. 前置資料（與 credentials 指南對應）

實作前必備資料請務必完整取得，詳細步驟見 [docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md)：

| 變數 | 必填 | 來源 |
| --- | --- | --- |
| `FUBON_PERSONAL_ID` | ✅ | 本人 |
| `FUBON_PASSWORD` | ✅ | 富邦開戶 |
| `FUBON_CERT_PATH` | ✅ | TCEM.exe 申請 |
| `FUBON_CERT_PASSWORD` | ✅ | 申請時自訂 |
| `FUBON_BRANCH_NO` | ✅ | 開戶資料 |
| `FUBON_ACCOUNT_NO` | ✅ | 開戶資料 |
| `FUBON_API_KEY` | ⚪ | 金鑰管理後台 |
| `FUBON_API_SECRET` | ⚪ | 金鑰管理後台（僅顯示一次）|
| `FUBON_DRY_RUN` | ⚪ | `true` 時不實際下單，僅 log |

---

## 4. 設定與秘密載入

```python
# src/stock_order_api/config.py
from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FUBON_", extra="ignore")

    personal_id: str
    password: SecretStr
    cert_path: Path
    cert_password: SecretStr
    branch_no: str
    account_no: str
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    dry_run: bool = False
    timeout_sec: int = 30
    reconnect_times: int = 2

settings = Settings()
```

- GUI 啟動時：若 `.env` 缺欄位 → 顯示「登入對話框」讓使用者輸入，可選擇以 `keyring.set_password(...)` 存入 OS Keychain（避免明文）。

---

## 5. 日誌設計（重點：完整 log）

### 5.1 Sink 配置（Loguru）

```python
# src/stock_order_api/logging_setup.py
from loguru import logger
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def setup_logging():
    logger.remove()

    # 1) 終端（開發用彩色）
    logger.add(
        sink=lambda m: print(m, end=""),
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> <level>{level:<7}</level> {message}",
    )

    # 2) 人類可讀主日誌（輪替：每日 + 保留 30 天）
    logger.add(
        LOG_DIR / "app.log",
        rotation="00:00", retention="30 days", compression="zip",
        level="DEBUG", enqueue=True, backtrace=True, diagnose=False,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
    )

    # 3) JSON 結構化日誌（給 ELK / Loki）
    logger.add(
        LOG_DIR / "app.jsonl",
        rotation="100 MB", retention="90 days",
        level="DEBUG", enqueue=True, serialize=True,
    )

    # 4) 交易審計日誌（獨立檔；長期保存 7 年）
    logger.add(
        LOG_DIR / "trade.log",
        rotation="1 day", retention="2555 days", compression="zip",
        level="INFO", enqueue=True,
        filter=lambda r: r["extra"].get("audit") is True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {extra[event]} | {message}",
    )

    # 5) 錯誤獨立檔
    logger.add(
        LOG_DIR / "error.log",
        rotation="10 MB", retention="90 days",
        level="ERROR", enqueue=True, backtrace=True, diagnose=True,
    )
```

### 5.2 審計記錄規範

每一筆下單 / 改單 / 刪單 / 登入 / 斷線都要寫 trade.log：

```python
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

共用欄位：

| 欄位 | 說明 |
| --- | --- |
| `ts` | ISO8601 毫秒 |
| `event` | `LOGIN` / `PLACE_ORDER` / `MODIFY_PRICE` / `MODIFY_QTY` / `CANCEL` / `FILLED` / `DISCONNECT` / `RECONNECT` |
| `request_id` | UUID；由 GUI 產生用於對映 |
| `account` | 帳號 |
| `symbol` / `side` / `qty` / `price` | 下單欄位 |
| `resp_order_no` / `is_success` / `message` | SDK 回應 |

### 5.3 稽核 DB（SQLite）

除了文字日誌外，另存 SQLite 便於查詢：
- `logs/audit.sqlite3`
- 表：`audit_events`（id, ts, event, request_id, payload_json）
- 表：`orders`（order_no, account, symbol, ... , status, last_update）
- 表：`fills`（order_no, seq, qty, price, ts）

GUI 的「委託查詢 / 成交查詢 / 稽核」頁面都查這個 DB。

### 5.4 GUI 內建 Log Viewer

主視窗底部 dock 一個即時 log 面板：
- 使用 `QPlainTextEdit`
- 透過 `logger.add(sink=qt_signal_emitter, ...)` 把 log 打進 Qt signal
- 支援 Level 過濾、關鍵字搜尋、一鍵開啟 `logs/` 資料夾

---

## 6. GUI 設計（PySide6）

### 6.1 視窗結構

```
┌─ StockOrderAPI ──────────────────────────────────┐
│ 選單：檔案 / 交易 / 工具 / 說明                    │
├──────────────────────────────────────────────────┤
│ 狀態列：[登入狀態] [帳號] [SDK版本] [連線狀態]    │
├────────────┬─────────────────────────────────────┤
│ 側邊頁籤   │ 主頁面                              │
│ - 下單     │                                     │
│ - 委託查詢 │                                     │
│ - 庫存     │                                     │
│ - 損益     │                                     │
│ - 行情     │                                     │
│ - 條件單   │                                     │
│ - 稽核     │                                     │
├────────────┴─────────────────────────────────────┤
│ Log 面板（即時）  [過濾] [搜尋] [開啟 logs/]      │
└──────────────────────────────────────────────────┘
```

### 6.2 登入對話框流程

1. 啟動時讀 `.env` + keyring。
2. 若欄位缺：彈出對話框輸入（身分證、密碼、憑證路徑、憑證密碼、API Key/Secret）。
3. 勾選「記住（安全存 keychain）」→ 存入 `keyring`。
4. 按「測試連線」→ 呼叫 `sdk.login(...)` → 成功顯示帳號列表 → 選擇 → 進入主視窗。

### 6.3 下單頁元件

- 股票代號（自動完成）
- 買 / 賣 切換
- 數量（張數 + 零股切換）
- 價格類型（Limit / Market / LimitUp / LimitDown / Reference）
- 下單類型（現股 / 融資 / 融券 / 當沖）
- 時效（ROD / IOC / FOK）
- **DRY RUN 開關**（config 為 true 時預設勾選且按鈕變橘色）
- **雙重確認彈窗**（顯示金額與張數）

### 6.4 非阻塞執行緒

- 所有 SDK 呼叫透過 `QThreadPool` / `asyncio` 跑在背景，避免 GUI 凍結。
- SDK 事件（on_order/on_filled）由背景執行緒接收 → `QtSignal` emit 到 GUI 更新。

### 6.5 安全設計

- 主視窗閒置 15 分鐘自動鎖定（要求重新輸入交易密碼才能下單）。
- 任何下單動作前檢查 **Kill Switch**（設定檔或 GUI 開關）。
- 啟動時若偵測憑證有效期 ≤ 30 天 → 警示 banner。

---

## 7. 實作里程碑

### M1：環境 + 登入（Foundation）
- [ ] `uv init` + `pyproject.toml`
- [ ] `config.py`、`logging_setup.py`
- [ ] `FubonClient` 單例、登入流程、憑證到期檢查
- [ ] 最小 GUI：登入視窗 + 空主視窗 + log 面板
- [ ] `tests/test_config.py`

### M2：股票下單 + 審計
- [ ] Order DTO、參數驗證（price_type × price 搭配）
- [ ] `place_order / modify_price / modify_quantity / cancel_order`
- [ ] 下單頁 GUI + DRY RUN
- [ ] trade.log 審計、SQLite 稽核表
- [ ] Rate limit（40 req/sec）

### M3：事件回報
- [ ] `on_order / on_order_changed / on_filled / on_event`
- [ ] 回報寫入 SQLite 並在 GUI「委託查詢」即時刷新
- [ ] 斷線重連機制（指數退避）+ 重連後對帳補差

### M4：帳務查詢
- [ ] 庫存、未實現 / 已實現損益、買進力、維持率、交割
- [ ] GUI 頁面 + 快取 10~30 秒

### M5：行情
- [ ] REST：報價 / K 線 / 逐筆
- [ ] WebSocket：trades / books 訂閱
- [ ] GUI 行情頁（簡易報價 + Tick）

### M6：期貨選擇權
- [ ] 下單 / 改單 / 刪單 / 未平倉 / 保證金
- [ ] GUI 切換證券 / 期權模式

### M7：條件單
- [ ] 建立 / 查詢 / 取消 OCO、OSO、停損停利
- [ ] 每日 08:30 自動重掛

### M8：正式化
- [ ] PyInstaller 打包單檔 exe/app
- [ ] 憑證到期排程（30 天前警示）
- [ ] Kill Switch、閒置鎖定
- [ ] `mypy --strict` 通過
- [ ] 文件：使用者手冊、疑難排解 FAQ

---

## 8. 非功能需求

| 項目 | 需求 |
| --- | --- |
| 可稽核性 | 所有交易事件必留 trade.log + SQLite；保存 7 年 |
| 可觀測性 | GUI 即時 log；錯誤有 stacktrace；JSON log 可給外部系統 |
| 安全性 | 秘密存 OS keychain；`.pfx` 權限 400；Kill Switch；閒置自動鎖定 |
| 穩定性 | SDK 斷線自動重連；每 5 分鐘健康檢查；每日對帳 |
| 效能 | GUI 保持非阻塞（<100ms 回應）；下單 <500ms 回應 |
| 相容性 | Windows 10+ / macOS 12+ / Ubuntu 22.04+ |

---

## 9. 風險清單

| 風險 | 緩解 |
| --- | --- |
| 本人在本機誤下單 | DRY RUN 預設開啟；下單雙重確認；閒置自動鎖 |
| 憑證過期 | 啟動即檢查；到期前 30 天 banner + 通知 |
| 秘密外洩 | `keyring` + `.gitignore` + pre-commit scan（`gitleaks`）|
| GUI 崩潰導致 log 遺失 | `enqueue=True`（async 寫入）+ flush on exit + SQLite fsync |
| 重複下單（雙擊）| `request_id` 冪等 + 按鈕 3 秒冷卻 + 雙重確認 |
| SDK 跨平台 wheel 缺失 | 先確認官方有對應 OS wheel，否則改走 Windows VM |
| 斷線未偵測 | on_event + 每 60s ping + GUI 紅燈告警 |

---

## 10. 下一步（Action Items）

- [ ] 本人依 [docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md) 完成帳戶、憑證、聲明書、連線測試。
- [ ] （選）申請 API Key，並取得部署機對外 IP 加白名單。
- [ ] `uv init` 建立專案骨架；執行 `uv sync`。
- [ ] 撰寫 `config.py` + `logging_setup.py`，確認 `.env` 可讀、`logs/` 檔案產生。
- [ ] 實作 M1 登入流程，GUI 可顯示「登入成功，帳號 6460-1234567」。
- [ ] 開始 M2 股票下單（先在 DRY RUN 驗證，再以 1 股實單測試）。
