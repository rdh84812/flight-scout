import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reports.generator import build_report_payload, write_report_data
from reports.publisher import publish_report_files
from reports.service import create_scan_report
from scripts.build_site import build_site

BASE_DIR = Path(__file__).resolve().parent.parent


class TestReportGeneration(unittest.TestCase):
    def setUp(self):
        self.results = [
            {
                "id": 101,
                "result_key": "private-history-key",
                "destination": "鹿兒島市",
                "country": "日本",
                "outbound_date": "9月17日週四",
                "return_date": "9月24日週四",
                "price": 12045.0,
                "original_price": 24907.0,
                "currency": "TWD",
                "discount_info": "比平時便宜 52%",
                "airline": "中華航空",
                "flight_number": "",
                "flight_details": "直達, 2 小時 5 分鐘, TPE–KOJ",
                "source_url": "https://www.google.com/travel/flights?tfs=visible",
                "raw_text": "不應發布的原始頁面文字",
            },
            {
                "id": 102,
                "result_key": "second-key",
                "destination": "大阪",
                "country": "日本",
                "outbound_date": "10月1日週四",
                "return_date": "10月5日週一",
                "price": 9800.0,
                "original_price": None,
                "currency": "TWD",
                "discount_info": "",
                "airline": "",
                "flight_number": "",
                "flight_details": "直達, TPE–KIX",
                "source_url": "https://www.google.com/travel/flights?tfs=osaka",
                "raw_text": "private",
            },
        ]
        self.changes = [dict(self.results[0], diff_type="price_drop", prev_price=13045.0, price_drop=1000.0)]
        self.generated_at = datetime(2026, 9, 6, 7, 30, tzinfo=ZoneInfo("Asia/Taipei"))

    def test_payload_contains_only_public_fields_and_summary(self):
        payload = build_report_payload(
            scan_id=14,
            results=self.results,
            items_to_notify=self.changes,
            prompt="華航，飛任何地方，桃園機場出發",
            generated_at=self.generated_at,
        )

        self.assertEqual(payload["report_date"], "2026-09-06")
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["changed"], 1)
        self.assertEqual(payload["summary"]["lowest_price"], 9800.0)
        self.assertEqual([deal["destination"] for deal in payload["deals"]], ["大阪", "鹿兒島市"])
        kagoshima = next(deal for deal in payload["deals"] if deal["destination"] == "鹿兒島市")
        self.assertEqual(kagoshima["status"], "price_drop")
        self.assertEqual(kagoshima["previous_price"], 13045.0)
        self.assertNotIn("id", kagoshima)
        self.assertNotIn("result_key", kagoshima)
        self.assertNotIn("raw_text", kagoshima)

    def test_writer_creates_latest_and_dated_json(self):
        payload = build_report_payload(
            scan_id=14,
            results=self.results,
            items_to_notify=self.changes,
            generated_at=self.generated_at,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            written = write_report_data(payload, Path(temp_dir))
            self.assertEqual({path.name for path in written}, {"latest.json", "2026-09-06.json"})
            latest = json.loads((Path(temp_dir) / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["scan_id"], 14)

    def test_site_builder_creates_complete_pages_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            payload = build_report_payload(
                scan_id=14,
                results=self.results,
                items_to_notify=self.changes,
                generated_at=self.generated_at,
            )
            write_report_data(payload, data_dir)
            output_dir = Path(temp_dir) / "site"
            build_site(
                data_dir=data_dir,
                web_dir=BASE_DIR / "web",
                output_dir=output_dir,
            )

            self.assertTrue((output_dir / "index.html").is_file())
            self.assertTrue((output_dir / "assets" / "style.css").is_file())
            self.assertTrue((output_dir / "assets" / "app.js").is_file())
            self.assertTrue((output_dir / "data" / "latest.json").is_file())

    def test_publisher_commits_only_report_json_and_pushes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            remote = root / "remote.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Flight Scout Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
            data_dir = repo / "reports" / "data"
            data_dir.mkdir(parents=True)
            latest = data_dir / "latest.json"
            dated = data_dir / "2026-09-06.json"
            latest.write_text('{"scan_id": 14}\n', encoding="utf-8")
            dated.write_text('{"scan_id": 14}\n', encoding="utf-8")
            unrelated = repo / "do-not-stage.txt"
            unrelated.write_text("private local file", encoding="utf-8")

            result = publish_report_files(repo, [latest, dated], report_date="2026-09-06")

            self.assertTrue(result["published"])
            tracked = subprocess.run(
                ["git", "-C", str(repo), "show", "--name-only", "--format="],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(set(tracked), {"reports/data/latest.json", "reports/data/2026-09-06.json"})
            self.assertIn("do-not-stage.txt", subprocess.run(
                ["git", "-C", str(repo), "status", "--short"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout)
            subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
                check=True,
                capture_output=True,
            )

    def test_scan_report_service_exports_without_publishing_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            config = {
                "google_flights": {"prompt": "華航，飛任何地方，桃園機場出發"},
                "reports": {
                    "enabled": True,
                    "auto_publish": False,
                    "site_url": "https://example.github.io/flight-scout/",
                },
            }
            status = create_scan_report(
                scan_result={
                    "success": True,
                    "scan_id": 14,
                    "results": self.results,
                    "items_to_notify": self.changes,
                },
                config=config,
                repo_dir=repo,
                generated_at=self.generated_at,
            )

            self.assertTrue(status["generated"])
            self.assertFalse(status["published"])
            self.assertEqual(status["site_url"], "https://example.github.io/flight-scout/")
            self.assertTrue((repo / "reports" / "data" / "latest.json").is_file())


if __name__ == "__main__":
    unittest.main()
