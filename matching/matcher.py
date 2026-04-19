"""
matcher.py
----------
Core matching engine.

Thresholds:
    >= 0.80 → exact
    0.60–0.80 → fuzzy
    < 0.60 → new

MIN_NAME_SCORE lowered to 0.25 — name tokens are now clean so
a single-token match gives Jaccard=1.0; the old 0.40 was
cutting good matches because tokens included strength/form noise.
"""
from dataclasses import dataclass
from typing import Optional

from .features import FeatureSet, extract_features
from .scorer  import compute_score, score_name

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
EXACT_THRESHOLD    = 0.75   # 0.78 = name exact + strength missing + form neutral → exact
FUZZY_THRESHOLD    = 0.55
MIN_NAME_SCORE     = 0.25   # fast-reject: skip candidates with near-zero name sim


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    match_type:       str             # "exact" | "fuzzy" | "new"
    confidence_score: float
    matched_item_id:  Optional[str]
    matched_item:     Optional[dict]
    explanation:      str

    def to_dict(self) -> dict:
        return {
            "match_type":       self.match_type,
            "confidence_score": self.confidence_score,
            "matched_item_id":  self.matched_item_id,
            "matched_item":     self.matched_item,
            "explanation":      self.explanation,
        }


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

class PharmaMatcher:
    """
    Pharmaceutical item matcher.

    Usage:
        matcher = PharmaMatcher(database)   # list of item dicts
        result  = matcher.match(new_item)
    """

    def __init__(self, database: list[dict]):
        """Pre-compute FeatureSets for entire database at init time."""
        self._db: list[tuple[dict, FeatureSet]] = []
        for item in database:
            fs = extract_features(item)
            self._db.append((item, fs))

    # ------------------------------------------------------------------

    def match(self, new_item: dict, debug: bool = False) -> MatchResult:
        """
        Match new_item against database.

        Parameters
        ----------
        new_item : dict with keys: name, strength, form, company, code
        debug    : if True, print top-5 candidates to stdout
        """
        if not self._db:
            return MatchResult("new", 0.0, None, None, "Empty database")

        new_fs = extract_features(new_item)

        candidates: list[tuple[float, dict, dict]] = []  # (score, result, item)

        for db_item, db_fs in self._db:
            # Fast reject: name must have minimum similarity
            n_score, _ = score_name(new_fs, db_fs)
            if n_score < MIN_NAME_SCORE:
                continue

            result = compute_score(new_fs, db_fs)
            candidates.append((result["total"], result, db_item))

        if not candidates:
            return MatchResult(
                "new", 0.0, None, None,
                f"No candidate passed name threshold ({MIN_NAME_SCORE}) "
                f"for '{new_fs.base_name}'"
            )

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        if debug:
            print(f"\n[MATCH] '{new_item.get('name')}' → base='{new_fs.base_name}' "
                  f"tokens={new_fs.name_tokens}")
            for sc, res, itm in candidates[:5]:
                print(f"  {sc:.3f}  {itm.get('name','?'):30s}  {res['explanation'][:80]}")

        best_score, best_result, best_item = candidates[0]

        # Decide
        if best_score >= EXACT_THRESHOLD:
            match_type = "exact"
        elif best_score >= FUZZY_THRESHOLD:
            match_type = "fuzzy"
        else:
            match_type = "new"
            best_item  = None

        return MatchResult(
            match_type       = match_type,
            confidence_score = round(best_score, 4),
            matched_item_id  = best_item.get("id") if best_item else None,
            matched_item     = best_item,
            explanation      = best_result["explanation"],
        )

    # ------------------------------------------------------------------

    def match_batch(self, items: list[dict], debug: bool = False) -> list[MatchResult]:
        return [self.match(item, debug=debug) for item in items]

    def add_to_database(self, new_item: dict):
        """Add confirmed new item to in-memory database."""
        self._db.append((new_item, extract_features(new_item)))
