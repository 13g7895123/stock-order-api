# stock-order-api

富邦 Neo API 本機帳務模組：**登入 → 歸戶 → 六大帳務查詢**（庫存 / 未實現 / 已實現 / 買進力 / 交割 / 維持率），附 CLI、PySide6 GUI、完整日誌與 SQLite 稽核。另含 **WebSocket 即時行情**（5 channels × 多連線分片 × 自動重連）。

> 本倉庫目前專注於「帳務查詢」與「即時行情」，下單 / 條件單 / 期權於後續計畫處理。
>
> 規劃文件：[plan.md](plan.md) · [plan-account.md](plan-account.md) · [plan-realtime.md](plan-realtime.md) · 憑證：[docs/fubon-credentials-guide.md](docs/fubon-credentials-guide.md)

---

## 快速開始

```bash
# 1) 安裝 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) 同步依賴
uv sync

# 3) 安裝富邦 SDK（非 PyPI）
#    從 https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk 下載對應平台 wheel
uv add ./fubon_neo-<version>-<plat>.whl

# 4) 準備憑證與設定
cp .env.example .env          # 編輯填入身分證 / 密碼 / 憑證路徑…
mkdir -p secrets && chmod 700 secrets
cp /path/to/A123456789.pfx secrets/

# 5) 登入驗證
uv run stock-order-account login
```

## CLI

```bash
uv run stock-order-account login
uv run stock-order-account inventories
uv run stock-order-account unrealized
uv run stock-order-account realized --from 2026-03-01 --to 2026-04-21
uv run stock-order-account buying-power
uv run stock-order-account settlements
uv run stock-order-account maintenance

# 通用參數
--account 6460-1234567    # 指定歸戶帳號
--output table|json|csv   # 輸出格式
--no-cache                # 強制直打 API
```

## GUI

```bash
uv run stock-order-gui            # 或 uv run python -m stock_order_api
```

五個 Tab：庫存 / 未實現 / 已實現 / 現金&交割 / 維持率；支援非阻塞查詢、30 秒自動刷新、CSV 匯出、帳號切換清 cache。

## 即時行情（WebSocket）

```bash
# 持續訂閱成交資訊（Ctrl-C 結束；table 模式 rich live 更新）
uv run stock-order-quote watch trades 2330 2317 2454

# 同時訂閱成交與五檔，輸出 JSON Lines
uv run stock-order-quote watch trades,books 2330 --output jsonl

# 輸出 CSV（結束時寫入 exports/）
uv run stock-order-quote watch trades 2330 --output csv --duration 30

# 快照一筆（等最多 3 秒）
uv run stock-order-quote snapshot 2330 --wait 3

# Normal 模式才能訂閱 candles / aggregates
uv run stock-order-quote watch candles 2330 --mode normal
```

GUI 的「即時行情」Tab 提供報價表 + 五檔深度；切 mode 下拉可於 speed ↔ normal 之間切換（會自動關舊連線重建）。

限制摘要（官方）：

| 項目 | 上限 |
| --- | --- |
| 單一 WebSocket 連線 | 200 訂閱 |
| 同帳號最大連線數 | 5 條 |
| 理論天花板 | 1000 個 (channel × symbol) |

`SubscriptionManager` 會自動分片；超過上限會 raise `SubscriptionLimitError`。

## 開發

```bash
uv run pytest            # 單元測試
uv run ruff check .      # Lint
uv run mypy src          # Type check
```

## 輸出路徑

| 路徑 | 說明 |
| --- | --- |
| `logs/app.log`        | 人類可讀主日誌（每日輪替） |
| `logs/app.jsonl`      | JSON 結構化日誌 |
| `logs/audit.log`      | 稽核事件（每筆登入/查詢） |
| `logs/error.log`      | 錯誤獨立檔 |
| `logs/audit.sqlite3`  | `audit_events` / `cache_entries` / `snapshots` |
| `exports/*.csv`       | GUI / CLI 匯出資料 |
| `STATS` log event     | 每 `FUBON_REALTIME_STATS_INTERVAL` 秒一筆，含每 channel 的 msg/s 與 p50/p95 延遲 |

## 安全

- `.pfx` 與 `.env` 均列入 `.gitignore`，`secrets/` 權限 700。
- 建議將 `password` / `cert_password` 存入 OS keychain（GUI 登入對話框提供選項）。
- 若使用 API Key，請務必於後台設定 IP 白名單。

## 疑難排解

| 症狀 | 可能原因 / 解法 |
| --- | --- |
| `FubonSDKUnavailableError` | 尚未安裝 `fubon-neo` wheel；見「快速開始」第 3 步 |
| `CertificateError: 無法解析憑證` | 憑證密碼錯誤或 `.pfx` 損毀 |
| `FubonLoginError: 憑證已過期` | 使用 TCEM.exe 展期 |
| 歸戶後抓不到指定帳號 | 調整 `.env` 的 `FUBON_BRANCH_NO` / `FUBON_ACCOUNT_NO` 或改用 `--account 6460-xxxxxxx` |
| `maintenance` 回 `None` | 帳號未開通融資融券 |
| `ChannelNotAllowedError: Speed 模式不支援 channel=candles` | 改用 `--mode normal` 或訂閱 `trades/books/indices` |
| `SubscriptionLimitError` | 超過 200 訂閱/連線 或 5 連線上限；減少商品或拆程序 |
| `WebSocketConnectionClosedException` | 盤外或網路短線；`RealtimeClient` 會自動指數退避重連（預設最多 5 次） |

---

© Jarvis — 僅供個人使用，非投資建議。
