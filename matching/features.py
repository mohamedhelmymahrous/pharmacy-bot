"""
features.py
-----------
Converts a raw item dict into a structured, comparable FeatureSet.
All normalization happens here using normalizer.py.
"""
from dataclasses import dataclass, field
from typing import Optional
from .normalizer import (
    normalize_name,
    normalize_form,
    parse_strength,
    strength_to_base,
    tokenize_name,
)


@dataclass
class FeatureSet:
    """
    Structured representation of a pharmaceutical item for matching.
    All fields are normalized and ready for comparison.
    """
    # Raw item identifier (optional, for traceability)
    item_id: Optional[str] = None

    # Name features
    raw_name: str = ""
    normalized_name: str = ""
    name_tokens: set = field(default_factory=set)

    # Strength features
    strength_value: Optional[float] = None   # numeric value
    strength_unit: Optional[str] = None      # normalized unit (MG, ML, IU...)
    strength_base_value: Optional[float] = None  # converted to base unit
    strength_base_unit: Optional[str] = None

    # Form features
    raw_form: str = ""
    canonical_form: str = ""

    # Company features (optional)
    raw_company: str = ""
    company_tokens: set = field(default_factory=set)

    # Code (optional, for exact lookup)
    code: Optional[str] = None


def extract_features(item: dict) -> FeatureSet:
    """
    Convert a raw item dict to a FeatureSet.

    Expected input keys (all optional except 'name'):
        name, strength, form, company, code, id

    Example:
        item = {
            "name": "ADWIFLAM",
            "strength": "75MG",
            "form": "AMPOULE",
            "company": "ADWIA",
            "code": "270-00539"
        }
    """
    fs = FeatureSet()

    # --- ID / Code ---
    fs.item_id = item.get("id") or item.get("item_id")
    fs.code = _clean_code(item.get("code", ""))

    # --- Name ---
    raw_name = str(item.get("name", "") or "").strip()
    fs.raw_name = raw_name
    fs.normalized_name = normalize_name(raw_name)
    fs.name_tokens = tokenize_name(fs.normalized_name)

    # --- Strength ---
    raw_strength = str(item.get("strength", "") or "").strip()
    value, unit = parse_strength(raw_strength)
    fs.strength_value = value
    fs.strength_unit = unit
    if value is not None and unit is not None:
        fs.strength_base_value, fs.strength_base_unit = strength_to_base(value, unit)

    # --- Form ---
    raw_form = str(item.get("form", "") or "").strip()
    fs.raw_form = raw_form
    fs.canonical_form = normalize_form(raw_form)

    # --- Company ---
    raw_company = str(item.get("company", "") or "").strip()
    fs.raw_company = raw_company
    if raw_company:
        norm = normalize_name(raw_company)
        fs.company_tokens = tokenize_name(norm)

    return fs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_code(code: str) -> str:
    """Normalize item code: strip, uppercase, remove spaces."""
    return re.sub(r"\s+", "", code.upper().strip()) if code else ""


# Lazy import to avoid circular
import re
