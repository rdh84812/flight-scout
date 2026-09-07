"""
Google Flights / Google Flights Deals AI 頁面重要 Selectors 集中管理
若 Google UI 改版，請優先調整此檔案內的 Selectors 與關鍵字。
優先原則：使用 role, aria-label, visible text, placeholder，避免依賴易變更的層級結構。
"""

# 1. 首次進入可能出現的引導彈窗關閉按鈕
MODAL_DISMISS_SELECTORS = [
    'button[aria-label="我知道了"]',
    'button:has-text("我知道了")',
    'button[aria-label="Got it"]',
    'button:has-text("Got it")',
    'div[role="dialog"] button:visible',
]

# 2. Google Flights Deals AI 自然語言搜尋輸入框
AI_INPUT_SELECTORS = [
    'div[role="textbox"][aria-label*="想去旅遊嗎"]',
    'div[role="textbox"][aria-label*="目的地、時間和方式"]',
    'div[role="textbox"][placeholder*="想去旅遊嗎"]',
    'div[role="textbox"]',
    'input[aria-label*="想去旅遊嗎"]',
    'input[placeholder*="想去旅遊嗎"]',
    '[aria-label*="想去旅遊嗎"]',
]

# 3. 搜尋按鈕
SEARCH_BUTTON_SELECTORS = [
    'button[aria-label="搜尋"]',
    'button[aria-label="Search"]',
    'button:has-text("搜尋")',
    'button[type="submit"]',
]

# 4. 搜尋結果卡片 (Deal Card)
# Google Flights Deals 頁面上每一張特惠行程卡片通常是 a.BSkw5b 或包含跳轉航班連結的卡片
DEAL_CARD_SELECTORS = [
    'a.BSkw5b',
    'a[href*="/travel/flights/search"]',
    'a[href*="/travel/flights?tfs="]',
    'a[href*="/travel/flights"]',
]

# 5. 特惠卡片內部特定元素（若直接擷取卡片整段 text 解析失敗時作為輔助）
CARD_DESTINATION_SELECTORS = [
    '.pIav2d',
    '.sSHqwe',
    'h3',
    'div[role="heading"]',
]

CARD_PRICE_SELECTORS = [
    '.YMlIz',
    'span[aria-label*="元"]',
    'span[aria-label*="NT$"]',
]

# 6. 錯誤偵測特徵 (例如出現 Google Sorry 頁面、機器人驗證、流量異常等)
ERROR_URL_PATTERNS = [
    '/sorry/index',
    'google.com/sorry',
]

ERROR_BODY_PATTERNS = [
    '糟糕！系統發生錯誤',
    '我們的系統偵測到您的電腦網路發出異常流量',
    'our systems have detected unusual traffic',
    '請驗證您不是自動程式',
    'verify you are human',
    '存取遭到拒絕',
    'access denied',
]
