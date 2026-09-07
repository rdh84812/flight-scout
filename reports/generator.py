import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DATA_DIR = BASE_DIR / "reports" / "data"

PUBLIC_FIELDS = (
    "destination",
    "country",
    "outbound_date",
    "return_date",
    "price",
    "original_price",
    "currency",
    "discount_info",
    "airline",
    "flight_number",
    "flight_details",
    "source_url",
)


def _change_key(item: Dict[str, Any]) -> str:
    return str(item.get("result_key") or "|").strip()


def build_report_payload(
    scan_id: int,
    results: Iterable[Dict[str, Any]],
    items_to_notify: Iterable[Dict[str, Any]],
    prompt: str = "",
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build a public JSON payload without database IDs or raw page text."""
    timestamp = generated_at or datetime.now(TAIPEI_TIMEZONE)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=TAIPEI_TIMEZONE)

    result_list = list(results)
    changes = {_change_key(item): item for item in items_to_notify}
    deals: List[Dict[str, Any]] = []
    for result in sorted(
        result_list,
        key=lambda item: (
            item.get("price") is None,
            item.get("price") or 0,
            str(item.get("destination") or ""),
        ),
    ):
        deal = {field: result.get(field) for field in PUBLIC_FIELDS}
        change = changes.get(_change_key(result))
        deal["status"] = change.get("diff_type", "unchanged") if change else "unchanged"
        deal["previous_price"] = change.get("prev_price") if change else None
        deal["price_drop"] = change.get("price_drop") if change else None
        deals.append(deal)

    prices = [item.get("price") for item in deals if item.get("price") is not None]
    countries = sorted({str(item.get("country")) for item in deals if item.get("country")})
    changed_count = sum(item["status"] != "unchanged" for item in deals)

    return {
        "schema_version": 1,
        "report_date": timestamp.astimezone(TAIPEI_TIMEZONE).date().isoformat(),
        "generated_at": timestamp.astimezone(TAIPEI_TIMEZONE).isoformat(timespec="seconds"),
        "scan_id": scan_id,
        "prompt": prompt,
        "summary": {
            "total": len(deals),
            "changed": changed_count,
            "new": sum(item["status"] == "new" for item in deals),
            "price_drops": sum(item["status"] == "price_drop" for item in deals),
            "lowest_price": min(prices) if prices else None,
            "countries": countries,
        },
        "deals": deals,
    }


def write_report_data(
    payload: Dict[str, Any],
    output_dir: Path = DEFAULT_REPORT_DATA_DIR,
) -> List[Path]:
    """Write both the latest report and a date-addressable snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    paths = [
        output_dir / "latest.json",
        output_dir / f"{payload['report_date']}.json",
    ]
    for path in paths:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    return paths
