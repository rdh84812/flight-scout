from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from reports.generator import build_report_payload, write_report_data
from reports.publisher import publish_report_files

BASE_DIR = Path(__file__).resolve().parent.parent


def create_scan_report(
    scan_result: Dict[str, Any],
    config: Dict[str, Any],
    repo_dir: Path = BASE_DIR,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Export a successful scan and optionally publish its JSON through Git."""
    report_config = config.get("reports", {})
    site_url = str(report_config.get("site_url", "")).strip()
    if not report_config.get("enabled", True) or not scan_result.get("success"):
        return {
            "generated": False,
            "published": False,
            "site_url": site_url,
            "reason": "disabled_or_unsuccessful_scan",
        }

    payload = build_report_payload(
        scan_id=int(scan_result.get("scan_id", 0)),
        results=scan_result.get("results", []),
        items_to_notify=scan_result.get("items_to_notify", []),
        prompt=str(config.get("google_flights", {}).get("prompt", "")),
        generated_at=generated_at,
    )
    paths = write_report_data(payload, repo_dir / "reports" / "data")
    status: Dict[str, Any] = {
        "generated": True,
        "published": False,
        "site_url": site_url,
        "report_date": payload["report_date"],
        "paths": [str(path) for path in paths],
    }
    if report_config.get("auto_publish", True):
        publication = publish_report_files(repo_dir, paths, payload["report_date"])
        status.update(publication)
    return status
