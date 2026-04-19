"""
normalizer.py
-------------
Pharmaceutical text normalization.
Root cause fix: name tokens must be separated from strength/form tokens
before any similarity comparison.
"""
import re

# ---------------------------------------------------------------------------
# Form synonym → canonical
# ---------------------------------------------------------------------------
FORM_MAP = {
    "TAB": "TABLET", "TABS": "TABLET", "TABLETS": "TABLET",
    "FILM": "TABLET", "COATED": "TABLET", "FC": "TABLET",
    "FILMCOATED": "TABLET", "FILMCOAT": "TABLET",
    "DISPERSIBLE": "TABLET", "EFFERVESCENT": "TABLET",
    "CAP": "CAPSULE", "CAPS": "CAPSULE", "CAPSULES": "CAPSULE",
    "SOFTGEL": "CAPSULE", "SOFTCAP": "CAPSULE",
    "AMP": "AMPOULE", "AMPS": "AMPOULE", "AMPOULES": "AMPOULE",
    "INJECTION": "AMPOULE", "INJ": "AMPOULE", "AMPL": "AMPOULE",
    "VIAL": "VIAL", "VIALS": "VIAL",
    "SYR": "SYRUP", "SYRUP": "SYRUP",
    "SUSP": "SUSPENSION", "SUSPENSION": "SUSPENSION",
    "DROPS": "DROPS", "DROP": "DROPS", "ORAL": "DROPS",
    "SOL": "SOLUTION", "SOLUTION": "SOLUTION",
    "INF": "INFUSION", "INFUSION": "INFUSION",
    "OINT": "OINTMENT", "OINTMENT": "OINTMENT",
    "CR": "CREAM", "CREAM": "CREAM",
    "GEL": "GEL",
    "MDI": "INHALER", "INHALER": "INHALER",
    "SPRAY": "SPRAY",
    "SACHET": "SACHET", "SACH": "SACHET",
    "PEN": "PEN", "FLEXPEN": "PEN", "PENFIL": "PEN",
    "CARTRIDGE": "CARTRIDGE",
    "BOTTLE": "BOTTLE",
    "LOZENGE": "LOZENGE",
    "PATCH": "PATCH",
    "SUPPOSITORY": "SUPPOSITORY", "SUPP": "SUPPOSITORY",
    "LOTION": "LOTION",
    "STRIP": "TABLET",   # strip = tablet packaging
}

# All form-related words to STRIP from name tokens
FORM_WORDS = set(FORM_MAP.keys()) | set(FORM_MAP.values())
FORM_WORDS.update({
    "FILM", "COATED", "ORAL", "SUSTAINED", "RELEASE",
    "MODIFIED", "EXTENDED", "PROLONGED", "RETARD",
    "CHEWABLE", "CHEW", "EFFERVESCENT", "ENTERIC",
    "IMMEDIATE", "DELAYED", "CONTROLLED",
    "SCORED", "UNSCORED", "PLAIN",
    "INTRAVENOUS", "IV", "IM", "SC", "SUBCUTANEOUS",
    "INTRAMUSCULAR", "TOPICAL", "TRANSDERMAL",
    "ML", "MG", "MCG", "GM", "IU", "UG",
    "STRIP", "BOX", "PACK", "SACHET",
})

# Noise words — carry no drug identity
NOISE_WORDS = {
    "AND", "FOR", "WITH", "PLUS", "EXTRA",
    "FORTE", "JUNIOR", "PEDIATRIC", "PAEDIATRIC", "ADULT",
    "SR", "XR", "ER", "LA", "MR", "CR", "PR",
    "COMPOUND", "COMP", "COMPLEX",
    "NEW", "ORIGINAL", "GENERIC",
}

# Strength pattern
STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?)\s*"
    r"(MG|MCG|UG|G(?!\w)|GM|ML(?:/\w+)?|L(?!\w)|IU|I\.U\.|UNIT(?:S)?|%)",
    re.IGNORECASE,
)

# Unit normalization to base
WEIGHT_UNITS = {"G", "GM", "GRAM", "MG", "MCG", "UG"}
VOLUME_UNITS = {"ML", "L"}
INCOMPARABLE_UNITS = {"%", "IU", "UNIT", "UNITS"}

UNIT_TO_BASE = {
    "G": 1000.0, "GM": 1000.0, "GRAM": 1000.0,
    "MG": 1.0,
    "MCG": 0.001, "UG": 0.001,
    "ML": 1.0, "L": 1000.0,
    "IU": 1.0, "%": 1.0, "UNIT": 1.0, "UNITS": 1.0,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_drug_base_name(raw_name: str) -> str:
    """
    Extract ONLY the brand/drug name tokens from a full pharmaceutical string.
    Removes: strength values, units, dosage forms, noise words, numbers.

    Examples:
        'ADWIFLAM 75mg Ampoule 3 ml'  -> 'ADWIFLAM'
        'ALDOMET 250MG FILM COATED'   -> 'ALDOMET'
        'ALKAPRESS 5 mg Strip 10'     -> 'ALKAPRESS'
        'AMLODIPINE 10MG TABLET'      -> 'AMLODIPINE'
        'ALKAPRESS TRIO (5/12.5/160)' -> 'ALKAPRESS TRIO'
        'adwiflam'                    -> 'ADWIFLAM'
        'ALDOMET(METHYLDOPA)'         -> 'ALDOMET METHYLDOPA'
    """
    if not raw_name:
        return ""

    text = raw_name.upper().strip()

    # Normalize brackets and hyphens
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"(?<=[A-Z])-(?=[A-Z])", " ", text)  # ADWI-FLAM → ADWI FLAM
    text = re.sub(r"[^\w\s/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    words = text.split()
    name_tokens = []

    for word in words:
        # Stop at pure numbers
        if re.match(r"^\d+$", word):
            continue

        # Stop at strength pattern embedded in word (75MG → stop)
        if STRENGTH_RE.match(word):
            continue

        # Check if it's a slash-strength (1.5/5ML)
        if re.match(r"^\d+[./]\d+", word):
            continue

        # Strip trailing digits from word (AMARYL1 → AMARYL)
        clean = re.sub(r"\d+$", "", word)
        alpha = re.sub(r"[^A-Z]", "", clean)

        # Skip form words
        if alpha in FORM_WORDS or word in FORM_WORDS:
            continue

        # Skip noise words
        if alpha in NOISE_WORDS:
            continue

        # Skip pure unit words
        if alpha in {"MG", "MCG", "ML", "IU", "GM", "UG", "G", "L"}:
            continue

        # Keep if has alphabetic content (min 2 chars)
        if len(alpha) >= 2:
            name_tokens.append(alpha)

    # Fallback: if nothing survived, use first word
    if not name_tokens and words:
        name_tokens = [re.sub(r"[^A-Z]", "", words[0].upper())]

    return " ".join(name_tokens)


def tokenize_drug_name(raw_name: str) -> set[str]:
    """
    Returns a set of clean drug name tokens (no strength/form/noise).
    Used for Jaccard similarity.
    """
    base = extract_drug_base_name(raw_name)
    tokens = set(base.split())
    return {t for t in tokens if len(t) >= 2}


def normalize_form(form: str) -> str:
    """Map any dosage form variant to its canonical form."""
    if not form:
        return ""
    upper = form.upper().strip()
    if upper in FORM_MAP:
        return FORM_MAP[upper]
    # Try longest-match in the string
    for key in sorted(FORM_MAP.keys(), key=len, reverse=True):
        if key in upper:
            return FORM_MAP[key]
    return upper


def parse_strength(strength_str: str) -> tuple[float | None, str | None]:
    """
    Parse strength string → (numeric_value, unit).

    Examples:
        '75MG'        → (75.0,  'MG')
        '1.5MG/5ML'   → (1.5,   'MG')
        '5 mg'        → (5.0,   'MG')
        '0.5MCG'      → (0.5,   'MCG')
        None          → (None,  None)
    """
    if not strength_str:
        return None, None
    upper = str(strength_str).upper().strip()
    m = STRENGTH_RE.search(upper)
    if not m:
        return None, None
    raw_val = m.group(1).replace(",", ".").split("/")[0]  # take numerator
    raw_unit = m.group(2).upper().replace(".", "")
    if raw_unit in ("IU",):
        raw_unit = "IU"
    raw_unit = re.sub(r"/.*$", "", raw_unit)   # ML/5ML → ML
    try:
        return float(raw_val), raw_unit
    except ValueError:
        return None, None


def strength_to_base(value: float, unit: str) -> tuple[float, str]:
    """Convert strength to base unit within its dimension."""
    if unit in INCOMPARABLE_UNITS:
        return value, unit
    if unit in WEIGHT_UNITS:
        return value * UNIT_TO_BASE.get(unit, 1.0), "MG"
    if unit in VOLUME_UNITS:
        return value * UNIT_TO_BASE.get(unit, 1.0), "ML"
    return value, unit


def clean_text(text: str) -> str:
    """Generic text cleaner — uppercase, collapse spaces."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.upper().strip())
