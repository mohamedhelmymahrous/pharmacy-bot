"""
matcher.py
----------
Core matching engine.

Given a new item and a database of existing items,
finds the best match and returns a structured decision.
"""
from .features import FeatureSet, extract_features
from .scorer import compute_score
from typing import Optional

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
EXACT_THRESHOLD = 0.85
FUZZY_THRESHOLD = 0.70

# Minimum name score to even consider a match
# (prevents matching totally different drugs with same strength)
MIN_NAME_SCORE = 0.40

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
from dataclasses import dataclass


@dataclass
class MatchResult:
    match_type: str           # "exact" | "fuzzy" | "new"
    confidence_score: float   # 0.0 - 1.0
    matched_item_id: Optional[str]
    matched_item: Optional[dict]
    explanation: str

    def to_dict(self) -> dict:
        return {
            "match_type": self.match_type,
            "confidence_score": self.confidence_score,
            "matched_item_id": self.matched_item_id,
            "matched_item": self.matched_item,
            "explanation": self.explanation,
        }


# ---------------------------------------------------------------------------
# Main matcher
# ---------------------------------------------------------------------------

class PharmaMatcher:
    """
    Pharmaceutical item matcher.

    Usage:
        matcher = PharmaMatcher(database)
        result = matcher.match(new_item)

    database: list of dicts, each with keys:
        id, name, strength, form, company (optional), code (optional)
    """

    def __init__(self, database: list[dict]):
        """
        Pre-computes FeatureSets for all database items at init time.
        This avoids recomputing on every match call.
        """
        self._db: list[tuple[dict, FeatureSet]] = []
        for item in database:
            fs = extract_features(item)
            self._db.append((item, fs))

    def match(self, new_item: dict) -> MatchResult:
        """
        Match a new item against the database.

        Returns MatchResult with:
        - match_type: "exact" | "fuzzy" | "new"
        - confidence_score: best score found
        - matched_item_id: id of best match (or None)
        - matched_item: full dict of best match (or None)
        - explanation: human-readable reasoning
        """
        if not self._db:
            return MatchResult(
                match_type="new",
                confidence_score=0.0,
                matched_item_id=None,
                matched_item=None,
                explanation="Database is empty.",
            )

        new_fs = extract_features(new_item)

        best_score = -1.0
        best_result = None
        best_item = None

        for db_item, db_fs in self._db:
            # Fast reject: if name score too low, skip full scoring
            from .scorer import score_name
            name_s, _ = score_name(new_fs, db_fs)
            if name_s < MIN_NAME_SCORE:
                continue

            result = compute_score(new_fs, db_fs)
            total = result["total"]

            if total > best_score:
                best_score = total
                best_result = result
                best_item = db_item

        # Nothing passed the name filter
        if best_result is None:
            return MatchResult(
                match_type="new",
                confidence_score=0.0,
                matched_item_id=None,
                matched_item=None,
                explanation="No candidate passed minimum name similarity threshold.",
            )

        # Decide match type
        if best_score >= EXACT_THRESHOLD:
            match_type = "exact"
        elif best_score >= FUZZY_THRESHOLD:
            match_type = "fuzzy"
        else:
            match_type = "new"
            best_item = None

        return MatchResult(
            match_type=match_type,
            confidence_score=round(best_score, 4),
            matched_item_id=best_item.get("id") if best_item else None,
            matched_item=best_item if best_item else None,
            explanation=best_result["explanation"],
        )

    def match_batch(self, items: list[dict]) -> list[MatchResult]:
        """Match a list of items against the database."""
        return [self.match(item) for item in items]

    def add_to_database(self, new_item: dict):
        """Add a confirmed new item to the in-memory database."""
        fs = extract_features(new_item)
        self._db.append((new_item, fs))
