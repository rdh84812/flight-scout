import argparse
import asyncio
import sys
from typing import Optional

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from database.db import init_db
from discord_bot.bot import bot, execute_scan_and_notify
from reports.service import create_scan_report
from scanner.google_flights import run_google_flights_scan
from utils.config import get_env_var, load_config
from utils.logger import get_logger

logger = get_logger("main")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Google Flights AI 華航低價機票監控與 Discord Bot"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="單獨執行一次 Google Flights scan 並更新資料庫後結束 (依 config 設定決定是否 headless)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="以可視化瀏覽器 (headless=False) 單獨執行一次 scan，方便肉眼確認操作"
    )
    return parser.parse_args()

async def run_single_scan(force_headless: Optional[bool] = None):
    """CLI 單獨執行一次掃描"""
    logger.info("=== CLI 模式: 執行單次掃描 ===")
    try:
        res = await run_google_flights_scan(force_headless=force_headless)
    except Exception as error:
        logger.error(f"CLI 掃描無法啟動: {error}", exc_info=True)
        res = {
            "success": False,
            "error": f"掃描初始化失敗: {error}",
            "results": [],
            "items_to_notify": [],
        }
    if res.get("success"):
        try:
            report_status = await asyncio.to_thread(create_scan_report, res, load_config())
            res["report"] = report_status
            if report_status.get("published"):
                logger.info(f"航班網頁資料已推送: {report_status.get('site_url', '')}")
                logger.info("網站部署結果請查看 GitHub Actions；資料推送成功不代表網站已上線。")
            elif report_status.get("error"):
                logger.error(f"航班報告已保留於本機，但發布失敗: {report_status['error']}")
        except Exception as error:
            logger.error(f"產生或發布航班網頁失敗: {error}", exc_info=True)
            res["report"] = {"generated": False, "published": False, "error": str(error)}
        results = res.get("results", [])
        new_items = res.get("items_to_notify", [])
        print("\n" + "=" * 50)
        print(f"✅ 掃描成功！總共擷取到 {len(results)} 筆特惠航點")
        print(f"🔥 新增或降價需通知項目: {len(new_items)} 筆")
        print("=" * 50)
        # 列出前 10 筆最低價
        for idx, item in enumerate(results[:10], start=1):
            dest = item.get("destination", "")
            country = item.get("country", "")
            price = item.get("price", 0)
            orig = item.get("original_price")
            orig_str = f" (原價: NT${int(orig):,})" if orig else ""
            disc = item.get("discount_info", "")
            dates = f"{item.get('outbound_date')} → {item.get('return_date')}"
            flight = item.get("flight_number", "")
            print(f"[{idx:02d}] {dest} ({country}) | NT${int(price):,}{orig_str} | {disc} | {dates} | {flight}")
        print("=" * 50 + "\n")
    else:
        err = res.get("error", "未知錯誤")
        screenshot = res.get("screenshot")
        print("\n" + "!" * 50)
        print(f"❌ 掃描失敗: {err}")
        if screenshot:
            print(f"📷 除錯截圖已儲存於: {screenshot}")
        print("!" * 50 + "\n")

def setup_scheduler() -> AsyncIOScheduler:
    """初始化並啟動 APScheduler"""
    config = load_config()
    sched_cfg = config.get("schedule", {})
    scheduler = AsyncIOScheduler()

    if sched_cfg.get("enabled", True):
        hour = sched_cfg.get("hour", 7)
        minute = sched_cfg.get("minute", 0)
        tz_str = sched_cfg.get("timezone", "Asia/Taipei")
        try:
            tz = pytz.timezone(tz_str)
        except Exception:
            tz = pytz.timezone("Asia/Taipei")

        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
        scheduler.add_job(
            execute_scan_and_notify,
            trigger=trigger,
            id="daily_google_flights_scan",
            replace_existing=True
        )
        logger.info(f"已啟用每日自動排程: 每天 {hour:02d}:{minute:02d} ({tz_str}) 執行掃描與推播")
    else:
        logger.info("自動排程已在 config.yaml 中設為停用 (schedule.enabled = false)")

    return scheduler

async def start_bot_and_scheduler():
    """啟動 Discord Bot 與背景排程"""
    # 確保資料庫已初始化
    init_db()

    token = get_env_var("DISCORD_BOT_TOKEN")
    channel_id = get_env_var("DISCORD_CHANNEL_ID")

    if not token or token == "your_discord_bot_token_here":
        logger.error(
            "\n" + "!" * 60 + "\n"
            "尚未設定有效的 DISCORD_BOT_TOKEN！\n"
            "請將 .env.example 複製為 .env，並填入 Discord Developer Portal 取得的 Bot Token。\n"
            "若只需單次測試爬蟲，可執行: python3 main.py --scan 或 python3 main.py --debug\n"
            + "!" * 60
        )
        sys.exit(1)

    if not channel_id or channel_id == "your_discord_channel_id_here":
        logger.warning("尚未設定 DISCORD_CHANNEL_ID，自動排程推播將無法發送到頻道。")

    scheduler = setup_scheduler()

    async def start_scheduler_after_ready():
        await bot.wait_until_ready()
        scheduler.start()
        logger.info("Discord ready，APScheduler 排程器已啟動")

    scheduler_task = asyncio.create_task(start_scheduler_after_ready())

    try:
        logger.info("正在連線至 Discord...")
        await bot.start(token)
    except discord.LoginFailure:
        logger.error("Discord 登入失敗！請檢查 .env 中的 DISCORD_BOT_TOKEN 是否正確。")
    except Exception as e:
        logger.error(f"Bot 執行時發生錯誤: {e}", exc_info=True)
    finally:
        if not scheduler_task.done():
            scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.error(f"排程器啟動失敗: {error}", exc_info=True)
        if scheduler.running:
            scheduler.shutdown()
        if not bot.is_closed():
            await bot.close()

def main():
    args = parse_args()

    if args.debug:
        asyncio.run(run_single_scan(force_headless=False))
    elif args.scan:
        asyncio.run(run_single_scan(force_headless=None))
    else:
        asyncio.run(start_bot_and_scheduler())

if __name__ == "__main__":
    main()
