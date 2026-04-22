"""
dictionary_loader.py — Pharmacy matching dictionary manager.
"""
import json
import os
import logging
from datetime import datetime

logger   = logging.getLogger(__name__)
DICT_PATH = os.environ.get("DICT_PATH", "dictionary.json")
_cache: dict = {}


# ── Load / Save ──────────────────────────────────────────────────────── #

def load_dictionary() -> dict:
    global _cache
    if not os.path.exists(DICT_PATH):
        logger.warning(f"dictionary.json not found at {DICT_PATH}")
        _cache = {"form_map": {}, "aliases": {}, "unknown_log": []}
        return _cache
    with open(DICT_PATH, "r", encoding="utf-8") as f:
        _cache = json.load(f)
    _cache.setdefault("form_map", {})
    _cache.setdefault("aliases", {})
    _cache.setdefault("unknown_log", [])
    logger.info(f"Dictionary loaded: {len(_cache['aliases'])} aliases, "
                f"{len(_cache['form_map'])} form mappings")
    return _cache


def _save():
    with open(DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)


def get_dictionary() -> dict:
    if not _cache:
        load_dictionary()
    return _cache


# ── Apply ────────────────────────────────────────────────────────────── #

def apply_alias(name: str) -> str:
    d     = get_dictionary()
    upper = name.upper().strip()
    if upper in d["aliases"]:
        return d["aliases"][upper]
    for raw, canon in d["aliases"].items():
        if raw.upper() == upper:
            return canon.upper()
    return upper


def apply_form_map(form: str) -> str:
    d = get_dictionary()
    if not form: return ""
    upper   = form.upper().strip()
    stripped = form.strip()
    if upper   in d["form_map"]: return d["form_map"][upper]
    if stripped in d["form_map"]: return d["form_map"][stripped]
    matches = [(k, v) for k, v in d["form_map"].items()
               if k in upper or k in stripped]
    if matches:
        return max(matches, key=lambda x: len(x[0]))[1]
    return upper


# ── Logging (IMPROVED: full context + dedup by name+strength+form) ───── #

def log_unknown(item):
    """
    Log an unmatched item with full context.
    Deduplication is based on (name + strength + form) combination.

    Accepts either:
      - a dict  (full item from matcher)
      - a str   (legacy — name only)
    """
    d = get_dictionary()

    # Support legacy str call and new dict call
    if isinstance(item, str):
        item = {"name": item}

    name     = str(item.get("name",     "") or "").upper().strip()
    strength = str(item.get("strength", "") or "").upper().strip()
    form     = str(item.get("form",     "") or
                   item.get("uom",      "") or "").upper().strip()
    company  = str(item.get("company",  "") or "").upper().strip()

    if not name:
        return

    # Dedup key: name + strength + form
    dedup_key = f"{name}|{strength}|{form}"
    existing_keys = [
        f"{e['name']}|{e.get('strength','')}|{e.get('form','')}".upper()
        for e in d["unknown_log"]
    ]
    if dedup_key in existing_keys:
        return

    entry = {
        "name":      name,
        "strength":  strength,
        "form":      form,
        "company":   company,
        "raw_item":  {k: str(v) for k, v in item.items()
                      if k not in ("_row",)},
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    d["unknown_log"].append(entry)
    _save()
    logger.info(f"Unknown logged: '{name}' str='{strength}' form='{form}'")


# ── Learning ─────────────────────────────────────────────────────────── #

def learn_alias(wrong_name: str, correct_name: str):
    """
    Register a confirmed alias and remove from unknown_log.
    Called after user confirms via Telegram YES flow.
    """
    d   = get_dictionary()
    key = wrong_name.upper().strip()
    val = correct_name.upper().strip()
    d["aliases"][key] = val
    # Remove from unknown log
    d["unknown_log"] = [
        e for e in d["unknown_log"]
        if e["name"] != key
    ]
    _save()
    logger.info(f"Alias learned: '{key}' → '{val}'")


def get_unknown_log() -> list:
    return get_dictionary().get("unknown_log", [])


def add_form_mapping(raw_form: str, canonical: str):
    d = get_dictionary()
    d["form_map"][raw_form.upper().strip()] = canonical.upper().strip()
    _save()
    logger.info(f"Form mapping added: '{raw_form}' → '{canonical}'")
