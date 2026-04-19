"""
database.py
-----------
JSON database layer — fast access, synced from Excel.
Also builds and returns PharmaMatcher instance.
"""
import json
import logging
import os
from typing import Optional
from excel_manager import load_excel, sheet_to_db_list, save_excel
from matching import PharmaMatcher

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "database.json")


# ---------------------------------------------------------------------------
# JSON Operations
# ---------------------------------------------------------------------------

def load_db() -> list[dict]:
    """Load items from database.json. Returns empty list if not found."""
    if not os.path.exists(DB_PATH):
        logger.warning("database.json not found — returning empty list")
        return []
    with open(DB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def save_db(items: list[dict]):
    """Save items list to database.json."""
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    logger.info(f"database.json saved → {len(items)} items")


def sync_from_excel() -> list[dict]:
    """
    Read Excel → update database.json → return items list.
    Call this after every Excel update.
    """
    _, ws = load_excel()
    items = sheet_to_db_list(ws)
    save_db(items)
    logger.info(f"JSON synced from Excel → {len(items)} items")
    return items


# ---------------------------------------------------------------------------
# Matcher Builder
# ---------------------------------------------------------------------------

def build_matcher() -> PharmaMatcher:
    """
    Build PharmaMatcher from current database.
    Priority: database.json → Excel (if JSON missing)
    """
    items = load_db()
    if not items:
        logger.info("JSON empty — loading from Excel")
        items = sync_from_excel()
    return PharmaMatcher(items)
