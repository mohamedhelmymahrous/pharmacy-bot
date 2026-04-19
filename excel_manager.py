"""
excel_manager.py
----------------
Handles all Excel read/write operations for inventory.xlsx
"""
import os
import uuid
import logging
from typing import Optional
import openpyxl
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)

EXCEL_PATH = os.environ.get("EXCEL_PATH", "inventory.xlsx")

COLUMNS = ["ID", "NAME", "STRENGTH", "FORM", "COMPANY", "BFW", "RECEIVED", "ISSUED", "BALANCE"]
COL_INDEX = {name: i + 1 for i, name in enumerate(COLUMNS)}  # 1-based


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_excel() -> tuple[Workbook, Worksheet]:
    """
    Load inventory.xlsx. Creates a new one if not found.
    Returns (workbook, sheet)
    """
    if os.path.exists(EXCEL_PATH):
        wb = openpyxl.load_workbook(EXCEL_PATH)
        ws = wb.active
        logger.info(f"Loaded Excel: {EXCEL_PATH} ({ws.max_row - 1} items)")
    else:
        logger.warning("Excel not found — creating new inventory.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "Inventory"
        ws.append(COLUMNS)  # header row
        wb.save(EXCEL_PATH)

    return wb, ws


def sheet_to_db_list(ws: Worksheet) -> list[dict]:
    """
    Convert Excel sheet rows to a list of item dicts.
    Skips header row.
    """
    items = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):  # skip empty rows
            continue
        item = {
            "id":       str(row[COL_INDEX["ID"] - 1] or ""),
            "name":     str(row[COL_INDEX["NAME"] - 1] or ""),
            "strength": str(row[COL_INDEX["STRENGTH"] - 1] or ""),
            "form":     str(row[COL_INDEX["FORM"] - 1] or ""),
            "company":  str(row[COL_INDEX["COMPANY"] - 1] or ""),
            "bfw":      _to_float(row[COL_INDEX["BFW"] - 1]),
            "received": _to_float(row[COL_INDEX["RECEIVED"] - 1]),
            "issued":   _to_float(row[COL_INDEX["ISSUED"] - 1]),
            "balance":  _to_float(row[COL_INDEX["BALANCE"] - 1]),
        }
        if item["name"]:  # skip rows with no name
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

def update_excel(
    item: dict,
    match_type: str,
    matched_item_id: Optional[str],
    ws: Worksheet,
) -> dict:
    """
    Update Excel sheet based on match result.

    - exact/fuzzy → find row by ID → update received, issued, balance
    - new         → append new row

    Returns the final row data as dict.
    """
    received = _to_float(item.get("received", 0))
    issued   = _to_float(item.get("issued", 0))

    if match_type in ("exact", "fuzzy") and matched_item_id:
        row_num = _find_row_by_id(ws, matched_item_id)

        if row_num:
            # Read current values
            cur_bfw      = _to_float(ws.cell(row_num, COL_INDEX["BFW"]).value)
            cur_received = _to_float(ws.cell(row_num, COL_INDEX["RECEIVED"]).value)
            cur_issued   = _to_float(ws.cell(row_num, COL_INDEX["ISSUED"]).value)

            # Update
            new_received = cur_received + received
            new_issued   = cur_issued + issued
            new_balance  = cur_bfw + new_received - new_issued

            ws.cell(row_num, COL_INDEX["RECEIVED"]).value = new_received
            ws.cell(row_num, COL_INDEX["ISSUED"]).value   = new_issued
            ws.cell(row_num, COL_INDEX["BALANCE"]).value  = new_balance

            logger.info(f"Updated row {row_num}: {item.get('name')} → balance={new_balance}")

            return {
                "id":       matched_item_id,
                "name":     ws.cell(row_num, COL_INDEX["NAME"]).value,
                "strength": ws.cell(row_num, COL_INDEX["STRENGTH"]).value,
                "form":     ws.cell(row_num, COL_INDEX["FORM"]).value,
                "company":  ws.cell(row_num, COL_INDEX["COMPANY"]).value,
                "bfw":      cur_bfw,
                "received": new_received,
                "issued":   new_issued,
                "balance":  new_balance,
            }

    # --- New item → append row ---
    new_id  = str(uuid.uuid4())[:8].upper()
    bfw     = _to_float(item.get("bfw", 0))
    balance = bfw + received - issued

    new_row = [
        new_id,
        item.get("name", ""),
        item.get("strength", ""),
        item.get("form", ""),
        item.get("company", ""),
        bfw,
        received,
        issued,
        balance,
    ]
    ws.append(new_row)
    logger.info(f"New item added: {item.get('name')} id={new_id}")

    return {
        "id":       new_id,
        "name":     item.get("name", ""),
        "strength": item.get("strength", ""),
        "form":     item.get("form", ""),
        "company":  item.get("company", ""),
        "bfw":      bfw,
        "received": received,
        "issued":   issued,
        "balance":  balance,
    }


def save_excel(wb: Workbook):
    """Save workbook to disk."""
    wb.save(EXCEL_PATH)
    logger.info(f"Excel saved → {EXCEL_PATH}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_row_by_id(ws: Worksheet, item_id: str) -> Optional[int]:
    """Find row number (1-based) by item ID. Returns None if not found."""
    id_col = COL_INDEX["ID"]
    for row in ws.iter_rows(min_row=2):
        if str(row[id_col - 1].value or "").strip() == item_id.strip():
            return row[0].row
    return None


def _to_float(val) -> float:
    try:
        return float(val or 0)
    except (ValueError, TypeError):
        return 0.0
