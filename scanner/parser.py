import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from utils.logger import get_logger

logger = get_logger("parser")

def clean_price(price_str: str) -> Optional[float]:
    """將價格字串 (如 NT$12,045 或 $8,200) 轉換為浮點數"""
    if not price_str:
        return None
    # 移除 NT$, $, 元, 空格, 逗點
    digits = re.sub(r"[^\d]", "", price_str)
    if digits:
        try:
            return float(digits)
        except ValueError:
            return None
    return None

def parse_deal_card_text(raw_text: str, href: str, base_url: str = "https://www.google.com") -> Optional[Dict[str, Any]]:
    """
    解析單張 Deal Card 的文字內容。
    範例格式：
    鹿兒島市
    日本
    9月17日週四 — 9月24日週四
    $12,045
    $24,907
    比平時便宜 52%
    中華航空
    直達, 2 小時 5 分鐘, TPE – KOJ
    """
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return None

    full_url = urljoin(base_url, href) if href else ""

    destination = ""
    country = ""
    outbound_date = ""
    return_date = ""
    price = None
    original_price = None
    currency = "TWD"
    discount_info = ""
    airline = ""
    flight_number = ""
    flight_details = ""

    # 1. 尋找所有金額 (例如 NT$12,045, $12,045, 12,045 元)
    price_matches = re.findall(r"(?:NT\$|\$)\s*[\d,]+|[\d,]+\s*元", raw_text)
    cleaned_prices = []
    for p in price_matches:
        num = clean_price(p)
        if num is not None and num > 0:
            cleaned_prices.append(num)

    if cleaned_prices:
        # 第一個通常為優惠售價，第二個為原價/平時價格
        price = cleaned_prices[0]
        if len(cleaned_prices) > 1:
            original_price = cleaned_prices[1]

    # 2. 尋找折扣標籤 (例如：比平時便宜 52%, 便宜 43%, 省下 NT$5,000)
    discount_match = re.search(r"(比平時便宜\s*\d+%(?:\s*以上)?|便宜\s*\d+%|省下\s*[^,\n]+)", raw_text)
    if discount_match:
        discount_info = re.sub(r"\s+", " ", discount_match.group(1)).strip()

    # 3. 尋找日期區間 (例如：9月17日週四 — 9月24日週四 或 2026/10/16 — 2026/10/18 或 10/16 — 10/18)
    date_range_match = re.search(
        r"((?:\d{4}[年/.-])?\d{1,2}[月/.-]\d{1,2}日?(?:\s*[週星][一二三四五六日天])?)\s*[—–\-~至]\s*((?:\d{4}[年/.-])?\d{1,2}[月/.-]\d{1,2}日?(?:\s*[週星][一二三四五六日天])?)",
        raw_text
    )
    if date_range_match:
        outbound_date = date_range_match.group(1).strip()
        return_date = date_range_match.group(2).strip()

    # 4. 只保存卡片實際顯示的航空公司、航班編號與航程資訊。
    airline_match = re.search(r"(中華航空|華航|China Airlines)", raw_text, re.IGNORECASE)
    if airline_match:
        airline = airline_match.group(1)

    flight_number_match = re.search(r"\bCI\s?\d{2,4}\b", raw_text, re.IGNORECASE)
    if flight_number_match:
        flight_number = flight_number_match.group(0)

    for index, line in enumerate(lines):
        if re.match(r"^(?:直達|\d+\s*次轉機|轉機\s*\d+\s*次)", line):
            flight_details = ", ".join(lines[index:index + 3])
            break

    # 5. 判斷目的地與國家
    # 在卡片結構中，前面兩行通常為目的地與國家
    candidate_lines = []
    for line in lines:
        # 排除包含價格、折扣、日期、航程的行
        if any(keyword in line for keyword in ["$", "NT$", "元", "便宜", "直達", "轉機", "中華航空", "華航"]):
            continue
        if re.search(r"\d+[月/-]\d+", line):
            continue
        candidate_lines.append(line)

    if candidate_lines:
        destination = candidate_lines[0]
        if len(candidate_lines) > 1:
            country = candidate_lines[1]
    elif lines:
        destination = lines[0]

    # 如果沒抓到價格，該卡片可能不是有效航班卡片
    if price is None:
        logger.debug(f"未能從卡片文字中擷取到價格: {raw_text[:50]}...")
        return None

    return {
        "destination": destination,
        "country": country,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "price": price,
        "original_price": original_price,
        "currency": currency,
        "discount_info": discount_info,
        "airline": airline,
        "flight_number": flight_number,
        "flight_details": flight_details,
        "source_url": full_url,
        "raw_text": raw_text
    }

def parse_all_cards(cards_data: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """解析多張卡片資料並過濾重複"""
    results = []
    seen = set()

    for item in cards_data:
        raw_text = item.get("text", "")
        href = item.get("href", "")
        parsed = parse_deal_card_text(raw_text, href)
        if not parsed:
            continue

        # 唯一識別鍵: (destination, outbound_date, return_date, price)
        key = (
            parsed["destination"], 
            parsed["outbound_date"], 
            parsed["return_date"], 
            parsed["price"]
        )
        if key not in seen:
            seen.add(key)
            results.append(parsed)

    logger.info(f"成功解析 {len(results)} 筆有效特惠航班結果")
    return results
