import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError

from database.db import finish_scan_run, init_db, save_flight_results, start_scan_run
from scanner.parser import parse_all_cards
from scanner.selectors import (
    AI_INPUT_SELECTORS,
    DEAL_CARD_SELECTORS,
    ERROR_BODY_PATTERNS,
    ERROR_URL_PATTERNS,
    MODAL_DISMISS_SELECTORS,
    SEARCH_BUTTON_SELECTORS,
)
from utils.config import load_config
from utils.logger import get_logger

logger = get_logger("google_flights")

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG_DIR = BASE_DIR / "debug"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# 全域非同步鎖，防止排程與手動 /scan 同時啟動多個瀏覽器實體
_SCAN_LOCK = asyncio.Lock()


def validate_scan_results(results: List[Dict[str, Any]]) -> None:
    """避免把 selector/parser 失效造成的空結果誤記為成功。"""
    if not results:
        raise ValueError("未解析出任何 Google Flights AI 搜尋結果")


def is_retryable_system_error(error_message: Optional[str]) -> bool:
    """只有 Google AI 暫時性系統錯誤可以自動重試一次。"""
    return bool(error_message and "糟糕！系統發生錯誤" in error_message)

async def save_debug_artifacts(page: Optional[Page], reason: str) -> Tuple[str, str]:
    """發生錯誤時儲存螢幕截圖與完整 HTML 供除錯"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = str(DEBUG_DIR / f"{timestamp}.png")
    html_path = str(DEBUG_DIR / f"{timestamp}.html")

    if page:
        try:
            await page.screenshot(path=screenshot_path, full_page=True)
            logger.info(f"已儲存除錯截圖: {screenshot_path}")
        except Exception as e:
            logger.warning(f"儲存除錯截圖失敗: {e}")
            screenshot_path = ""

        try:
            content = await page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"已儲存除錯 HTML: {html_path}")
        except Exception as e:
            logger.warning(f"儲存除錯 HTML 失敗: {e}")
            html_path = ""
    return screenshot_path, html_path

async def check_for_page_errors(page: Page) -> Optional[str]:
    """檢測頁面是否有 CAPTCHA、阻擋或異常流量提示"""
    try:
        current_url = page.url.lower()
        for pattern in ERROR_URL_PATTERNS:
            if pattern in current_url:
                return f"Google 重新導向至阻擋頁面: {current_url}"

        title = (await page.title()).lower()
        if "sorry" in title or "unusual traffic" in title:
            return f"偵測到異常流量阻擋頁面 (標題: {title})"

        body_text = await page.locator("body").inner_text()
        for pattern in ERROR_BODY_PATTERNS:
            if pattern.lower() in body_text.lower():
                return f"偵測到 Google 安全驗證特徵: '{pattern}'"
    except Exception as e:
        logger.debug(f"檢測頁面異常時發生錯誤: {e}")
    return None


async def retry_search_on_system_error(
    page: Page,
    error_message: Optional[str],
    delay_ms: int,
) -> Tuple[Optional[str], bool]:
    """遇到 Google AI 系統錯誤時等待後重新按一次搜尋。"""
    if not is_retryable_system_error(error_message):
        return error_message, False

    logger.warning("Google AI 回傳系統錯誤，等待後重新按一次搜尋")
    if delay_ms > 0:
        await page.wait_for_timeout(delay_ms)
    if not await submit_search(page):
        return "Google AI 系統錯誤後重新搜尋失敗", True

    await page.wait_for_timeout(3000)
    return await check_for_page_errors(page), True

async def dismiss_modals(page: Page) -> None:
    """如果出現『我知道了』或歡迎導覽彈窗，自動點擊關閉"""
    for selector in MODAL_DISMISS_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=1500):
                logger.info(f"發現導覽彈窗，點擊關閉: {selector}")
                await locator.click()
                await page.wait_for_timeout(500)
                break
        except Exception:
            continue

async def enter_ai_prompt(page: Page, prompt: str) -> bool:
    """在 AI 自然語言搜尋欄輸入搜尋語句"""
    for selector in AI_INPUT_SELECTORS:
        try:
            input_el = page.locator(selector).first
            if await input_el.is_visible(timeout=2000):
                logger.info(f"找到 AI 搜尋框: {selector}")
                await input_el.click(timeout=5000)
                await page.wait_for_timeout(300)

                # fill() 同時支援 input、textarea 與 contenteditable，並會觸發 input 事件。
                await input_el.fill(prompt, timeout=5000)
                await page.wait_for_timeout(300)

                tag_name = await input_el.evaluate("el => el.tagName")
                if tag_name in {"INPUT", "TEXTAREA"}:
                    text = await input_el.input_value()
                else:
                    text = await input_el.inner_text()
                if text.strip() != prompt:
                    logger.warning(
                        f"AI 搜尋框輸入驗證失敗: selector={selector}, actual={text!r}"
                    )
                    continue
                logger.info(f"成功輸入 AI Prompt: '{prompt}'")
                return True
        except Exception as e:
            logger.debug(f"嘗試 selector '{selector}' 失敗: {e}")
            continue

    return False

async def submit_search(page: Page) -> bool:
    """點擊搜尋按鈕或按下 Enter 送出搜尋"""
    for selector in SEARCH_BUTTON_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                logger.info(f"點擊搜尋按鈕: {selector}")
                await btn.click()
                return True
        except Exception:
            continue

    # Fallback: 按下 Enter 鍵
    try:
        logger.info("未找到搜尋按鈕，嘗試按 Enter 送出搜尋")
        await page.keyboard.press("Enter")
        return True
    except Exception as e:
        logger.warning(f"按 Enter 送出失敗: {e}")
        return False

async def extract_deal_cards(page: Page, timeout_ms: int = 20000) -> List[Dict[str, str]]:
    """等待特惠卡片載入並擷取所有卡片內容"""
    card_selector = DEAL_CARD_SELECTORS[0]
    logger.info("等待 Google Flights AI 搜尋結果卡片載入...")

    # 等待第一張卡片出現
    try:
        await page.wait_for_selector(card_selector, timeout=timeout_ms, state="visible")
    except PlaywrightTimeoutError:
        logger.warning(f"在 {timeout_ms}ms 內未等到 {card_selector}，嘗試其他 card selectors")
        for alt_selector in DEAL_CARD_SELECTORS[1:]:
            try:
                await page.wait_for_selector(alt_selector, timeout=3000, state="visible")
                card_selector = alt_selector
                break
            except Exception:
                continue

    # 捲到底並等待卡片數量與頁面高度穩定，避免漏掉 lazy-loaded 結果。
    try:
        stable_rounds = 0
        previous_count = -1
        previous_height = -1
        for _ in range(20):
            current_count = await page.locator(card_selector).count()
            current_height = await page.evaluate("document.documentElement.scrollHeight")
            await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            await page.wait_for_timeout(800)
            new_count = await page.locator(card_selector).count()
            new_height = await page.evaluate("document.documentElement.scrollHeight")
            if (
                new_count == current_count == previous_count
                and new_height == current_height == previous_height
            ):
                stable_rounds += 1
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0
            previous_count = new_count
            previous_height = new_height
    except Exception as error:
        logger.debug(f"捲動載入更多結果時發生錯誤: {error}")

    # 擷取所有卡片節點的 text 與 href
    cards_data = []
    cards = page.locator(card_selector)
    count = await cards.count()
    logger.info(f"畫面上找到 {count} 個特惠卡片節點")

    for i in range(count):
        card = cards.nth(i)
        try:
            text = await card.inner_text()
            href = await card.get_attribute("href") or ""
            if text.strip():
                cards_data.append({"text": text, "href": href})
        except Exception as e:
            logger.debug(f"擷取第 {i} 張卡片時出錯: {e}")

    return cards_data

async def run_google_flights_scan(
    force_headless: Optional[bool] = None,
    custom_prompt: Optional[str] = None
) -> Dict[str, Any]:
    """
    執行一次 Google Flights AI 搜尋與 SQLite 同步。
    若已有其他掃描正在進行，將立即回傳 is_locked=True。
    """
    if _SCAN_LOCK.locked():
        logger.warning("已有掃描正在進行中，略過本次請求")
        return {
            "success": False,
            "is_locked": True,
            "error": "Scanner is already running.",
            "results": [],
            "items_to_notify": []
        }

    async with _SCAN_LOCK:
        config = load_config()
        prompt = custom_prompt or config["google_flights"]["prompt"]
        url = config["google_flights"]["url"]
        timeout_ms = config["google_flights"]["timeout_ms"]
        modal_appearance_delay_ms = config["google_flights"].get(
            "modal_appearance_delay_ms", 3000
        )
        before_prompt_delay_ms = config["google_flights"].get(
            "before_prompt_delay_ms", 3000
        )
        submit_delay_ms = config["google_flights"].get("submit_delay_ms", 1500)
        min_price_drop = config.get("notification", {}).get("min_price_drop", 0.0)

        headless = force_headless if force_headless is not None else config["browser"]["headless"]
        slow_mo = config["browser"].get("slow_mo", 50)
        viewport = config["browser"].get("viewport", {"width": 1280, "height": 900})

        # 初始化資料庫
        init_db()
        scan_id = start_scan_run()
        logger.info(f"=== 開始 Google Flights AI 掃描 (Scan ID: {scan_id}) ===")
        logger.info(f"搜尋語句: '{prompt}', Headless: {headless}")

        browser: Optional[Browser] = None
        page: Optional[Page] = None
        screenshot_path = ""
        html_path = ""

        try:
            async with async_playwright() as p:
                logger.info("正在啟動 Playwright Chromium 瀏覽器...")
                browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
                context = await browser.new_context(
                    viewport=viewport,
                    locale="zh-TW",
                    timezone_id="Asia/Taipei",
                )
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)

                logger.info(f"導航至 Google Flights 頁面: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightTimeoutError as error:
                    err = f"Google Flights 頁面載入逾時: {error}"
                    screenshot_path, html_path = await save_debug_artifacts(page, err)
                    finish_scan_run(scan_id, "failed", 0, err)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": [],
                    }
                # 檢測是否有阻擋
                err_msg = await check_for_page_errors(page)
                if err_msg:
                    screenshot_path, html_path = await save_debug_artifacts(page, err_msg)
                    finish_scan_run(scan_id, "failed", 0, err_msg)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err_msg,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": []
                    }

                # 導覽彈窗可能在 DOMContentLoaded 後才出現，先等待再關閉。
                if modal_appearance_delay_ms > 0:
                    logger.info(
                        f"頁面已開啟，等待 {modal_appearance_delay_ms}ms 讓導覽彈窗載入"
                    )
                    await page.wait_for_timeout(modal_appearance_delay_ms)

                # 關閉彈窗導覽
                await dismiss_modals(page)

                # 將等待放在關閉彈窗之後，確保使用者能清楚看到頁面穩定後才輸入。
                if before_prompt_delay_ms > 0:
                    logger.info(
                        f"Google Flights 頁面與導覽彈窗處理完成，等待 {before_prompt_delay_ms}ms 後輸入 Prompt"
                    )
                    await page.wait_for_timeout(before_prompt_delay_ms)

                # 輸入 Prompt
                input_ok = await enter_ai_prompt(page, prompt)
                if not input_ok:
                    err = "找不到 AI 搜尋欄 (AI search box not found)"
                    logger.error(err)
                    screenshot_path, html_path = await save_debug_artifacts(page, err)
                    finish_scan_run(scan_id, "failed", 0, err)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": []
                    }

                if submit_delay_ms > 0:
                    logger.info(
                        f"Prompt 輸入完成，等待 {submit_delay_ms}ms 後送出搜尋"
                    )
                    await page.wait_for_timeout(submit_delay_ms)

                # 送出搜尋
                if not await submit_search(page):
                    err = "無法送出 Google Flights AI 搜尋"
                    screenshot_path, html_path = await save_debug_artifacts(page, err)
                    finish_scan_run(scan_id, "failed", 0, err)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": [],
                    }
                await page.wait_for_timeout(3000)

                err_msg = await check_for_page_errors(page)
                err_msg, _ = await retry_search_on_system_error(
                    page, err_msg, submit_delay_ms
                )
                if err_msg:
                    screenshot_path, html_path = await save_debug_artifacts(page, err_msg)
                    finish_scan_run(scan_id, "failed", 0, err_msg)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err_msg,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": [],
                    }

                # 擷取特惠卡片
                cards_data = await extract_deal_cards(page, timeout_ms=timeout_ms)

                # Google 的系統錯誤有時會在送出後延遲出現，因此擷取後再檢查一次。
                err_msg = await check_for_page_errors(page)
                err_msg, retried = await retry_search_on_system_error(
                    page, err_msg, submit_delay_ms
                )
                if err_msg:
                    screenshot_path, html_path = await save_debug_artifacts(page, err_msg)
                    finish_scan_run(scan_id, "failed", 0, err_msg)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err_msg,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": [],
                    }
                if retried:
                    cards_data = await extract_deal_cards(page, timeout_ms=timeout_ms)

                # 解析結果
                parsed_results = parse_all_cards(cards_data)
                try:
                    validate_scan_results(parsed_results)
                except ValueError as error:
                    err = str(error)
                    screenshot_path, html_path = await save_debug_artifacts(page, err)
                    finish_scan_run(scan_id, "failed", 0, err)
                    return {
                        "success": False,
                        "scan_id": scan_id,
                        "error": err,
                        "screenshot": screenshot_path,
                        "html": html_path,
                        "results": [],
                        "items_to_notify": [],
                    }
                result_count = len(parsed_results)

                # 儲存至 SQLite 並計算 diff
                saved_results, items_to_notify = save_flight_results(
                    scan_id=scan_id,
                    results=parsed_results,
                    min_price_drop=min_price_drop
                )

                result_count = len(saved_results)
                finish_scan_run(scan_id, "success", result_count, error=None)
                logger.info(
                    f"=== 掃描完成 (Scan ID: {scan_id}) === "
                    f"共找到 {result_count} 筆，其中 {len(items_to_notify)} 筆需通知"
                )

                return {
                    "success": True,
                    "scan_id": scan_id,
                    "error": None,
                    "screenshot": screenshot_path,
                    "html": html_path,
                    "results": saved_results,
                    "items_to_notify": items_to_notify
                }

        except PlaywrightTimeoutError as te:
            err = f"Google Flights 操作逾時 (Timeout): {te}"
            logger.error(err)
            screenshot_path, html_path = await save_debug_artifacts(page, err)
            finish_scan_run(scan_id, "failed", 0, err)
            return {
                "success": False,
                "scan_id": scan_id,
                "error": err,
                "screenshot": screenshot_path,
                "html": html_path,
                "results": [],
                "items_to_notify": []
            }
        except Exception as e:
            err = f"掃描過程發生未預期錯誤: {e}"
            logger.error(err, exc_info=True)
            screenshot_path, html_path = await save_debug_artifacts(page, err)
            finish_scan_run(scan_id, "failed", 0, err)
            return {
                "success": False,
                "scan_id": scan_id,
                "error": err,
                "screenshot": screenshot_path,
                "html": html_path,
                "results": [],
                "items_to_notify": []
            }
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
