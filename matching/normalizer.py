"""
normalizer.py — Pharmaceutical text normalization layer.
"""
import re
from typing import Optional

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?)\s*"
    r"(MG|MCG|UG|G(?!\w)|GM|ML(?:/\w+)?|L(?!\w)|IU|I\.U\.|UNIT(?:S)?|%)",
    re.IGNORECASE,
)

_FORM_WORDS = {
    "TABLET","TABLETS","TAB","TABS","CAPSULE","CAPSULES","CAP","CAPS",
    "AMPOULE","AMPOULES","AMP","AMPS","INJECTION","INJ",
    "SYRUP","SYR","SUSPENSION","SUSP","SOLUTION","SOL",
    "DROPS","DROP","INFUSION","INF","OINTMENT","OINT","GEL","CREAM","LOTION",
    "INHALER","SPRAY","SACHET","SACH","VIAL","VIALS","PEN","FLEXPEN","PENFIL",
    "CARTRIDGE","BOTTLE","SUPPOSITORY","SUPP","PATCH","LOZENGE",
    "FILM","COATED","FILMCOATED","FC","DISPERSIBLE","EFFERVESCENT","CHEWABLE",
    "STRIP","ORAL","SUSTAINED","RELEASE","MODIFIED","EXTENDED","DELAYED",
    "CONTROLLED","ENTERIC","INTRAVENOUS","IV","IM","SC","TOPICAL",
}

_NOISE_WORDS = {
    "AND","FOR","WITH","PLUS","EXTRA","FORTE","JUNIOR","PEDIATRIC","PAEDIATRIC",
    "ADULT","SR","XR","ER","LA","MR","CR","PR","COMPOUND","COMP","COMPLEX",
    "NEW","GENERIC",
}

_WEIGHT_UNITS = {"G","GM","GRAM","MG","MCG","UG"}
_VOLUME_UNITS = {"ML","L"}
_UNIT_TO_MG   = {"G":1000.0,"GM":1000.0,"GRAM":1000.0,"MG":1.0,"MCG":0.001,"UG":0.001}
_UNIT_TO_ML   = {"ML":1.0,"L":1000.0}


def normalize_name(raw: str) -> str:
    if not raw: return ""
    s = raw.upper().strip()
    s = re.sub(r"[()]", " ", s)
    s = re.sub(r"(?<=[A-Z])-(?=[A-Z])", " ", s)
    s = re.sub(r"[^\w\s/.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_base_name(raw: str) -> str:
    if not raw: return ""
    s = normalize_name(raw)
    out = []
    for word in s.split():
        if re.match(r"^\d+$", word): continue
        if STRENGTH_RE.match(word): continue
        if re.match(r"^\d+[./]\d+", word): continue
        clean = re.sub(r"\d+$", "", word)
        alpha = re.sub(r"[^A-Z]", "", clean)
        if alpha in _FORM_WORDS: continue
        if alpha in _NOISE_WORDS: continue
        if alpha in {"MG","MCG","ML","IU","GM","UG","G","L","SR"}: continue
        if len(alpha) >= 2: out.append(alpha)
    if not out and s:
        out = [re.sub(r"[^A-Z]", "", s.split()[0])]
    return " ".join(out)


def name_tokens(raw: str) -> set:
    return {t for t in extract_base_name(raw).split() if len(t) >= 2}


def parse_strength(s: str) -> tuple:
    if not s: return None, None
    m = STRENGTH_RE.search(str(s).upper())
    if not m: return None, None
    val  = m.group(1).replace(",", ".").split("/")[0]
    unit = m.group(2).upper().replace(".", "").split("/")[0]
    unit = re.sub(r"UNITS?$", "UNIT", unit)
    try: return float(val), unit
    except ValueError: return None, None


def to_base_strength(value: float, unit: str) -> tuple:
    if unit in _WEIGHT_UNITS: return value * _UNIT_TO_MG.get(unit, 1.0), "MG"
    if unit in _VOLUME_UNITS: return value * _UNIT_TO_ML.get(unit, 1.0), "ML"
    return value, unit


# ── FIXED: strengths_match ────────────────────────────────────────────────
def strengths_match(a: str, b: str, tolerance: float = 0.02) -> bool:
    """
    Strict strength comparison.

    Rules:
    - BOTH missing  → allow match (True)   — no strength data on either side
    - ONE missing   → reject match (False) — asymmetric data = unsafe
    - BOTH present  → compare numerically within tolerance
    - Different dim → reject (MG vs ML)
    """
    va, ua = parse_strength(a)
    vb, ub = parse_strength(b)

    # Both missing → allow (no strength info available)
    if va is None and vb is None:
        return True

    # One missing → REJECT (was: return True — this was the bug)
    if va is None or vb is None:
        return False

    # Normalize to base units
    va, ua = to_base_strength(va, ua)
    vb, ub = to_base_strength(vb, ub)

    # Different dimensions → reject
    if ua != ub:
        return False

    if va == 0 and vb == 0: return True
    if va == 0 or vb == 0:  return False

    return min(va, vb) / max(va, vb) >= (1.0 - tolerance)


def normalize_form_str(raw: str) -> str:
    if not raw: return ""
    try:
        from dictionary_loader import apply_form_map
        return apply_form_map(raw)
    except ImportError:
        return raw.upper().strip()


def forms_match(a: str, b: str) -> bool:
    na = normalize_form_str(a)
    nb = normalize_form_str(b)
    if not na or not nb: return True
    return na == nb


def jaccard(a: set, b: set) -> float:
    if not a and not b: return 1.0
    if not a or not b:  return 0.0
    i = len(a & b); u = len(a | b)
    return i / u if u else 0.0


def char_similarity(s1: str, s2: str) -> float:
    if s1 == s2: return 1.0
    if not s1 or not s2: return 0.0
    a = s1.replace(" ", "")[:60]
    b = s2.replace(" ", "")[:60]
    n = len(b); prev = [0] * (n + 1)
    for ca in a:
        curr = [0] * (n + 1)
        for j, cb in enumerate(b):
            curr[j+1] = prev[j]+1 if ca == cb else max(curr[j], prev[j+1])
        prev = curr
    return 2 * prev[n] / (len(a) + len(b))


def name_similarity(raw_a: str, raw_b: str) -> float:
    ba = extract_base_name(raw_a)
    bb = extract_base_name(raw_b)
    if not ba or not bb: return 0.0
    if ba == bb: return 1.0
    j   = jaccard(set(ba.split()), set(bb.split()))
    cs  = char_similarity(ba, bb)
    short = min(ba, bb, key=len); long_ = max(ba, bb, key=len)
    prefix = 0.10 if len(short) >= 4 and long_.startswith(short) else 0.0
    return min(max(j, cs) + prefix, 1.0)
