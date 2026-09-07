import asyncio
import time
from typing import Optional

import discord
from discord.ext import commands

from database.db import (
    get_latest_scan_results,
    get_new_results_from_latest_scan,
    get_scan_history,
    get_scanner_status,
    mark_as_notified,
)
from discord_bot.embeds import (
    create_deal_embed,
    create_error_embed,
    create_help_embed,
    create_history_embed,
    create_scan_summary_embed,
    create_status_embed,
)
from scanner.google_flights import _SCAN_LOCK, run_google_flights_scan
from reports.service import create_scan_report
from utils.config import get_env_var, load_config
from utils.logger import get_logger

logger = get_logger("discord_bot")


async def prepare_scan_report(scan_res: dict) -> dict:
    """在瀏覽器關閉後產生並發布網頁資料，不阻塞 Discord event loop。"""
    try:
        status = await asyncio.to_thread(create_scan_report, scan_res, load_config())
        if status.get("published"):
            logger.info(f"航班網頁資料已推送: {status.get('site_url', '')}")
        return status
    except Exception as error:
        logger.error(f"產生或發布航班網頁失敗: {error}", exc_info=True)
        return {"generated": False, "published": False, "error": str(error)}

class FlightBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.notification_channel_id: Optional[int] = None

    async def setup_hook(self):
        # 註冊 Slash Commands 至 Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"成功同步 {len(synced)} 個 Slash Commands")
        except Exception as e:
            logger.error(f"同步 Slash Commands 失敗: {e}")
            raise

bot = FlightBot()


async def get_notification_channel():
    """從 cache 或 Discord API 取得通知頻道。"""
    channel_id_str = get_env_var("DISCORD_CHANNEL_ID")
    if not channel_id_str or not channel_id_str.isdigit():
        return None
    channel_id = int(channel_id_str)
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
        logger.error(f"無法取得 Discord 通知頻道 {channel_id}: {error}")
        return None

async def send_deals_to_channel(
    channel: discord.abc.Messageable,
    items: list,
    title_prefix: str = "",
    mark_sent: bool = False,
):
    """分批發送所有特惠卡片，並只標記已成功送出的批次。"""
    if not items:
        return

    if title_prefix:
        await channel.send(title_prefix)

    # Discord 每則訊息最多支援 10 個 Embeds。
    for i in range(0, len(items), 10):
        chunk = items[i:i + 10]
        embeds = [create_deal_embed(item) for item in chunk]
        await channel.send(embeds=embeds)
        if mark_sent:
            mark_as_notified(chunk)
        if i + 10 < len(items):
            await asyncio.sleep(0.5)

async def execute_scan_and_notify():
    """執行自動排程掃描，並於有新特惠或降價時通知指定頻道"""
    logger.info("排程觸發: 開始執行 Google Flights 每日自動掃描...")
    channel = await get_notification_channel()

    start_time = time.time()
    try:
        scan_res = await run_google_flights_scan()
    except Exception as error:
        logger.error(f"每日排程掃描無法啟動: {error}", exc_info=True)
        scan_res = {
            "success": False,
            "error": f"掃描初始化失敗: {error}",
            "results": [],
            "items_to_notify": [],
        }
    duration = time.time() - start_time

    if not scan_res.get("success"):
        err = scan_res.get("error", "未知錯誤")
        screenshot = scan_res.get("screenshot")
        logger.error(f"每日排程掃描失敗: {err}")
        if channel:
            embed = create_error_embed(err, screenshot)
            await channel.send(embed=embed)
        return

    items_to_notify = scan_res.get("items_to_notify", [])
    total_found = len(scan_res.get("results", []))
    report_status = await prepare_scan_report(scan_res)
    report_url = report_status.get("site_url") if report_status.get("generated") else None

    if channel:
        logger.info(f"發送掃描摘要；新特惠/降價機票 {len(items_to_notify)} 筆")
        summary_embed = create_scan_summary_embed(
            total_found=total_found,
            new_items_count=len(items_to_notify),
            scan_id=scan_res.get("scan_id", 0),
            duration_sec=duration,
            report_url=report_url,
        )
        await channel.send(embed=summary_embed)
        if items_to_notify:
            mark_as_notified(items_to_notify)
    else:
        logger.warning("未設定有效的 DISCORD_CHANNEL_ID，略過頻道推播")

# ==================== Slash Commands ====================

@bot.tree.command(name="scan", description="立即執行一次 Google Flights AI 搜尋")
async def cmd_scan(interaction: discord.Interaction):
    if _SCAN_LOCK.locked():
        await interaction.response.send_message("⚠️ Scanner is already running. 請稍候目前任務完成。", ephemeral=True)
        return

    # 先回覆告知已開始搜尋
    await interaction.response.send_message("🔎 開始搜尋 Google Flights AI... 請稍候 (約需 10~20 秒)")

    start_time = time.time()
    try:
        scan_res = await run_google_flights_scan()
    except Exception as error:
        logger.error(f"手動掃描無法啟動: {error}", exc_info=True)
        scan_res = {
            "success": False,
            "error": f"掃描初始化失敗: {error}",
            "results": [],
            "items_to_notify": [],
        }
    duration = time.time() - start_time

    if not scan_res.get("success"):
        err = scan_res.get("error", "未知錯誤")
        screenshot = scan_res.get("screenshot")
        error_embed = create_error_embed(err, screenshot)
        await interaction.followup.send(content="❌ 掃描失敗！", embed=error_embed)
        return

    results = scan_res.get("results", [])
    items_to_notify = scan_res.get("items_to_notify", [])
    total_count = len(results)
    new_count = len(items_to_notify)

    report_status = await prepare_scan_report(scan_res)
    report_url = report_status.get("site_url") if report_status.get("generated") else None
    reply_msg = f"✅ 搜尋完成，共找到 **{total_count}** 筆結果，其中 **{new_count}** 筆為新結果/降價機票。"
    summary_embed = create_scan_summary_embed(
        total_found=total_count,
        new_items_count=new_count,
        scan_id=scan_res.get("scan_id", 0),
        duration_sec=duration,
        report_url=report_url,
    )
    await interaction.followup.send(content=reply_msg, embed=summary_embed)
    if items_to_notify:
        mark_as_notified(items_to_notify)

@bot.tree.command(name="latest", description="顯示最近一次搜尋結果 (最低價優先)")
async def cmd_latest(interaction: discord.Interaction):
    run, results = get_latest_scan_results(limit=10)
    if not run or not results:
        await interaction.response.send_message("目前資料庫尚無有效的成功搜尋結果，可使用 `/scan` 立即搜尋！")
        return

    site_url = load_config().get("reports", {}).get("site_url", "")
    await interaction.response.send_message(
        f"📊 最近一次掃描 (ID: `#{run['id']}` - {run['start_time']})，共找到 {run['result_count']} 筆。\n"
        f"🌐 [查看完整航班網頁]({site_url})"
    )

@bot.tree.command(name="new", description="顯示最近一次掃描相較前一輪新增或降價的機票")
async def cmd_new(interaction: discord.Interaction):
    min_price_drop = load_config().get("notification", {}).get("min_price_drop", 100.0)
    new_results = get_new_results_from_latest_scan(
        limit=10,
        min_price_drop=min_price_drop,
    )
    if not new_results:
        site_url = load_config().get("reports", {}).get("site_url", "")
        await interaction.response.send_message(
            "最近一次掃描相較上一輪無新增或顯著降價機票。\n"
            f"🌐 [查看完整航班網頁]({site_url})"
        )
        return

    site_url = load_config().get("reports", {}).get("site_url", "")
    await interaction.response.send_message(
        f"✨ 最近一次掃描新增或降價的航班共 {len(new_results)} 筆。\n"
        f"🌐 [查看完整航班網頁]({site_url})"
    )

@bot.tree.command(name="history", description="顯示最近幾次 scanner 執行紀錄")
async def cmd_history(interaction: discord.Interaction):
    history = get_scan_history(limit=7)
    embed = create_history_embed(history)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="status", description="顯示 Bot 與 Google Flights Scanner 運作狀態")
async def cmd_status(interaction: discord.Interaction):
    status_data = get_scanner_status()
    embed = create_status_embed(status_data)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="顯示 Bot 可用指令列表")
async def cmd_help(interaction: discord.Interaction):
    embed = create_help_embed()
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    logger.info(f"Discord Bot 已成功登入為: {bot.user.name} (ID: {bot.user.id})")
    logger.info("Bot 已在線上並準備接收 Slash Commands！")
