"""
scorer.py
---------
Weighted scoring engine.

Weights (sum = 1.0):
    Name     → 60%   (most important — brand name is primary identity)
    Strength → 20%
    Form     → 15%
    Company  →  5%
"""
from .features import FeatureSet

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
WEIGHTS = {
    "name":     0.60,
    "strength": 0.20,
    "form":     0.15,
    "company":  0.05,
}

STRENGTH_TOLERANCE = 0.02   # ±2% numeric tolerance


# ---------------------------------------------------------------------------
# Name scoring
# ---------------------------------------------------------------------------

def score_name(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Compare drug base names only (strength/form already stripped).

    Strategy (take highest):
    1. Exact base name match           → 1.0
    2. Token Jaccard on name_tokens    → set overlap
    3. Character LCS similarity        → handles abbreviations
    4. Prefix bonus                    → one name starts with other
    """
    if not a.base_name or not b.base_name:
        return 0.0, "missing name"

    # 1. Exact
    if a.base_name == b.base_name:
        return 1.0, f"exact: '{a.base_name}'"

    # 2. Token Jaccard (on clean name tokens)
    jaccard = _jaccard(a.name_tokens, b.name_tokens)

    # 3. Character similarity on base names
    char_sim = _char_sim(a.base_name, b.base_name)

    # 4. Prefix bonus
    prefix = 0.0
    short = min(a.base_name, b.base_name, key=len)
    long_ = max(a.base_name, b.base_name, key=len)
    if len(short) >= 4 and long_.startswith(short):
        prefix = 0.10

    score = min(max(jaccard, char_sim) + prefix, 1.0)
    exp = (f"jaccard={jaccard:.2f} char={char_sim:.2f} "
           f"prefix={prefix:.2f} → {score:.2f} "
           f"['{a.base_name}' vs '{b.base_name}']")
    return score, exp


# ---------------------------------------------------------------------------
# Strength scoring
# ---------------------------------------------------------------------------

def score_strength(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Numeric strength comparison after unit normalization.

    - Both None      → 0.5 (neutral, don't penalize)
    - One None       → 0.4 (slight uncertainty)
    - Different dims → 0.0 (MG vs ML — categorically different)
    - Within ±2%     → 1.0
    - Otherwise      → scaled by ratio
    """
    av, au = a.strength_base_value, a.strength_base_unit
    bv, bu = b.strength_base_value, b.strength_base_unit

    if av is None and bv is None:
        return 0.5, "both strength missing (neutral)"
    if av is None or bv is None:
        return 0.4, "one strength missing"
    if au != bu:
        return 0.0, f"unit mismatch: {au} vs {bu}"
    if av == 0 and bv == 0:
        return 1.0, "both zero"
    if av == 0 or bv == 0:
        return 0.0, "one is zero"

    ratio = min(av, bv) / max(av, bv)
    if ratio >= (1.0 - STRENGTH_TOLERANCE):
        return 1.0, f"match: {av}{au} ≈ {bv}{bu}"

    # Smooth penalty curve
    partial = round(0.2 + ratio * 0.8, 3)
    return partial, f"mismatch: {av}{au} vs {bv}{bu} (ratio={ratio:.2f})"


# ---------------------------------------------------------------------------
# Form scoring
# ---------------------------------------------------------------------------

def score_form(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Canonical form comparison.
    Forms are categorical — either match or they don't.
    """
    af, bf = a.canonical_form, b.canonical_form
    if not af and not bf:
        return 0.5, "both form missing (neutral)"
    if not af or not bf:
        return 0.5, "one form missing (neutral)"  # generous — form often absent in Excel
    if af == bf:
        return 1.0, f"match: {af}"
    return 0.0, f"mismatch: {af} vs {bf}"


# ---------------------------------------------------------------------------
# Company scoring
# ---------------------------------------------------------------------------

def score_company(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    if not a.company_tokens or not b.company_tokens:
        return 0.5, "company missing (neutral)"
    j = _jaccard(a.company_tokens, b.company_tokens)
    return j, f"company jaccard={j:.2f}"


# ---------------------------------------------------------------------------
# Final weighted score
# ---------------------------------------------------------------------------

def compute_score(a: FeatureSet, b: FeatureSet) -> dict:
    """
    Compute full weighted score between two FeatureSets.

    Returns dict with total, component scores, and explanation.
    """
    # Code shortcut — definitive match
    if a.code and b.code and a.code == b.code:
        return {
            "total": 1.0,
            "name_score": 1.0, "strength_score": 1.0,
            "form_score": 1.0, "company_score": 1.0,
            "explanation": f"CODE MATCH: {a.code}",
        }

    n_s, n_e = score_name(a, b)
    s_s, s_e = score_strength(a, b)
    f_s, f_e = score_form(a, b)
    c_s, c_e = score_company(a, b)

    total = (
        n_s * WEIGHTS["name"]     +
        s_s * WEIGHTS["strength"] +
        f_s * WEIGHTS["form"]     +
        c_s * WEIGHTS["company"]
    )

    explanation = (
        f"Name={n_s:.2f}×{WEIGHTS['name']} ({n_e}) | "
        f"Str={s_s:.2f}×{WEIGHTS['strength']} ({s_e}) | "
        f"Form={f_s:.2f}×{WEIGHTS['form']} ({f_e}) | "
        f"Co={c_s:.2f}×{WEIGHTS['company']} ({c_e})"
    )

    return {
        "total":          round(total, 4),
        "name_score":     round(n_s, 4),
        "strength_score": round(s_s, 4),
        "form_score":     round(f_s, 4),
        "company_score":  round(c_s, 4),
        "explanation":    explanation,
    }


# ---------------------------------------------------------------------------
# Similarity helpers (zero external deps)
# ---------------------------------------------------------------------------

def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _char_sim(s1: str, s2: str) -> float:
    """LCS-based character similarity. Handles abbreviations well."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    a = s1.replace(" ", "")[:60]
    b = s2.replace(" ", "")[:60]
    lcs = _lcs(a, b)
    return 2.0 * lcs / (len(a) + len(b))


def _lcs(a: str, b: str) -> int:
    """1-D DP LCS length."""
    n = len(b)
    prev = [0] * (n + 1)
    for ca in a:
        curr = [0] * (n + 1)
        for j, cb in enumerate(b):
            curr[j+1] = prev[j] + 1 if ca == cb else max(curr[j], prev[j+1])
        prev = curr
    return prev[n]
