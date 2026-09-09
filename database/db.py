import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger("database")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "flights.db"


def _normalize_result_key_part(value: Any) -> str:
    """正規化 Google 顯示欄位，排除空白與連字號格式的無意義差異。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return "".join(text.split()).casefold()


def _build_result_key(item: Dict[str, Any]) -> str:
    """以穩定的 Google 行程欄位建立識別鍵，不使用每次會變的 tfs URL。"""
    return "|".join(
        _normalize_result_key_part(item.get(field, ""))
        for field in (
            "destination",
            "country",
            "outbound_date",
            "return_date",
            "airline",
            "flight_number",
            "flight_details",
        )
    )


def _build_destination_key(item: Dict[str, Any]) -> str:
    """同地點跨日期比較；不同國家、幣別不混在一起。"""
    return "|".join(_normalize_result_key_part(item.get(field) or default)
                    for field, default in (("destination", ""), ("country", ""), ("currency", "TWD")))


def _refresh_current_flights(conn) -> None:
    # Only successful snapshots can replace the current fare. Pick the cheapest
    # candidate within the newest scan, never the historical minimum.
    conn.execute("""
        INSERT INTO current_flights (destination_key, result_id)
        SELECT destination_key, id FROM (
            SELECT f.destination_key, f.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.destination_key
                       ORDER BY f.scan_id DESC, f.price IS NULL, f.price ASC, f.id DESC
                   ) AS rank
            FROM flight_results f JOIN scan_runs s ON s.id = f.scan_id
            WHERE s.status = 'success'
        ) WHERE rank = 1
        ON CONFLICT(destination_key) DO UPDATE SET result_id = excluded.result_id
    """)

@contextmanager
def get_connection():
    """取得會自動提交/回滾並確實關閉的 SQLite 連線。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """初始化資料庫與資料表"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. scan_runs 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL,
                result_count INTEGER DEFAULT 0,
                error TEXT
            )
        """)

        # 2. flight_results 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flight_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                result_key TEXT,
                destination TEXT NOT NULL,
                country TEXT,
                outbound_date TEXT,
                return_date TEXT,
                price REAL,
                original_price REAL,
                currency TEXT DEFAULT 'TWD',
                discount_info TEXT,
                airline TEXT,
                flight_number TEXT,
                flight_details TEXT,
                source_url TEXT,
                raw_text TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (scan_id) REFERENCES scan_runs (id) ON DELETE CASCADE
            )
        """)

        existing_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(flight_results)")
        }
        if "flight_details" not in existing_columns:
            cursor.execute("ALTER TABLE flight_results ADD COLUMN flight_details TEXT")
        if "result_key" not in existing_columns:
            cursor.execute("ALTER TABLE flight_results ADD COLUMN result_key TEXT")
        if "destination_key" not in existing_columns:
            cursor.execute("ALTER TABLE flight_results ADD COLUMN destination_key TEXT")
        # Google tfs URL 每次掃描可能改變，即使是同一行程；一律改用穩定欄位 key。
        existing_results = cursor.execute(
            "SELECT id, destination, country, outbound_date, return_date, airline, "
            "flight_number, flight_details, currency FROM flight_results"
        ).fetchall()
        cursor.executemany(
            "UPDATE flight_results SET result_key = ?, destination_key = ? WHERE id = ?",
            [(_build_result_key(dict(row)), _build_destination_key(dict(row)), row["id"]) for row in existing_results],
        )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS current_flights (
                destination_key TEXT PRIMARY KEY,
                result_id INTEGER NOT NULL REFERENCES flight_results(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_flight_destination_scan ON flight_results(destination_key, scan_id)")
        _refresh_current_flights(conn)

        # 3. notifications 表 (記錄已發送通知的項目與價格)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER,
                result_key TEXT,
                destination TEXT NOT NULL,
                outbound_date TEXT,
                return_date TEXT,
                price REAL NOT NULL,
                notification_type TEXT NOT NULL,
                notified_at TEXT NOT NULL,
                FOREIGN KEY (result_id) REFERENCES flight_results (id)
            )
        """)

        notification_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(notifications)")
        }
        if "result_key" not in notification_columns:
            cursor.execute("ALTER TABLE notifications ADD COLUMN result_key TEXT")
        cursor.execute("""
            UPDATE notifications
            SET result_key = COALESCE(
                (SELECT result_key FROM flight_results WHERE id = notifications.result_id),
                result_key
            )
        """)

        # 建立索引以利快速比對
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_flight_results_dest_dates 
            ON flight_results (destination, outbound_date, return_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_dest_dates 
            ON notifications (destination, outbound_date, return_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_result_key
            ON notifications (result_key)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_flight_results_result_key
            ON flight_results (result_key)
        """)

        conn.commit()
    logger.info(f"SQLite 資料庫初始化完成: {DB_PATH}")

def start_scan_run() -> int:
    """記錄新的掃描開始，回傳 scan_id"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scan_runs (start_time, status, result_count) VALUES (?, ?, ?)",
            (now, "running", 0)
        )
        conn.commit()
        return cursor.lastrowid

def finish_scan_run(scan_id: int, status: str, result_count: int, error: Optional[str] = None) -> None:
    """更新掃描結束狀態"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE scan_runs SET end_time = ?, status = ?, result_count = ?, error = ? WHERE id = ?",
            (now, status, result_count, error, scan_id)
        )
        if status == "success":
            _refresh_current_flights(conn)
        conn.commit()

def save_flight_results(
    scan_id: int, 
    results: List[Dict[str, Any]], 
    min_price_drop: float = 0.0
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    將爬蟲結果存入資料庫，並比對歷史資料。
    回傳: (所有存入的項目, 需發送通知的項目列表[含 diff 資訊])
    """
    now = datetime.now().isoformat(timespec="seconds")
    all_saved = []
    items_to_notify = []

    with get_connection() as conn:
        cursor = conn.cursor()

        best_by_destination = {}
        for item in results:
            key = _build_destination_key(item)
            previous = best_by_destination.get(key)
            if previous is None or (item.get("price") is not None and
                                    (previous.get("price") is None or item["price"] < previous["price"])):
                best_by_destination[key] = item

        for destination_key, item in best_by_destination.items():
            dest = item.get("destination", "").strip()
            country = item.get("country", "").strip()
            out_date = item.get("outbound_date", "").strip()
            ret_date = item.get("return_date", "").strip()
            price = item.get("price")
            orig_price = item.get("original_price")
            currency = item.get("currency", "TWD")
            discount_info = item.get("discount_info", "")
            airline = item.get("airline", "")
            flight_num = item.get("flight_number", "")
            flight_details = item.get("flight_details", "")
            source_url = item.get("source_url", "")
            raw_text = item.get("raw_text", "")
            result_key = _build_result_key(item)

            cursor.execute("""
                INSERT INTO flight_results (
                    scan_id, result_key, destination_key, destination, country, outbound_date, return_date,
                    price, original_price, currency, discount_info, airline,
                    flight_number, flight_details, source_url, raw_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scan_id, result_key, destination_key, dest, country, out_date, ret_date,
                price, orig_price, currency, discount_info, airline,
                flight_num, flight_details, source_url, raw_text, now
            ))
            item_id = cursor.lastrowid
            saved_item = dict(item)
            saved_item["id"] = item_id
            saved_item["scan_id"] = scan_id
            saved_item["result_key"] = result_key
            saved_item["destination_key"] = destination_key
            all_saved.append(saved_item)

            # 比對最近成功掃描的同地點價格，不以曾通知價格或行程日期為準。
            cursor.execute("""
                SELECT f.price, f.outbound_date, f.return_date FROM current_flights c
                JOIN flight_results f ON f.id = c.result_id
                WHERE c.destination_key = ?
            """, (destination_key,))
            previous_fare = cursor.fetchone()

            if previous_fare is None:
                # 情況 1: 新出現的行程
                saved_item["diff_type"] = "new"
                saved_item["prev_price"] = None
                items_to_notify.append(saved_item)
            else:
                prev_price = previous_fare["price"]
                if price is not None and prev_price is not None and prev_price > price and (prev_price - price) >= min_price_drop:
                    # 情況 2: 同目的地價格下降
                    saved_item["diff_type"] = "price_drop"
                    saved_item["prev_price"] = prev_price
                    saved_item["price_drop"] = prev_price - price
                    saved_item["previous_outbound_date"] = previous_fare["outbound_date"]
                    saved_item["previous_return_date"] = previous_fare["return_date"]
                    items_to_notify.append(saved_item)
                else:
                    # 情況 3: 價格未變或降價未達門檻，略過通知避免洗版
                    pass

        conn.commit()

    return all_saved, items_to_notify

def mark_as_notified(items: List[Dict[str, Any]]) -> None:
    """將已發送 Discord 通知的項目寫入 notifications 表"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.cursor()
        for item in items:
            cursor.execute("""
                INSERT INTO notifications (
                    result_id, result_key, destination, outbound_date, return_date,
                    price, notification_type, notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("id"),
                item.get("result_key") or _build_result_key(item),
                item.get("destination", ""),
                item.get("outbound_date", ""),
                item.get("return_date", ""),
                item.get("price", 0.0),
                item.get("diff_type", "new"),
                now
            ))
        conn.commit()

def get_latest_scan_results(limit: int = 20) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """取得最近一次成功的掃描資訊與航班結果"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scan_runs
            WHERE status = 'success'
            ORDER BY id DESC LIMIT 1
        """)
        run = cursor.fetchone()
        if not run:
            return None, []

        run_dict = dict(run)
        cursor.execute("""
            SELECT * FROM flight_results
            WHERE scan_id = ?
            ORDER BY price ASC
            LIMIT ?
        """, (run_dict["id"], limit))
        results = [dict(r) for r in cursor.fetchall()]
        return run_dict, results

def get_current_flight_results() -> List[Dict[str, Any]]:
    """每個目的地的最新有效票價；歷史快照仍在 flight_results。"""
    with get_connection() as conn:
        return [dict(row) for row in conn.execute("""
            SELECT f.* FROM current_flights c
            JOIN flight_results f ON f.id = c.result_id
            ORDER BY f.price IS NULL, f.price ASC
        """)]


def get_new_results_from_latest_scan(
    limit: int = 15,
    min_price_drop: float = 0.0,
) -> List[Dict[str, Any]]:
    """與 /scan 相同：比較各目的地上一次成功出現時的最新票價。"""
    with get_connection() as conn:
        rows = conn.execute("""
            WITH ranked AS (
                SELECT f.*, ROW_NUMBER() OVER (
                    PARTITION BY f.destination_key
                    ORDER BY f.scan_id DESC, f.price IS NULL, f.price, f.id DESC
                ) AS rank
                FROM flight_results f JOIN scan_runs s ON s.id = f.scan_id
                WHERE s.status = 'success'
                  AND s.id < (SELECT MAX(id) FROM scan_runs WHERE status = 'success')
            )
            SELECT cur.*, prev.price AS prev_price,
                   prev.outbound_date AS previous_outbound_date,
                   prev.return_date AS previous_return_date,
                   CASE WHEN prev.id IS NULL THEN 'new' ELSE 'price_drop' END AS diff_type,
                   prev.price - cur.price AS price_drop
            FROM flight_results cur
            LEFT JOIN ranked prev ON prev.destination_key = cur.destination_key AND prev.rank = 1
            WHERE cur.scan_id = (SELECT MAX(id) FROM scan_runs WHERE status = 'success')
              AND (prev.id IS NULL OR (prev.price > cur.price AND prev.price - cur.price >= ?))
            ORDER BY cur.price IS NULL, cur.price ASC LIMIT ?
        """, (min_price_drop, limit))
        return [dict(row) for row in rows]


def get_scan_history(limit: int = 5) -> List[Dict[str, Any]]:
    """取得最近幾次掃描紀錄"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scan_runs
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


def get_latest_successful_scan() -> Optional[Dict[str, Any]]:
    """取得最近一次成功掃描，供每日排程判斷是否需要補跑。"""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT * FROM scan_runs
            WHERE status = 'success'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        return dict(row) if row else None

def get_scanner_status() -> Dict[str, Any]:
    """取得 Scanner 狀態統計"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        last_run = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as total_runs FROM scan_runs")
        total_runs = cursor.fetchone()["total_runs"]

        cursor.execute("SELECT COUNT(*) as total_results FROM flight_results")
        total_results = cursor.fetchone()["total_results"]

        cursor.execute("SELECT COUNT(*) as total_notifications FROM notifications")
        total_notifications = cursor.fetchone()["total_notifications"]

        return {
            "last_run": dict(last_run) if last_run else None,
            "total_runs": total_runs,
            "total_results": total_results,
            "total_notifications": total_notifications
        }
