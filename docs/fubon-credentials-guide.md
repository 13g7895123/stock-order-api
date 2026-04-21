# 富邦證券 API 串接資訊取得完整指南

> 本文件說明串接 **富邦新一代 API（Fubon Neo API）** 所需的**所有資訊如何取得**，包含帳號、密碼、憑證、API Key / Secret Key、分公司代號、帳號號碼等。
> 建議將本指南交給**實際持有帳戶的本人**操作（因涉及個資、OTP 與金融授權）。

---

## 0. 取得資料總覽（Checklist）

| # | 項目 | 取得場所 | 備註 |
| - | --- | --- | --- |
| 1 | 身分證字號 | 本人 | 登入與所有 API 必要 |
| 2 | 電子交易密碼 | 富邦證券開戶後由券商提供 / 可自行重設 | 登入必要 |
| 3 | 分公司代號（branch_no，4 碼）| 開戶資料 / 富邦 e 點通 | 區分營業據點 |
| 4 | 證券帳號（account，7 碼）| 開戶資料 / 富邦 e 點通 | 下單對象 |
| 5 | 數位憑證 `.pfx` 檔 | TCEM.exe（Windows 工具）| 登入必要 |
| 6 | 憑證密碼 | 申請時自訂 / 預設＝身分證字號 | 登入必要 |
| 7 | API 使用風險暨聲明書 | 線上簽署 | 未簽不可連線 |
| 8 | API Key | 富邦 API 金鑰後台 | 選用；強化安全 |
| 9 | Secret Key | 同上（僅顯示一次）| 選用 |
| 10 | 對外 IP | 伺服器 NAT 出口 IP | 設定 API Key 白名單 |

---

## 1. 開立富邦證券帳戶

若已有帳戶可跳過。

### 管道
- **線上開戶**（推薦）：<https://www.fubon.com/securities/open-now/>
  - 需準備：雙證件、自然人憑證 / 健保卡、銀行存摺封面、手機、Email。
- **臨櫃開戶**：富邦證券各分公司。

### 完成後取得
- **分公司代號**（4 碼，例如 `6460`）
- **證券帳號**（7 碼，例如 `1234567`）
- **電子交易密碼**（用於登入 e 點通、交易 API）
- **網路下單密碼 / 憑證密碼**（可能與交易密碼不同）
- **交割銀行帳號**（富邦銀行 / 台北富邦銀行）

> 👉 這些會顯示在「開戶成功通知信 / 簡訊」與「富邦 e 點通」個人頁。

### 密碼忘記或要重設
- **富邦 e 點通**：<https://ebrokerdj.fbs.com.tw/>（登入頁有「忘記密碼」）
- **客服專線**：**0809-058-888**
- 建議每 3–6 個月輪替交易密碼。

---

## 2. 申請數位憑證（最關鍵步驟）

Fubon Neo API **必須用憑證登入**，無憑證一切免談。

### 2.1 下載憑證 e 總管（TCEM.exe）

- 入口：<https://www.fbs.com.tw/Certificate/Management/>
- 點選「立即執行」下載 `TCEM.exe`（**Windows 專用**）。
- 如果沒有 Windows：可向同事 / 家人借 Windows 電腦一次即可，之後將 `.pfx` 複製走。

### 2.2 申請流程

1. 執行 `TCEM.exe`。
2. 輸入**身分證字號 + 電子交易密碼**登入。
3. 選擇「**申請 / 展期憑證**」。
4. 系統寄送 **OTP** 至登記手機 / 信箱 → 輸入 OTP。
5. 設定憑證密碼：
   - **自訂密碼**：自己記（建議）；
   - **預設密碼**：系統設為身分證字號（僅 SDK v1.3.2+ 支援省略參數）。
6. 申請成功。

### 2.3 憑證位置

- 預設存放：`C:\CAFubon\<身分證字號>\<身分證字號>.pfx`
- 檔名格式：`A123456789.pfx`（以身分證字號為檔名）
- **這個 `.pfx` 就是登入 API 所需檔案。**

### 2.4 轉移到 Linux / macOS

`.pfx` 為 PKCS#12 標準格式，跨平台可用：

```bash
# 範例：將 pfx 放到部署主機的 /secrets/fubon/ 下，權限 400
scp A123456789.pfx user@server:/secrets/fubon/
ssh user@server
chmod 400 /secrets/fubon/A123456789.pfx
chown app:app /secrets/fubon/A123456789.pfx
```

### 2.5 憑證有效期與展期

- **有效期：1 年**。到期後無法登入 API。
- 展期：到期前在 TCEM.exe 執行「展期」。
- 建議**到期前 30 天**主動展期並更新部署環境的 `.pfx`。

### 2.6 檢查憑證有效期（本地指令）

```bash
# Linux / macOS，需要知道憑證密碼
openssl pkcs12 -in A123456789.pfx -nokeys -info -passin pass:你的憑證密碼 \
  | openssl x509 -noout -dates
# 輸出:
# notBefore=Apr 21 00:00:00 2026 GMT
# notAfter =Apr 21 23:59:59 2027 GMT
```

---

## 3. 簽署 API 使用風險暨聲明書 + 連線測試

**沒簽 → 就算憑證沒問題也會被擋下來**。

### 3.1 線上簽署

- 操作說明書：<https://www.fbs.com.tw/wcm/new_web/operate_manual/operate_manual_01/API-SignSOP_guide.pdf>
- 流程：登入富邦 e 點通 → 「數位服務 / API 申請」→ 閱讀並同意 → OTP 認證 → 完成。

### 3.2 連線測試

- 下載：<https://www.fbs.com.tw/TradeAPI_SDK/sample_code/API_Sign_Test.zip>（Windows exe）
- 執行 → 填入身分證 / 密碼 / 憑證路徑 → 點擊「測試」。
- **測試通過後富邦才會開通 API 權限**（約數分鐘）。

> 替代：熟悉 SDK 者可直接用 `sdk.login(...)` 自行測試。但若沒跑過簽署流程會直接在 login 時失敗。

---

## 4. 申請 API Key / Secret Key（選用但強烈建議）

### 4.1 為什麼要用 API Key？

- 可**限制權限**（只下單 / 只行情 / 只查詢）。
- 可設定 **IP 白名單**、**有效期限**。
- 撤銷單把 Key 不影響主帳號，**適合服務化部署**。

### 4.2 申請步驟

1. 進入「金鑰申請及管理」後台：<https://www.fbs.com.tw/TradeAPI/docs/key/>
2. 輸入**身分證 + 電子交易密碼**登入。
3. 點選「**申請網頁版憑證**」→ 收 OTP → 完成。
4. 點選「**新增金鑰**」：
   - 名稱：例如 `prod-trading`、`dev-marketdata`
   - 權限：勾選「行情」/「交易」/「帳務」/「條件單」
   - **IP 白名單**：填入伺服器對外 IP（查法見 §5）
   - 有效期限：建議設 90 天，定期輪替
5. **立即複製 Secret Key**（關閉頁面後永遠看不到）。
6. 至 `.env` 儲存：

   ```env
   FUBON_API_KEY=AAAAAAAAAAAAAAAAAAAA
   FUBON_API_SECRET=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
   ```

### 4.3 注意事項

- 每個身分證最多 **30 把 Key**。
- 權限變更：V2.2.8 以後僅該 Key 的 session 被強制斷線。
- **Secret Key 絕不可入 Git**；建議存放於 Vault / AWS Secrets Manager / 1Password。
- 若 Secret Key 外洩：立即至後台**停用**該 Key，再新增新 Key。

---

## 5. 取得伺服器對外 IP（設定白名單用）

```bash
# 在部署主機執行
curl -s https://api.ipify.org
# 或
curl -s https://ifconfig.me
```

- 若部署在雲端（AWS/GCP/Azure）：建議綁定 **固定 IP / NAT Gateway**，避免 IP 變動導致連線失敗。
- 多機部署：每台主機的對外 IP 都要加入白名單，或統一走一個 NAT。

---

## 6. 查詢分公司代號與帳號號碼

三種方法，選一即可：

### 方法 A：富邦 e 點通
1. 登入 <https://ebrokerdj.fbs.com.tw/>
2. 右上角個人資訊 → 可看到 `6460-1234567`（**前 4 碼為分公司，後 7 碼為帳號**）。

### 方法 B：透過 SDK 取得
```python
from fubon_neo.sdk import FubonSDK
sdk = FubonSDK()
result = sdk.login(personal_id, password, cert_path, cert_password)
for acc in result.data:
    print(acc.branch_no, acc.account, acc.account_type)
```

### 方法 C：紙本
- 開戶證明 / 存摺封面 / 對帳單皆有。

---

## 7. 環境變數與秘密管理建議

### 7.1 `.env.example`（進 Git）

```env
# ==== 富邦登入 ====
FUBON_PERSONAL_ID=A123456789
FUBON_PASSWORD=change_me
FUBON_CERT_PATH=/secrets/fubon/A123456789.pfx
FUBON_CERT_PASSWORD=change_me

# ==== API Key（可選）====
FUBON_API_KEY=
FUBON_API_SECRET=

# ==== 預設使用的帳號 ====
FUBON_BRANCH_NO=6460
FUBON_ACCOUNT_NO=1234567

# ==== 行為參數 ====
FUBON_TIMEOUT_SEC=30
FUBON_RECONNECT_TIMES=2
```

### 7.2 `.env`（**不進 Git**）
在部署機上填入真實值，並設定權限：
```bash
chmod 600 .env
```

### 7.3 Git 忽略
`.gitignore`：
```
.env
*.pfx
*.p12
secrets/
```

### 7.4 正式環境
- **本機 GUI 開發**：`.env` + keyring（macOS Keychain / Windows Credential Manager）
- **伺服器**：HashiCorp Vault / AWS Secrets Manager / Doppler / Infisical
- **容器**：以 Docker Secret 或 Kubernetes Secret 掛載，不寫進 image

---

## 8. 憑證 / 密碼外洩應變流程（SOP）

1. **立即**至金鑰管理後台停用所有 API Key。
2. 登入富邦 e 點通重設**電子交易密碼**。
3. 使用 TCEM.exe **廢止現有憑證**，重新申請新憑證。
4. 更新 `.env`、重新部署服務。
5. 檢視稽核日誌是否有異常下單；若有，立刻聯絡富邦客服 **0809-058-888 / service.sec@fubon.com** 申請撤單。

---

## 9. 測試環境說明

富邦**無公開 sandbox**。可行測試方式：

| 方式 | 說明 | 成本 |
| --- | --- | --- |
| 盤後零股 1 股 | 下 1 股跌停價 Limit 單 → 刪單 | ≒0，最真實 |
| 非交易時段 login | 只驗證登入與憑證 | 0 |
| 改用歷史行情 REST | `historical.candles` 可在任何時間呼叫 | 0 |
| 富邦模擬交易平台 | 富邦「e01 模擬下單」獨立系統，**非 API**，不適合本專案 | 0 |

> 實作時建議加 `FUBON_DRY_RUN=true` 開關，在 Dry Run 模式下只 log 不呼叫 SDK。

---

## 10. 參考連結彙整

| 資源 | 連結 |
| --- | --- |
| TradeAPI 官網 | <https://www.fbs.com.tw/TradeAPI/> |
| 事前準備 | <https://www.fbs.com.tw/TradeAPI/docs/trading/prepare> |
| API Key 申請 | <https://www.fbs.com.tw/TradeAPI/docs/trading/api-key-apply> |
| 金鑰管理後台 | <https://www.fbs.com.tw/TradeAPI/docs/key/> |
| 憑證管理（TCEM 下載）| <https://www.fbs.com.tw/Certificate/Management/> |
| 簽署 SOP | <https://www.fbs.com.tw/wcm/new_web/operate_manual/operate_manual_01/API-SignSOP_guide.pdf> |
| 連線測試小幫手 | <https://www.fbs.com.tw/TradeAPI_SDK/sample_code/API_Sign_Test.zip> |
| SDK 下載 | <https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk> |
| 富邦 e 點通 | <https://ebrokerdj.fbs.com.tw/> |
| 線上開戶 | <https://www.fubon.com/securities/open-now/> |
| 客服信箱 | service.sec@fubon.com |
| 客服專線 | 0809-058-888 |
