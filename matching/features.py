"""
features.py
-----------
Converts raw item dict → FeatureSet ready for scoring.
Key fix: name_tokens contains ONLY drug name tokens (no strength/form).
"""
from dataclasses import dataclass, field
from typing import Optional
from .normalizer import (
    extract_drug_base_name,
    tokenize_drug_name,
    normalize_form,
    parse_strength,
    strength_to_base,
    clean_text,
)


@dataclass
class FeatureSet:
    # Traceability
    item_id:  Optional[str] = None
    code:     Optional[str] = None

    # Name — ONLY brand/drug tokens, no strength or form
    raw_name:        str = ""
    base_name:       str = ""   # e.g. "ADWIFLAM"
    name_tokens:     set = field(default_factory=set)  # e.g. {'ADWIFLAM'}

    # Strength
    strength_raw:        str           = ""
    strength_value:      Optional[float] = None
    strength_unit:       Optional[str]   = None
    strength_base_value: Optional[float] = None
    strength_base_unit:  Optional[str]   = None

    # Form
    raw_form:      str = ""
    canonical_form: str = ""

    # Company (optional)
    company_tokens: set = field(default_factory=set)


def extract_features(item: dict) -> FeatureSet:
    """
    Convert raw item dict to FeatureSet.

    Accepts these keys (all optional except 'name'):
        name, strength, form, company, code, id, _row
    """
    fs = FeatureSet()

    # ID / code
    fs.item_id = str(item.get("id") or item.get("item_id") or "")
    fs.code    = _clean_code(str(item.get("code", "") or ""))

    # Name — extract base name tokens only
    raw_name     = str(item.get("name", "") or "").strip()
    fs.raw_name  = raw_name
    fs.base_name = extract_drug_base_name(raw_name)
    fs.name_tokens = tokenize_drug_name(raw_name)

    # Strength — first try dedicated field, then parse from name
    raw_strength = str(item.get("strength", "") or "").strip()
    if not raw_strength:
        raw_strength = raw_name   # parse strength embedded in name

    fs.strength_raw = raw_strength
    value, unit = parse_strength(raw_strength)
    fs.strength_value = value
    fs.strength_unit  = unit
    if value is not None and unit is not None:
        fs.strength_base_value, fs.strength_base_unit = strength_to_base(value, unit)

    # Form — try dedicated field, then UOM, then parse from name
    raw_form = str(item.get("form", "") or item.get("uom", "") or "").strip()
    if not raw_form:
        raw_form = raw_name   # normalize_form will pick it out
    fs.raw_form      = raw_form
    fs.canonical_form = normalize_form(raw_form)

    # Company
    raw_company = str(item.get("company", "") or "").strip()
    if raw_company:
        fs.company_tokens = tokenize_drug_name(raw_company)

    return fs


def _clean_code(code: str) -> str:
    import re
    return re.sub(r"\s+", "", code.upper()) if code else ""
