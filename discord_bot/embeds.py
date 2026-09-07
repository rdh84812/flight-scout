from datetime import datetime
from typing import Any, Dict, List, Optional
import discord

def format_price(amount: Optional[float], currency: str = "TWD") -> str:
    """將價格格式化為易讀字串 (如 NT$12,045)"""
    if amount is None:
        return "未知"
    formatted_num = f"{int(amount):,}"
    if currency.upper() in ["TWD", "NT$"]:
        return f"NT${formatted_num}"
    return f"${formatted_num} {currency}"

def create_deal_embed(item: Dict[str, Any]) -> discord.Embed:
    """建立單筆特惠機票的 Discord Embed"""
    dest = item.get("destination", "未知目的地")
    country = item.get("country", "")
    full_dest = f"{dest} ({country})" if country else dest

    out_date = item.get("outbound_date", "")
    ret_date = item.get("return_date", "")
    date_str = f"{out_date} → {ret_date}" if out_date and ret_date else (out_date or "未標明日期")

    curr_price_str = format_price(item.get("price"), item.get("currency", "TWD"))
    orig_price_str = format_price(item.get("original_price"), item.get("currency", "TWD")) if item.get("original_price") else ""
    discount = item.get("discount_info", "")
    flight_number = item.get("flight_number", "")
    flight_details = item.get("flight_details", "")
    source_url = item.get("source_url", "")
    diff_type = item.get("diff_type", "new")

    # 標題與顏色
    if diff_type == "price_drop":
        prev_price_str = format_price(item.get("prev_price"), item.get("currency", "TWD"))
        drop_str = format_price(item.get("price_drop", 0), item.get("currency", "TWD"))
        title = f"📉 同目的地降價通知 - {dest}"
        description = f"上次掃描 `{prev_price_str}` → 本次 `{curr_price_str}`，便宜 **{drop_str}**。"
        previous_dates = [item.get("previous_outbound_date"), item.get("previous_return_date")]
        if any(previous_dates) and previous_dates != [out_date, ret_date]:
            description += f"\n出遊日期有變動；上次為 {' → '.join(date or '未提供' for date in previous_dates)}。"
        color = discord.Color.green()
    else:
        title = f"🔥 華航新低價候選 - {dest}"
        description = f"Google Flights AI 找到飛往 **{dest}** 的華航低價候選。"
        color = discord.Color.orange()

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        url=source_url if source_url else None,
        timestamp=datetime.now()
    )

    embed.add_field(name="📍 目的地", value=full_dest, inline=True)
    embed.add_field(name="📅 來回日期", value=date_str, inline=True)
    embed.add_field(name="💰 特惠價格", value=f"**{curr_price_str}**", inline=True)

    if orig_price_str and orig_price_str != "未知":
        embed.add_field(name="🏷️ 平時價格", value=orig_price_str, inline=True)
    if discount:
        embed.add_field(name="✨ 折扣標籤", value=f"`{discount}`", inline=True)
    if flight_number:
        embed.add_field(name="✈️ 航班", value=flight_number, inline=True)
    if flight_details:
        embed.add_field(name="🛫 航程資訊", value=flight_details, inline=True)

    embed.set_footer(text="來源: Google Flights AI | 尚未驗證 booking class")
    return embed

def create_error_embed(error_msg: str, screenshot_path: Optional[str] = None) -> discord.Embed:
    """建立掃描錯誤警報 Embed"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    embed = discord.Embed(
        title="⚠️ Google Flights Scanner Error",
        description=f"**錯誤詳情：**\n```{error_msg[:500]}```",
        color=discord.Color.red(),
        timestamp=datetime.now()
    )
    embed.add_field(name="發生時間", value=now_str, inline=False)
    if screenshot_path:
        embed.add_field(
            name="除錯資訊", 
            value=f"截圖已保存於本機：`{screenshot_path}`", 
            inline=False
        )
    embed.set_footer(text="Google Flights Scanner | 錯誤警報")
    return embed

def create_scan_summary_embed(
    total_found: int, 
    new_items_count: int, 
    scan_id: int, 
    duration_sec: Optional[float] = None,
    report_url: Optional[str] = None,
    report_status: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """建立掃描完成摘要 Embed"""
    embed = discord.Embed(
        title="✈️ Google Flights AI 掃描完成",
        description=(
            f"本次掃描編號: `#{scan_id}`\n"
            f"共找到 **{total_found}** 筆特惠結果。\n"
            f"其中 **{new_items_count}** 筆為新出現或降價項目。"
        ),
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    if duration_sec:
        embed.add_field(name="耗時", value=f"{duration_sec:.1f} 秒", inline=True)
    if new_items_count == 0:
        embed.add_field(
            name="今日通知",
            value="今天沒有新發現或達到降價門檻的折扣航班。",
            inline=False,
        )
    if report_status is not None:
        report_url = report_status.get("site_url") if report_status.get("published") else None
        if report_status.get("error"):
            report_message = f"⚠️ 網頁報告處理失敗：{str(report_status['error'])[:600]}"
            if report_status.get("generated"):
                report_message += "\n本機報告已保留，航班掃描成功。"
        elif report_status.get("published"):
            report_message = "資料已上傳 GitHub；網站是否上線請查看 Actions 部署結果。若網址為 404，請檢查 Settings → Pages → GitHub Actions。"
        elif report_status.get("generated"):
            report_message = "報告已儲存於本機，尚未上傳 GitHub（自動發布未啟用）。"
        else:
            report_message = "本次未產生網頁報告。"
        embed.add_field(name="網頁發布狀態", value=report_message, inline=False)
    if report_url:
        embed.add_field(
            name="完整航班報告",
            value=f"[查看完整網頁]({report_url})",
            inline=False,
        )
    embed.set_footer(text="Google Flights Scanner")
    return embed

def create_status_embed(status_data: Dict[str, Any]) -> discord.Embed:
    """建立 Bot 與 Scanner 狀態 Embed"""
    last_run = status_data.get("last_run")
    embed = discord.Embed(
        title="🤖 Google Flights Scanner 系統狀態",
        color=discord.Color.teal(),
        timestamp=datetime.now()
    )

    embed.add_field(name="Bot 狀態", value="🟢 運作正常 (Online)", inline=True)
    embed.add_field(name="歷史掃描次數", value=str(status_data.get("total_runs", 0)), inline=True)
    embed.add_field(name="累計特惠筆數", value=str(status_data.get("total_results", 0)), inline=True)

    if last_run:
        status_text = "✅ 成功" if last_run.get("status") == "success" else f"❌ 失敗 ({last_run.get('error')})"
        embed.add_field(name="上次掃描時間", value=last_run.get("start_time", "無"), inline=True)
        embed.add_field(name="上次掃描結果", value=status_text, inline=True)
        embed.add_field(name="上次找到筆數", value=str(last_run.get("result_count", 0)), inline=True)
    else:
        embed.add_field(name="上次掃描", value="尚未執行過掃描", inline=False)

    embed.set_footer(text="使用 /scan 即可立即手動觸發搜尋")
    return embed

def create_history_embed(history_runs: List[Dict[str, Any]]) -> discord.Embed:
    """建立歷史執行紀錄 Embed"""
    embed = discord.Embed(
        title="📜 最近掃描執行紀錄",
        color=discord.Color.blurple(),
        timestamp=datetime.now()
    )
    if not history_runs:
        embed.description = "尚無歷史掃描紀錄。"
        return embed

    lines = []
    for run in history_runs:
        run_id = run.get("id")
        start_time = run.get("start_time", "")
        status = run.get("status", "")
        count = run.get("result_count", 0)
        icon = "✅" if status == "success" else "❌"
        err = f" - 錯誤: {run.get('error')[:20]}" if run.get("error") else ""
        lines.append(f"`#{run_id}` {icon} **{start_time}** | 結果: {count} 筆{err}")

    embed.description = "\n".join(lines)
    return embed

def create_help_embed() -> discord.Embed:
    """建立可用 Slash Commands 說明 Embed"""
    embed = discord.Embed(
        title="📖 Google Flights 華航特惠 Bot 指令說明",
        description="本機器人專為自動追蹤 Google Flights AI 華航低價機票而設計。",
        color=discord.Color.gold()
    )
    embed.add_field(name="`/scan`", value="立即開啟瀏覽器執行一次 Google Flights AI 搜尋。", inline=False)
    embed.add_field(name="`/latest`", value="顯示最近一次掃描找到的特惠機票清單。", inline=False)
    embed.add_field(name="`/new`", value="顯示最近一次掃描相較前次『新增或降價』的機票。", inline=False)
    embed.add_field(name="`/history`", value="查看最近幾次爬蟲掃描的執行紀錄與筆數。", inline=False)
    embed.add_field(name="`/status`", value="查看目前 Bot 運行狀況、上次執行時間與錯誤紀錄。", inline=False)
    embed.add_field(name="`/help`", value="顯示此指令說明列表。", inline=False)
    embed.set_footer(text="每日自動排程將於 config.yaml 設定之時間自動運行並推播通知")
    return embed
