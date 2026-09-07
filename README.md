# Google Flights AI 華航低價機票監控與 Discord Bot

專為自動追蹤 **Google Flights AI 自然語言搜尋** 設計的自動化爬蟲與 Discord 通知機器人。

每日自動操作真實瀏覽器進入 Google Flights AI 特惠搜尋，輸入自訂語句（預設：`華航，飛任何地方，桃園機場出發`），擷取結果存入 SQLite 比對，輸出公開 JSON 並由 GitHub Actions 發布成 GitHub Pages。Discord 只發送摘要與完整報告網址，避免大量 Embed 洗版。

---

## 🌟 核心特色

- **完全交由 Google Flights AI 自然語言搜尋**：不用自己維護繁瑣的目的地清單或日期矩陣，搜尋條件集中於 `config.yaml`。
- **即時比對與防止洗版 (Smart Diff & Deduplication)**：已推播過且票價未變動的行程不會重複洗版，僅在「新出現航點」或「相同行程顯著降價」時推播。
- **完整的 Discord Bot & Slash Commands**：
  - `/scan`：立即觸發一次即時搜尋（內建非同步鎖防重複執行）。
  - `/latest`：列出最新一次掃描找到的特惠行程（最低價優先）。
  - `/new`：列出最新一輪相較前次「新增或降價」的票價。
  - `/history`：檢視最近幾次爬蟲掃描歷史紀錄與筆數。
  - `/status`：查看 Bot 連線狀態、上次執行時間、搜到筆數與健康狀態。
  - `/help`：顯示可用指令說明。
- **嚴謹的錯誤處理與除錯機制**：若遇到頁面改版、載入逾時或安全驗證，自動保存除錯截圖（`debug/*.png`）與網頁原始碼（`debug/*.html`），並向 Discord 發送警報。
- **集中管理 Selectors**：所有 CSS / Role / Aria-Label 選取器集中於 `scanner/selectors.py`，未來 Google 改版維護極為容易。
- **精美的當日航班網頁**：支援目的地搜尋、國家與狀態篩選、價格／折扣排序、手機版與深色模式。
- **方案 B 發布流程**：Windows 本機負責 Google Flights 掃描與 JSON push；GitHub Actions 只建置及發布靜態網站，不在雲端嘗試操作 Google 或繞過安全驗證。

---

## 📁 專案結構

```
flight-scout/
├── main.py                    # 專案統一進入點 (支援 --scan, --debug)
├── config.yaml                # 搜尋語句、排程、瀏覽器參數設定
├── .env.example               # 環境變數範本 (DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID)
├── .gitignore                 # Git 忽略設定
├── requirements.txt           # Python 套件相依清單
├── README.md                  # 專案使用與建置說明文件
│
├── scanner/
│   ├── __init__.py
│   ├── selectors.py           # Google Flights UI 選取器集中管理
│   ├── google_flights.py      # Playwright 瀏覽器自動化核心邏輯 (Lock 控制、錯誤截圖)
│   └── parser.py              # Deal Cards 解析器（只保存 Google 卡片實際顯示的欄位）
│
├── database/
│   ├── __init__.py
│   └── db.py                  # SQLite 資料庫操作、歷史紀錄、去重比對邏輯
│
├── discord_bot/
│   ├── __init__.py
│   ├── bot.py                 # Discord.py Client 與 Slash Commands 實作
│   └── embeds.py              # Discord Rich Embed 訊息排版模組
│
├── utils/
│   ├── __init__.py
│   ├── config.py              # YAML 設定檔與環境變數載入工具
│   └── logger.py              # 日誌記錄器 (Console UTF-8 與 logs/scanner.log)
│
├── tests/
│   └── test_components.py     # 單元測試與整合測試
├── reports/
│   ├── generator.py           # 只輸出可公開的航班欄位
│   ├── publisher.py           # 僅 commit/push reports/data/*.json
│   └── data/                  # latest.json 與每日 JSON 快照
├── web/                       # GitHub Pages HTML/CSS/JavaScript
├── scripts/build_site.py      # GitHub Actions 建站指令
├── .github/workflows/pages.yml
│
├── data/                      # 存放 SQLite flights.db
├── logs/                      # 存放 scanner.log
└── debug/                     # 錯誤時自動存放截圖 (.png) 與 HTML (.html)
```

---

## 🛠️ Windows 環境安裝教學

### 步驟 1：建立並啟用 Python 虛擬環境

開啟 PowerShell 或 CMD，進入專案目錄：

```powershell
python3 -m venv .venv
.venv\Scripts\activate
```

### 步驟 2：安裝相依套件

```powershell
python3 -m pip install -r requirements.txt
```

### 步驟 3：安裝 Playwright 瀏覽器核心

```powershell
python3 -m playwright install chromium
```

### 步驟 4：設定環境變數 (.env)

複製 `.env.example` 為 `.env`：

```powershell
Copy-Item .env.example .env
```

使用記事本或編輯器打開 `.env`，填入您的 Discord Bot Token 與 Channel ID：

```env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=123456789012345678
```

*(詳細 Discord Bot 建立與 Token 取得流程請參閱下方教學)*

---

## 🤖 Discord Bot 建立與設定教學

若您尚未建立 Discord Bot，請按照以下 10 個步驟快速完成：

### 1. 前往 Discord 開發者入口
開啟瀏覽器前往 [Discord Developer Portal](https://discord.com/developers/applications) 並登入您的 Discord 帳號。

### 2. 建立應用程式 (Create Application)
點擊右上角 **「New Application」**，輸入名稱（例如 `China Airlines Scanner`），並確認建立。

### 3. 建立 Bot 並取得 Token
1. 點擊左側選單的 **「Bot」**。
2. 點擊 **「Reset Token」**（重設金鑰）。
3. 複製產生的金鑰字串（這就是 `DISCORD_BOT_TOKEN`）。
4. ⚠️ **注意**：切勿將 Token 分享給他人或上傳至 GitHub。

### 4. 設定 Privileged Gateway Intents
在 **「Bot」** 頁面往下滑動到 **「Privileged Gateway Intents」**：
- **Message Content Intent**：❌ **不需要開啟**（本 Bot 使用 Discord 官方 Slash Commands，不需要讀取用戶普通文字訊息內容）。
- **Server Members Intent**：❌ **不需要開啟**。
- **Presence Intent**：❌ **不需要開啟**。

### 5. 設定 Bot 權限與產生邀請連結 (OAuth2)
1. 點擊左側選單的 **「OAuth2」** -> **「URL Generator」**。
2. 在 **SCOPES** 區塊勾選：
   - `bot`
   - `applications.commands` (啟用 Slash Commands 必選)
3. 在下方 **BOT PERMISSIONS** 區塊勾選：
   - `Send Messages` (發送訊息)
   - `Embed Links` (嵌入連結/卡片)
   - `Attach Files` (上傳附件)
   - `Read Messages/View Channels` (查看頻道)
4. 複製最下方產生的 **Generated URL**。

### 6. 將 Bot 邀請加入您的 Discord 伺服器
將複製的 URL 貼到瀏覽器網址列，選擇您要加入的 Discord 伺服器，點擊「授權」。

### 7. 取得頻道 ID (DISCORD_CHANNEL_ID)
1. 在 Discord 應用程式中，點擊左下角「使用者設定 (齒輪)」->「進階 (Advanced)」-> 開啟 **「開發者模式 (Developer Mode)」**。
2. 回到伺服器，對想要接收機票通知的頻道按右鍵，點擊 **「複製頻道 ID」**。
3. 將 ID 貼入 `.env` 中的 `DISCORD_CHANNEL_ID`。

---

## 🚀 執行模式說明

### 1. 完整常駐模式 (Discord Bot + 每日自動排程)

```powershell
python3 main.py
```
- 連線至 Discord，自動註冊 `/scan`、`/latest`、`/new` 等 Slash Commands。
- 背景 APScheduler 會依據 `config.yaml` 中的時間（預設每天 07:00 Asia/Taipei）自動執行 Google Flights AI 搜尋。
- 若有新航點或降價，自動發送 Embed 訊息到指定頻道。
- 每次成功掃描會更新 `reports/data/latest.json` 與當日 JSON；`reports.auto_publish` 開啟時會自動 commit/push，觸發 GitHub Pages 發布。

### 2. 單獨執行掃描 (CLI 測試)

無需連線 Discord，直接在本機執行一次完整搜尋並將結果寫入 SQLite 資料庫：

```powershell
python3 main.py --scan
```

輸出範例：
```text
==================================================
✅ 掃描成功！總共擷取到 91 筆特惠航點
🔥 新增或降價需通知項目: 71 筆
==================================================
[01] 鹿兒島市 (日本) | NT$12,045 (原價: NT$24,907) | 比平時便宜 52% | 9月17日週四 → 9月24日週四 | 直達
[02] 鳳凰城 (美國) | NT$26,208 (原價: NT$51,279) | 比平時便宜 49% | 9月18日週五 → 9月27日週日 | 直達
[03] 北京 (中國) | NT$11,716 (原價: NT$21,295) | 比平時便宜 45% | 9月27日週日 → 10月3日週六 | 直達
...
```

### 3. 可視化除錯掃描 (Debug Mode)

強制彈出 Chromium 瀏覽器視窗，讓您肉眼親眼觀察瀏覽器自動開啟 Google Flights、填入搜尋語句與載入結果的過程：

```powershell
python3 main.py --debug
```

---

## ⚙️ 設定檔說明 (`config.yaml`)

修改搜尋語句或排程時間時，**只需編輯 `config.yaml`，完全不需要修改 Python 程式碼**：

```yaml
# Google Flights AI 搜尋設定
google_flights:
  prompt: "華航，飛任何地方，桃園機場出發"       # 搜尋文字
  modal_appearance_delay_ms: 3000                  # 先等延遲出現的導覽彈窗
  before_prompt_delay_ms: 3000                     # 關閉彈窗後等待再輸入
  submit_delay_ms: 1500                             # 輸入後等待再送出
  url: "https://www.google.com/travel/flights/deals?hl=zh-TW"
  timeout_ms: 30000

# 瀏覽器設定
browser:
  headless: false   # 開發初期預設 false；日後長期背景運行可改為 true
  slow_mo: 50       # 動作間隔毫秒

# 自動排程設定
schedule:
  enabled: true
  hour: 7           # 每天早上 7 點
  minute: 0
  timezone: "Asia/Taipei"

# 價格變化通知門檻
notification:
  min_price_drop: 100   # 相同行程價格下降超過 NT$100 時才發送降價通知

reports:
  enabled: true
  auto_publish: true
  site_url: "https://rdh84812.github.io/flight-scout/"
```

---

## 🌐 GitHub Pages（方案 B）

發布鏈路：

```text
Windows Playwright 掃描 → SQLite → reports/data/*.json → git push
→ GitHub Actions 建置 _site → GitHub Pages → Discord 網頁連結
```

Repository 的 **Settings → Pages → Build and deployment** 必須選擇 **GitHub Actions**。Workflow 位於 `.github/workflows/pages.yml`，也可在 Actions 頁面手動執行。

公開 JSON 不包含 `.env`、Discord credentials、SQLite ID、`result_key` 或 Google 頁面的 `raw_text`；`.env`、資料庫、logs、debug artifacts 與 `_site/` 均不會被 commit。

若不希望掃描程式自動執行 Git commit/push，可將 `config.yaml` 的 `reports.auto_publish` 改為 `false`。網站本機建置驗證指令：

```powershell
python3 scripts/build_site.py
```

輸出位於 `_site/`，這是暫存 build artifact，不納入 Git。

---

## 🔧 未來維護：若 Google 改版時該修改哪裡？

本專案遵循關注點分離原則，所有與網頁元素定位相關的 Selectors 均**集中管理於 `scanner/selectors.py`**。

如果未來 Google 調整介面：
1. **輸入框改版**：
   開啟 `scanner/selectors.py`，調整 `AI_INPUT_SELECTORS` 列表（優先加入新的 `aria-label`、`placeholder` 或 `role`）。
2. **卡片結構改版**：
   調整 `DEAL_CARD_SELECTORS`（例如卡片的 class 名稱）。
3. **引導彈窗變更**：
   調整 `MODAL_DISMISS_SELECTORS`。

### 錯誤診斷檔案
若爬蟲遇到異常（如找不到欄位、逾時），程式會自動在 `debug/` 目錄建立除錯紀錄：
- `debug/YYYYMMDD_HHMMSS.png`：發生錯誤當下的整頁截圖。
- `debug/YYYYMMDD_HHMMSS.html`：發生錯誤當下的完整網頁 DOM。
直接檢視這兩個檔案即可立即確認 Google 介面的變動狀況！

---

## 🧪 測試執行

執行單元測試與元件驗證（測試使用暫存 SQLite，不會污染 `data/flights.db`）：

```powershell
python3 tests/test_components.py
```

這些測試會驗證 parser、SQLite diff/去重、連線關閉、Embed、Slash Command
註冊與 Discord 分批發送邏輯。它們不會登入 Discord，也不會連線 Google。
要驗證目前 Google Flights AI 介面與 Playwright 整合，請另外執行：

```powershell
python3 main.py --debug
```

Google 的結果卡片目前通常只顯示直達/轉機、時間與航線，不一定顯示航空公司或
航班編號；程式對未顯示的欄位會保留空白，不會自行推定。
