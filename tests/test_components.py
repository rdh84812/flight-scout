import tempfile
import unittest
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# 加入專案根目錄至 sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import database.db as db_module
from database.db import (
    get_connection,
    init_db,
    start_scan_run,
    finish_scan_run,
    save_flight_results,
    mark_as_notified,
    get_latest_scan_results,
    get_new_results_from_latest_scan,
    get_scan_history,
    get_scanner_status
)
from scanner.parser import parse_deal_card_text, clean_price
from scanner.google_flights import is_retryable_system_error, validate_scan_results
from discord_bot.embeds import (
    create_deal_embed,
    create_error_embed,
    create_scan_summary_embed,
    create_status_embed,
    create_history_embed,
    create_help_embed
)
from discord_bot.bot import bot
from discord_bot.bot import execute_scan_and_notify, send_deals_to_channel
from utils.logger import get_logger

class TestScannerComponents(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db_module.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self._temp_dir.name) / "flights.db"
        init_db()

    def tearDown(self):
        db_module.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def test_clean_price(self):
        self.assertEqual(clean_price("NT$12,045"), 12045.0)
        self.assertEqual(clean_price("$8,200"), 8200.0)
        self.assertEqual(clean_price("15,000 元"), 15000.0)
        self.assertIsNone(clean_price("無價格"))

    def test_parser_card(self):
        raw = (
            "鹿兒島市\n日本\n"
            "9月17日週四 — 9月24日週四\n"
            "$12,045\n$24,907\n"
            "比平時便宜 52%\n中華航空\n"
            "直達, 2 小時 5 分鐘, TPE – KOJ"
        )
        parsed = parse_deal_card_text(raw, "/travel/flights?tfs=test")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["destination"], "鹿兒島市")
        self.assertEqual(parsed["country"], "日本")
        self.assertEqual(parsed["outbound_date"], "9月17日週四")
        self.assertEqual(parsed["return_date"], "9月24日週四")
        self.assertEqual(parsed["price"], 12045.0)
        self.assertEqual(parsed["original_price"], 24907.0)
        self.assertIn("52%", parsed["discount_info"])
        self.assertEqual(parsed["airline"], "中華航空")
        self.assertEqual(parsed["flight_number"], "")
        self.assertIn("KOJ", parsed["flight_details"])

    def test_parser_does_not_invent_airline_or_flight_number(self):
        raw = (
            "鹿兒島市\n日本\n9月17日週四 — 9月24日週四\n"
            "$12,045\n比平時便宜 52%\n直達\n2 小時 5 分鐘\nTPE–KOJ"
        )
        parsed = parse_deal_card_text(raw, "/travel/flights?tfs=test")
        self.assertEqual(parsed["airline"], "")
        self.assertEqual(parsed["flight_number"], "")
        self.assertIn("TPE", parsed["flight_details"])

    def test_empty_parsed_results_are_a_scan_failure(self):
        with self.assertRaisesRegex(ValueError, "未解析出任何"):
            validate_scan_results([])

    def test_only_google_system_error_is_retryable(self):
        self.assertTrue(is_retryable_system_error("偵測到 Google 安全驗證特徵: '糟糕！系統發生錯誤'"))
        self.assertFalse(is_retryable_system_error("偵測到 Google 安全驗證特徵: 'captcha'"))
        self.assertFalse(is_retryable_system_error(None))

    def test_database_and_diff(self):
        scan_id = start_scan_run()
        self.assertIsInstance(scan_id, int)

        dummy_results = [
            {
                "destination": "測試大阪",
                "country": "日本",
                "outbound_date": "10月16日",
                "return_date": "10月18日",
                "price": 8200.0,
                "original_price": 10000.0,
                "currency": "TWD",
                "discount_info": "比平時便宜 18%",
                "airline": "中華航空",
                "flight_number": "直達",
                "source_url": "https://flights.google.com",
                "raw_text": "測試"
            }
        ]

        # 第一輪儲存: 應視為新項目
        all_saved, to_notify = save_flight_results(scan_id, dummy_results, min_price_drop=100.0)
        self.assertEqual(len(all_saved), 1)
        self.assertEqual(len(to_notify), 1)
        self.assertEqual(to_notify[0]["diff_type"], "new")

        # 標記已通知
        mark_as_notified(to_notify)

        finish_scan_run(scan_id, "success", 1)

        # 第二輪儲存相同價格: 不應再次通知
        scan_id_2 = start_scan_run()
        all_saved2, to_notify2 = save_flight_results(scan_id_2, dummy_results, min_price_drop=100.0)
        self.assertEqual(len(to_notify2), 0)
        finish_scan_run(scan_id_2, "success", 1)

        # 第三輪價格下降 NT$500: 應觸發降價通知
        scan_id_3 = start_scan_run()
        dummy_results[0]["price"] = 7700.0
        all_saved3, to_notify3 = save_flight_results(scan_id_3, dummy_results, min_price_drop=100.0)
        self.assertEqual(len(to_notify3), 1)
        self.assertEqual(to_notify3[0]["diff_type"], "price_drop")
        self.assertEqual(to_notify3[0]["price_drop"], 500.0)
        finish_scan_run(scan_id_3, "success", 1)

        latest_changes = get_new_results_from_latest_scan(limit=10, min_price_drop=100.0)
        self.assertEqual(len(latest_changes), 1)
        self.assertEqual(latest_changes[0]["diff_type"], "price_drop")
        self.assertEqual(latest_changes[0]["prev_price"], 8200.0)

    def test_database_connection_is_closed_after_context(self):
        db_path = db_module.DB_PATH
        with get_connection() as conn:
            conn.execute("SELECT 1")
        db_path.unlink()
        self.assertFalse(db_path.exists())

    def test_current_fare_updates_on_rise_then_notifies_even_one_dollar_drop(self):
        base = {"destination": "大阪", "country": "日本", "currency": "TWD", "outbound_date": "10月1日"}
        for price, expected in [(8000, "new"), (9000, None), (8999, "price_drop"), (8999, None)]:
            scan = start_scan_run()
            saved, changes = save_flight_results(scan, [dict(base, price=price, outbound_date=f"10月{scan}日")])
            self.assertEqual(changes[0]["diff_type"] if changes else None, expected)
            if expected == "price_drop":
                self.assertEqual(changes[0]["prev_price"], 9000)
                self.assertEqual(changes[0]["price_drop"], 1)
            finish_scan_run(scan, "success", len(saved))
            current = db_module.get_current_flight_results()
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["price"], price)
            latest_changes = get_new_results_from_latest_scan()
            self.assertEqual([c["diff_type"] for c in latest_changes], [expected] if expected else [])
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM flight_results").fetchone()[0], 4)

    def test_duplicate_destinations_use_cheapest_new_scan_and_failed_scan_is_ignored(self):
        base = {"destination": "大阪", "country": "日本", "price": 8000}
        first = start_scan_run()
        save_flight_results(first, [base])
        finish_scan_run(first, "success", 1)
        failed = start_scan_run()
        save_flight_results(failed, [dict(base, price=1000)])
        finish_scan_run(failed, "failed", 0)
        self.assertEqual(db_module.get_current_flight_results()[0]["price"], 8000)
        next_scan = start_scan_run()
        saved, changes = save_flight_results(next_scan, [dict(base, price=10000), dict(base, price=9000)])
        self.assertEqual(len(saved), 1)
        self.assertEqual(changes, [])
        finish_scan_run(next_scan, "success", 1)
        self.assertEqual(db_module.get_current_flight_results()[0]["price"], 9000)

    def test_country_and_currency_are_separate_and_absent_city_keeps_last_fare(self):
        first = start_scan_run()
        saved, _ = save_flight_results(first, [
            {"destination": "同名城市", "country": "日本", "currency": "TWD", "price": 8000},
            {"destination": "同名城市", "country": "美國", "currency": "TWD", "price": 9000},
            {"destination": "同名城市", "country": "日本", "currency": "USD", "price": 300},
        ])
        finish_scan_run(first, "success", len(saved))
        self.assertEqual(len(db_module.get_current_flight_results()), 3)
        second = start_scan_run()
        save_flight_results(second, [{"destination": "東京", "price": 7000}])
        finish_scan_run(second, "success", 1)
        third = start_scan_run()
        _, changes = save_flight_results(third, [{"destination": "同名城市", "country": "日本", "currency": "TWD", "price": 7999}])
        self.assertEqual(changes[0]["prev_price"], 8000)

    def test_existing_database_migration_keeps_history_and_latest_price(self):
        for price in (8000, 9000):
            scan = start_scan_run()
            save_flight_results(scan, [{"destination": "大阪", "price": price}])
            finish_scan_run(scan, "success", 1)
        with get_connection() as conn:
            conn.execute("DROP TABLE current_flights")
            conn.execute("DROP INDEX idx_flight_destination_scan")
            conn.execute("ALTER TABLE flight_results DROP COLUMN destination_key")
        init_db()
        init_db()
        self.assertEqual(db_module.get_current_flight_results()[0]["price"], 9000)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM flight_results").fetchone()[0], 2)

    def test_dynamic_source_url_does_not_duplicate_same_itinerary(self):
        first_scan = start_scan_run()
        base_item = {
            "destination": "大阪",
            "outbound_date": "10月16日",
            "return_date": "10月18日",
            "price": 8200.0,
            "currency": "TWD",
            "source_url": "https://www.google.com/travel/flights?tfs=year-2026",
            "raw_text": "大阪",
        }
        _, first_notifications = save_flight_results(first_scan, [base_item])
        mark_as_notified(first_notifications)
        finish_scan_run(first_scan, "success", 1)

        second_scan = start_scan_run()
        same_itinerary_with_new_url = dict(base_item)
        same_itinerary_with_new_url["source_url"] = (
            "https://www.google.com/travel/flights?tfs=dynamic-signature"
        )
        _, second_notifications = save_flight_results(
            second_scan, [same_itinerary_with_new_url]
        )

        self.assertEqual(second_notifications, [])

    def test_embeds(self):
        item_new = {
            "destination": "東京",
            "country": "日本",
            "outbound_date": "10/1",
            "return_date": "10/7",
            "price": 9500.0,
            "original_price": 12000.0,
            "currency": "TWD",
            "discount_info": "比平時便宜 20%",
            "flight_number": "直達",
            "source_url": "https://flights.google.com",
            "diff_type": "new"
        }
        embed_new = create_deal_embed(item_new)
        self.assertIn("東京", embed_new.title)

        item_drop = dict(item_new)
        item_drop["diff_type"] = "price_drop"
        item_drop["prev_price"] = 9500.0
        item_drop["price"] = 8500.0
        item_drop["price_drop"] = 1000.0
        embed_drop = create_deal_embed(item_drop)
        self.assertIn("降價", embed_drop.title)

        error_embed = create_error_embed("測試錯誤", "debug/test.png")
        self.assertEqual(error_embed.title, "⚠️ Google Flights Scanner Error")

        summary_embed = create_scan_summary_embed(
            10,
            2,
            1,
            15.5,
            report_url="https://example.github.io/flight-scout/",
        )
        self.assertIn("#1", summary_embed.description)
        self.assertIn("查看完整網頁", summary_embed.fields[-1].value)
        no_new_summary = create_scan_summary_embed(10, 0, 2, 15.5)
        self.assertIn("沒有新發現", no_new_summary.fields[-1].value)

        status_data = get_scanner_status()
        status_embed = create_status_embed(status_data)
        self.assertIn("系統狀態", status_embed.title)

        history_data = get_scan_history(5)
        history_embed = create_history_embed(history_data)
        self.assertIn("最近掃描", history_embed.title)

        help_embed = create_help_embed()
        self.assertIn("指令說明", help_embed.title)

    def test_slash_commands_registered(self):
        registered_commands = [cmd.name for cmd in bot.tree.get_commands()]
        expected_commands = ["scan", "latest", "new", "history", "status", "help"]
        for expected in expected_commands:
            self.assertIn(expected, registered_commands, f"Slash command /{expected} 應已註冊")

    def test_loggers_keep_their_component_names(self):
        self.assertEqual(get_logger("component_a").name, "component_a")
        self.assertEqual(get_logger("component_b").name, "component_b")

    def test_file_logging_is_rotated_for_long_running_bot(self):
        logger = get_logger("rotation_test")
        self.assertTrue(
            any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers)
        )

    def test_main_imports_discord_for_login_failure_handling(self):
        import main
        self.assertTrue(hasattr(main, "discord"))

    def test_scan_cli_uses_configured_headless_setting(self):
        import main

        scan_result = object()
        scan_mock = MagicMock(return_value=scan_result)
        with (
            patch.object(main, "parse_args", return_value=SimpleNamespace(debug=False, scan=True)),
            patch.object(main, "run_single_scan", scan_mock),
            patch.object(main.asyncio, "run") as asyncio_run,
        ):
            main.main()

        scan_mock.assert_called_once_with(force_headless=None)
        asyncio_run.assert_called_once_with(scan_result)

    def test_scheduler_allows_sleep_resume_and_avoids_overlapping_runs(self):
        import main

        with patch.object(main, "load_config", return_value={
            "schedule": {
                "enabled": True,
                "hour": 7,
                "minute": 0,
                "timezone": "Asia/Taipei",
                "misfire_grace_hours": 23,
            }
        }):
            scheduler = main.setup_scheduler()
        job = scheduler.get_job("daily_google_flights_scan")
        self.assertEqual(job.misfire_grace_time, 23 * 60 * 60)
        self.assertTrue(job.coalesce)
        self.assertEqual(job.max_instances, 1)

    def test_startup_catchup_runs_only_after_schedule_without_today_success(self):
        import main

        config = {"schedule": {
            "enabled": True,
            "hour": 7,
            "minute": 0,
            "timezone": "Asia/Taipei",
            "catch_up_on_start": True,
        }}
        before_schedule = datetime(2026, 9, 9, 6, 59)
        after_schedule = datetime(2026, 9, 9, 10, 0)

        with patch.object(main, "get_latest_successful_scan", return_value=None):
            self.assertFalse(main.should_run_startup_catchup(config, before_schedule))
            self.assertTrue(main.should_run_startup_catchup(config, after_schedule))
        with patch.object(main, "get_latest_successful_scan", return_value={
            "start_time": "2026-09-09T07:00:00"
        }):
            self.assertFalse(main.should_run_startup_catchup(config, after_schedule))
        with patch.object(main, "get_latest_successful_scan", return_value={
            "start_time": "2026-09-08T14:00:00"
        }):
            self.assertTrue(main.should_run_startup_catchup(config, after_schedule))


class TestDiscordDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_manual_scan_sends_price_drop_details_after_summary(self):
        from discord_bot.bot import cmd_scan
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        drop = {"id": 2, "destination": "大阪", "price": 8999, "prev_price": 9000, "price_drop": 1,
                "diff_type": "price_drop", "outbound_date": "10月2日", "return_date": "10月9日",
                "previous_outbound_date": "10月1日", "previous_return_date": "10月8日"}
        with (
            patch("discord_bot.bot.run_google_flights_scan", AsyncMock(return_value={"success": True, "scan_id": 2, "results": [drop], "items_to_notify": [drop]})),
            patch("discord_bot.bot.prepare_scan_report", AsyncMock(return_value={"generated": True, "published": False})),
            patch("discord_bot.bot.mark_as_notified") as mark,
        ):
            await cmd_scan.callback(interaction)
        self.assertEqual(interaction.followup.send.await_count, 3)
        embed = interaction.followup.send.await_args.kwargs["embeds"][0]
        self.assertIn("9,000", embed.description)
        self.assertIn("8,999", embed.description)
        self.assertIn("出遊日期有變動", embed.description)
        mark.assert_called_once_with([drop])

    async def test_price_drop_delivery_only_marks_successful_batches(self):
        from discord_bot.bot import send_scan_changes
        drops = [{"id": i, "destination": "大阪", "price": 8000, "prev_price": 9000, "price_drop": 1000, "diff_type": "price_drop"} for i in range(12)]
        channel = SimpleNamespace(send=AsyncMock(side_effect=[None, None, RuntimeError("delivery failed")]))
        with patch("discord_bot.bot.mark_as_notified") as mark:
            with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                await send_scan_changes(channel, drops)
        mark.assert_called_once_with(drops[:10])

    async def test_command_sync_failure_stops_bot_startup(self):
        with patch.object(
            bot.tree,
            "sync",
            AsyncMock(side_effect=RuntimeError("sync failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                await bot.setup_hook()

    async def test_sends_and_marks_every_item_in_successful_chunks(self):
        channel = SimpleNamespace(send=AsyncMock())
        items = [
            {
                "id": index,
                "destination": f"目的地 {index}",
                "price": 1000 + index,
                "currency": "TWD",
                "diff_type": "new",
            }
            for index in range(23)
        ]

        with patch("discord_bot.bot.mark_as_notified") as mark_mock:
            await send_deals_to_channel(channel, items, mark_sent=True)

        self.assertEqual(channel.send.await_count, 3)
        self.assertEqual([len(call.args[0]) for call in mark_mock.call_args_list], [10, 10, 3])

    async def test_scheduled_scan_sends_one_web_summary_and_marks_changes(self):
        channel = SimpleNamespace(send=AsyncMock())
        changed = [{"id": 1, "destination": "鹿兒島", "price": 12000, "result_key": "key"}]
        scan_result = {
            "success": True,
            "scan_id": 18,
            "results": changed,
            "items_to_notify": changed,
        }
        with (
            patch("discord_bot.bot.get_notification_channel", AsyncMock(return_value=channel)),
            patch("discord_bot.bot.run_google_flights_scan", AsyncMock(return_value=scan_result)),
            patch(
                "discord_bot.bot.prepare_scan_report",
                AsyncMock(return_value={
                    "generated": True,
                    "published": True,
                    "site_url": "https://example.github.io/flight-scout/",
                }),
            ),
            patch("discord_bot.bot.mark_as_notified") as mark_mock,
        ):
            await execute_scan_and_notify()

        channel.send.assert_awaited_once()
        sent_embed = channel.send.await_args.kwargs["embed"]
        self.assertIn("查看完整網頁", sent_embed.fields[-1].value)
        mark_mock.assert_called_once_with(changed)

if __name__ == "__main__":
    unittest.main()
