"""
database.py
-----------
JSON fast-access layer + PharmaMatcher builder.
Syncs from Excel after every update.
"""
import json
import logging
import os
from excel_manager import load_excel, sheet_to_db_list, save_excel
from matching import PharmaMatcher

logger = logging.getLogger(__name__)
DB_PATH = os.environ.get("DB_PATH", "database.json")


def load_db() -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("items", [])


def save_db(items: list[dict]):
    # Remove internal _row key before saving to JSON
    clean = [{k: v for k, v in i.items() if k != "_row"} for i in items]
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": clean}, f, ensure_ascii=False, indent=2)
    logger.info(f"database.json saved → {len(clean)} items")


def sync_from_excel() -> list[dict]:
    """Read Excel → save JSON → return items (with _row for internal use)."""
    _, ws = load_excel()
    items = sheet_to_db_list(ws)
    save_db(items)
    return items


def build_matcher() -> tuple[PharmaMatcher, list[dict]]:
    """
    Build PharmaMatcher from Excel data.
    Returns (matcher, items_with_row) — items keep _row for update_excel.
    """
    _, ws = load_excel()
    items = sheet_to_db_list(ws)
    if not items:
        logger.warning("No items found in Excel — matcher will be empty")
    matcher = PharmaMatcher(items)
    return matcher, items
