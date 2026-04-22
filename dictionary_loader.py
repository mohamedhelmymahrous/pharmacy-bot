"""
dictionary_loader.py
--------------------
Loads, applies, and updates the pharmacy matching dictionary.

dictionary.json structure:
  form_map    : raw form string  → canonical form  (e.g. "AMP" → "AMPOULE")
  aliases     : raw drug name    → canonical name  (e.g. "ADWI FLAM" → "ADWIFLAM")
  unknown_log : list of names that hit match_type == "new"
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DICT_PATH = os.environ.get("DICT_PATH", "dictionary.json")

# In-memory cache — loaded once at import, reloaded on demand
_cache: dict = {}


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_dictionary() -> dict:
    """Load dictionary from disk into memory cache."""
    global _cache
    if not os.path.exists(DICT_PATH):
        logger.warning(f"dictionary.json not found at {DICT_PATH} — using empty dict")
        _cache = {"form_map": {}, "aliases": {}, "unknown_log": []}
        return _cache
    with open(DICT_PATH, "r", encoding="utf-8") as f:
        _cache = json.load(f)
    # Ensure required keys exist
    _cache.setdefault("form_map", {})
    _cache.setdefault("aliases", {})
    _cache.setdefault("unknown_log", [])
    logger.info(f"Dictionary loaded: {len(_cache['aliases'])} aliases, "
                f"{len(_cache['form_map'])} form mappings")
    return _cache


def _save_dictionary():
    """Save current cache to disk."""
    with open(DICT_PATH, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)


def get_dictionary() -> dict:
    """Return cached dictionary, loading from disk if needed."""
    if not _cache:
        load_dictionary()
    return _cache


# ---------------------------------------------------------------------------
# Apply dictionary
# ---------------------------------------------------------------------------

def apply_alias(name: str) -> str:
    """
    Apply alias lookup to a drug name.
    Case-insensitive lookup, returns canonical name or original if no alias.

    Example:
        "ADWI FLAM"  → "ADWIFLAM"
        "adwiflam"   → "ADWIFLAM"  (uppercase only, no alias needed)
    """
    d = get_dictionary()
    upper = name.upper().strip()
    # Direct lookup
    if upper in d["aliases"]:
        return d["aliases"][upper]
    # Case-insensitive search
    for raw, canonical in d["aliases"].items():
        if raw.upper() == upper:
            return canonical.upper()
    return upper


def apply_form_map(form: str) -> str:
    """
    Normalize a dosage form string to its canonical value.

    Example:
        "AMP"     → "AMPOULE"
        "قرص"     → "TABLET"
        "TABLETS" → "TABLET"
        "Tablet"  → "TABLET"
    """
    d = get_dictionary()
    if not form:
        return ""
    upper = form.upper().strip()

    # Direct lookup
    if upper in d["form_map"]:
        return d["form_map"][upper]

    # Arabic direct lookup (no upper() for Arabic)
    stripped = form.strip()
    if stripped in d["form_map"]:
        return d["form_map"][stripped]

    # Partial match — longest key that appears in the form string
    matches = [
        (k, v) for k, v in d["form_map"].items()
        if k in upper or k in stripped
    ]
    if matches:
        best = max(matches, key=lambda x: len(x[0]))
        return best[1]

    return upper


# ---------------------------------------------------------------------------
# Auto-learning
# ---------------------------------------------------------------------------

def log_unknown(name: str):
    """
    Log an unmatched name to unknown_log in dictionary.json.
    Does NOT auto-learn — manual confirmation required via learn_alias().
    """
    d = get_dictionary()
    entry = {
        "name":      name.upper().strip(),
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # Avoid duplicate logging
    existing = [e["name"] for e in d["unknown_log"]]
    if entry["name"] not in existing:
        d["unknown_log"].append(entry)
        _save_dictionary()
        logger.info(f"Unknown logged: '{name}'")


def learn_alias(wrong_name: str, correct_name: str):
    """
    Manually register a name alias after human confirmation.

    Usage:
        learn_alias("ADWI FLAM", "ADWIFLAM")
        learn_alias("METFORMIN", "CIDOPHAGE")

    This will:
    1. Add to aliases dict
    2. Remove from unknown_log if present
    3. Save dictionary.json
    """
    d = get_dictionary()
    key = wrong_name.upper().strip()
    val = correct_name.upper().strip()
    d["aliases"][key] = val
    # Remove from unknown log
    d["unknown_log"] = [
        e for e in d["unknown_log"]
        if e["name"] != key
    ]
    _save_dictionary()
    logger.info(f"Alias learned: '{key}' → '{val}'")
    print(f"✅ Alias saved: '{key}' → '{val}'")


def get_unknown_log() -> list[dict]:
    """Return list of unmatched names logged so far."""
    return get_dictionary().get("unknown_log", [])


def add_form_mapping(raw_form: str, canonical: str):
    """Add a new form mapping to dictionary."""
    d = get_dictionary()
    d["form_map"][raw_form.upper().strip()] = canonical.upper().strip()
    _save_dictionary()
    logger.info(f"Form mapping added: '{raw_form}' → '{canonical}'")
