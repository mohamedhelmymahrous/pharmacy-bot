"""
normalizer.py
-------------
Cleans and standardizes pharmaceutical text data.
Handles: names, strengths, dosage forms, units.
"""
import re

# ---------------------------------------------------------------------------
# Form synonym map → all map to a canonical form
# ---------------------------------------------------------------------------
FORM_MAP = {
    # Tablets
    "TAB": "TABLET", "TABS": "TABLET", "TABLETS": "TABLET",
    "FILM COATED": "TABLET", "FC": "TABLET", "FILMCOATED": "TABLET",
    "FILM-COATED": "TABLET", "DISPERSIBLE": "TABLET",
    # Capsules
    "CAP": "CAPSULE", "CAPS": "CAPSULE", "CAPSULES": "CAPSULE",
    "SOFTGEL": "CAPSULE", "SOFT GEL": "CAPSULE",
    # Ampoules / Injectables
    "AMP": "AMPOULE", "AMPS": "AMPOULE", "AMPOULES": "AMPOULE",
    "INJECTION": "AMPOULE", "INJ": "AMPOULE",
    "VIAL": "VIAL", "VIALS": "VIAL",
    # Liquids
    "SYR": "SYRUP", "SUSP": "SUSPENSION",
    "DROPS": "DROPS", "DROP": "DROPS",
    "SOLUTION": "SOLUTION", "SOL": "SOLUTION",
    "INFUSION": "INFUSION", "INF": "INFUSION",
    # Topical
    "OINT": "OINTMENT", "OIN": "OINTMENT",
    "CR": "CREAM",
    # Inhalers
    "MDI": "INHALER", "INHALER": "INHALER",
    "SPRAY": "SPRAY",
    # Other
    "SACHET": "SACHET", "SACH": "SACHET",
    "CARTRIDGE": "CARTRIDGE", "PEN": "PEN", "FLEXPEN": "PEN",
    "BOX": "BOX", "BOTTLE": "BOTTLE",
    "TUBE": "TUBE",
}

# ---------------------------------------------------------------------------
# Unit normalization → convert everything to base unit for comparison
# ---------------------------------------------------------------------------
UNIT_TO_BASE = {
    # Weight → base: MG
    "G": 1000.0, "GM": 1000.0, "GRAM": 1000.0,
    "MG": 1.0,
    "MCG": 0.001, "UG": 0.001, "MICROGRAM": 0.001,
    # Volume → base: ML
    "ML": 1.0, "L": 1000.0,
    # Units (kept as-is, different scale)
    "IU": 1.0, "I.U.": 1.0, "UNIT": 1.0, "UNITS": 1.0,
    # Percentage (kept as-is)
    "%": 1.0,
}

# All weight units → normalized to MG
WEIGHT_UNITS = {"G", "GM", "GRAM", "MG", "MCG", "UG", "MICROGRAM"}
# All volume units → normalized to ML
VOLUME_UNITS = {"ML", "L"}
# Units that can't be cross-compared
INCOMPARABLE_UNITS = {"%", "IU", "I.U.", "UNIT", "UNITS"}

# ---------------------------------------------------------------------------
# Noise words to strip from drug names
# ---------------------------------------------------------------------------
NOISE_WORDS = {
    "AND", "FOR", "WITH", "PLUS", "EXTRA", "FORTE", "JUNIOR",
    "PEDIATRIC", "PAEDIATRIC", "ADULT", "SR", "XR", "ER", "LA",
    "RETARD", "PROLONGED", "RELEASE", "MODIFIED", "MR",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """
    Normalize a drug name:
    - Uppercase
    - Remove punctuation except parentheses and slashes
    - Collapse whitespace
    - Remove trailing/leading noise
    Example: "Adwi-Flam  75mg" -> "ADWIFLAM"
    Note: strength/form words are NOT stripped here (done in features.py)
    """
    if not name:
        return ""
    text = name.upper().strip()
    # Remove hyphens between letters (ADWI-FLAM → ADWIFLAM)
    text = re.sub(r"(?<=[A-Z])-(?=[A-Z])", "", text)
    # Remove punctuation except ( ) / .
    text = re.sub(r"[^\w\s()/.]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_form(form: str) -> str:
    """
    Map dosage form to canonical name.
    Example: "TABS" -> "TABLET", "AMP" -> "AMPOULE"
    """
    if not form:
        return ""
    upper = form.upper().strip()
    # Try direct lookup
    if upper in FORM_MAP:
        return FORM_MAP[upper]
    # Try partial match (e.g. "TABLET 500MG" → still find TABLET)
    for key in sorted(FORM_MAP.keys(), key=len, reverse=True):
        if key in upper:
            return FORM_MAP[key]
    return upper


def parse_strength(strength_str: str) -> tuple[float | None, str | None]:
    """
    Parse a strength string into (numeric_value, unit).
    Normalizes the unit to uppercase canonical form.
    Examples:
        "75MG"      -> (75.0,  "MG")
        "0.5 MCG"   -> (0.5,   "MCG")
        "1.5MG/5ML" -> (1.5,   "MG")   ← takes the first part
        "70/30 100IU"-> (100.0, "IU")   ← finds first number+unit
        "5%"        -> (5.0,   "%")
    """
    if not strength_str:
        return None, None

    upper = strength_str.upper().strip()

    pattern = re.compile(
        r"(\d+(?:[.,]\d+)?)"
        r"\s*"
        r"(MG|MCG|UG|G(?!\w)|GM|ML|L(?!\w)|IU|I\.U\.|UNIT(?:S)?|%)",
        re.IGNORECASE,
    )
    match = pattern.search(upper)
    if not match:
        return None, None

    raw_val = match.group(1).replace(",", ".")
    raw_unit = match.group(2).upper().replace(".", "").replace("UNITS", "UNIT")

    # Normalize "I.U." → "IU"
    if raw_unit in ("IU", "I.U."):
        raw_unit = "IU"

    try:
        value = float(raw_val)
    except ValueError:
        return None, None

    return value, raw_unit


def strength_to_base(value: float, unit: str) -> tuple[float | None, str | None]:
    """
    Convert strength to base unit within its dimension group.
    Returns (base_value, base_unit) or (None, None) if incomparable.
    Examples:
        (1.0, "G")   -> (1000.0, "MG")
        (500.0, "MG")-> (500.0,  "MG")
        (0.5, "MCG") -> (0.0005, "MG")
        (5.0, "%")   -> (5.0,    "%")   ← kept as-is
    """
    if unit in INCOMPARABLE_UNITS:
        return value, unit
    if unit in WEIGHT_UNITS:
        base_mg = value * UNIT_TO_BASE.get(unit, 1.0)
        return base_mg, "MG"
    if unit in VOLUME_UNITS:
        base_ml = value * UNIT_TO_BASE.get(unit, 1.0)
        return base_ml, "ML"
    return value, unit


def tokenize_name(normalized_name: str) -> set[str]:
    """
    Split a normalized name into tokens, filtering noise words.
    Example: "ADWIFLAM FORTE" -> {"ADWIFLAM"}
    """
    tokens = set(normalized_name.split())
    # Remove noise words and single-character tokens
    tokens = {t for t in tokens if t not in NOISE_WORDS and len(t) > 1}
    return tokens
