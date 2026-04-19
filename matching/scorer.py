"""
scorer.py
---------
Weighted scoring engine.

Weights:
    Name       → 40%
    Strength   → 30%
    Form       → 20%
    Company    → 10%

Each component returns a score in [0.0, 1.0].
Final score is the weighted average.
"""
from .features import FeatureSet
from typing import Optional
import math

# ---------------------------------------------------------------------------
# Weights (must sum to 1.0)
# ---------------------------------------------------------------------------
WEIGHTS = {
    "name": 0.40,
    "strength": 0.30,
    "form": 0.20,
    "company": 0.10,
}

# Tolerance for numeric strength comparison (±2%)
STRENGTH_TOLERANCE = 0.02

# ---------------------------------------------------------------------------
# Individual scorers
# ---------------------------------------------------------------------------

def score_name(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Compare two drug names.
    Strategy:
    1. If normalized names are identical → 1.0
    2. Token Jaccard similarity
    3. Character-level similarity (for abbreviated names)
    Returns (score, explanation)
    """
    if not a.normalized_name or not b.normalized_name:
        return 0.0, "missing name"

    # 1. Exact normalized match
    if a.normalized_name == b.normalized_name:
        return 1.0, f"exact name match: '{a.normalized_name}'"

    # 2. Token Jaccard
    jaccard = _jaccard(a.name_tokens, b.name_tokens)

    # 3. Character-level similarity (handles abbreviations)
    char_sim = _char_similarity(a.normalized_name, b.normalized_name)

    # 4. Prefix match bonus (one name starts with the other)
    prefix_bonus = 0.0
    short = min(a.normalized_name, b.normalized_name, key=len)
    long_ = max(a.normalized_name, b.normalized_name, key=len)
    if long_.startswith(short) and len(short) >= 4:
        prefix_bonus = 0.1

    # Take the best signal
    raw = max(jaccard, char_sim) + prefix_bonus
    score = min(raw, 1.0)

    explanation = (
        f"name jaccard={jaccard:.2f} char_sim={char_sim:.2f} "
        f"prefix_bonus={prefix_bonus:.2f} → {score:.2f}"
    )
    return score, explanation


def score_strength(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Compare strengths numerically after unit normalization.

    Rules:
    - Both missing → neutral (0.5, doesn't penalize)
    - One missing → partial penalty (0.3)
    - Different unit dimensions (MG vs ML) → 0.0
    - Same dimension, within tolerance → 1.0
    - Same dimension, outside tolerance → scaled by ratio
    """
    a_val, a_unit = a.strength_base_value, a.strength_base_unit
    b_val, b_unit = b.strength_base_value, b.strength_base_unit

    # Both missing
    if a_val is None and b_val is None:
        return 0.5, "both strengths missing (neutral)"

    # One missing
    if a_val is None or b_val is None:
        return 0.3, "one strength missing"

    # Different unit dimensions (e.g. MG vs ML vs IU)
    if a_unit != b_unit:
        return 0.0, f"incompatible units: {a_unit} vs {b_unit}"

    # Numeric comparison within tolerance
    if a_val == 0 and b_val == 0:
        return 1.0, "both zero"

    if a_val == 0 or b_val == 0:
        return 0.0, "one is zero"

    ratio = min(a_val, b_val) / max(a_val, b_val)

    if ratio >= (1.0 - STRENGTH_TOLERANCE):
        return 1.0, f"strength match: {a_val}{a_unit} ≈ {b_val}{b_unit}"

    # Partial score based on how close they are
    # ratio of 0.5 → score of 0.3, ratio of 0.9 → score of 0.8
    partial = 0.3 + (ratio * 0.7)
    return round(partial, 3), f"strength mismatch: {a_val}{a_unit} vs {b_val}{b_unit} (ratio={ratio:.2f})"


def score_form(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Compare canonical dosage forms.

    Rules:
    - Both missing → neutral (0.5)
    - One missing → slight penalty (0.4)
    - Exact canonical match → 1.0
    - No match → 0.0
    (No partial score: forms are categorically different)
    """
    a_form = a.canonical_form
    b_form = b.canonical_form

    if not a_form and not b_form:
        return 0.5, "both forms missing (neutral)"
    if not a_form or not b_form:
        return 0.4, "one form missing"
    if a_form == b_form:
        return 1.0, f"form match: {a_form}"
    return 0.0, f"form mismatch: {a_form} vs {b_form}"


def score_company(a: FeatureSet, b: FeatureSet) -> tuple[float, str]:
    """
    Compare company names via token Jaccard.
    Optional field — missing from either side gives neutral 0.5.
    """
    if not a.company_tokens and not b.company_tokens:
        return 0.5, "both companies missing (neutral)"
    if not a.company_tokens or not b.company_tokens:
        return 0.5, "one company missing (neutral)"

    j = _jaccard(a.company_tokens, b.company_tokens)
    return j, f"company jaccard={j:.2f}"


# ---------------------------------------------------------------------------
# Final weighted score
# ---------------------------------------------------------------------------

def compute_score(a: FeatureSet, b: FeatureSet) -> dict:
    """
    Compute full weighted similarity score between two FeatureSets.

    Returns:
    {
        "total": float,          # 0.0 - 1.0
        "name_score": float,
        "strength_score": float,
        "form_score": float,
        "company_score": float,
        "explanation": str,
    }
    """
    # Code shortcut — same non-empty code = definitive match
    if a.code and b.code and a.code == b.code:
        return {
            "total": 1.0,
            "name_score": 1.0,
            "strength_score": 1.0,
            "form_score": 1.0,
            "company_score": 1.0,
            "explanation": f"EXACT CODE MATCH: {a.code}",
        }

    n_score, n_exp = score_name(a, b)
    s_score, s_exp = score_strength(a, b)
    f_score, f_exp = score_form(a, b)
    c_score, c_exp = score_company(a, b)

    total = (
        n_score * WEIGHTS["name"] +
        s_score * WEIGHTS["strength"] +
        f_score * WEIGHTS["form"] +
        c_score * WEIGHTS["company"]
    )

    explanation = (
        f"[Name {n_score:.2f}×{WEIGHTS['name']}] {n_exp} | "
        f"[Strength {s_score:.2f}×{WEIGHTS['strength']}] {s_exp} | "
        f"[Form {f_score:.2f}×{WEIGHTS['form']}] {f_exp} | "
        f"[Company {c_score:.2f}×{WEIGHTS['company']}] {c_exp}"
    )

    return {
        "total": round(total, 4),
        "name_score": round(n_score, 4),
        "strength_score": round(s_score, 4),
        "form_score": round(f_score, 4),
        "company_score": round(c_score, 4),
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Similarity helpers (no external dependencies)
# ---------------------------------------------------------------------------

def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _char_similarity(s1: str, s2: str) -> float:
    """
    Character-level similarity using a simplified edit-distance ratio.
    Based on the same idea as difflib.SequenceMatcher but without the library.
    Score = 2 * LCS_length / (len(s1) + len(s2))
    """
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    # Remove spaces for character-level comparison
    a = s1.replace(" ", "")
    b = s2.replace(" ", "")

    lcs_len = _lcs_length(a, b)
    return (2.0 * lcs_len) / (len(a) + len(b))


def _lcs_length(a: str, b: str) -> int:
    """
    Longest Common Subsequence length.
    Optimized with 1D DP array.
    """
    # Limit length to avoid O(n²) slowness on very long strings
    a = a[:60]
    b = b[:60]

    n = len(b)
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for ch_a in a:
        for j, ch_b in enumerate(b):
            if ch_a == ch_b:
                curr[j + 1] = prev[j] + 1
            else:
                curr[j + 1] = max(curr[j], prev[j + 1])
        prev, curr = curr, [0] * (n + 1)

    return prev[n]
