"""
matcher.py — ERP-style pharmaceutical matching engine.

Pipeline:
  Step 1: Code shortcut (exact code = instant exact match)
  Step 2: NAME filter (fuzzy, threshold 0.60)
  Step 3: FORM filter (strict, skip if either missing)
  Step 4: STRENGTH filter (strict, skip if either missing)
  Step 5: Score + company bonus → classify exact/fuzzy/new
"""
import logging
from dataclasses import dataclass
from typing import Optional

from .normalizer import (
    name_similarity, extract_base_name, strengths_match,
    forms_match, normalize_form_str, parse_strength, jaccard,
)

logger = logging.getLogger(__name__)

NAME_THRESHOLD = 0.60
EXACT_NAME_MIN = 0.95


@dataclass
class MatchResult:
    match_type:       str
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


class PharmaMatcher:

    def __init__(self, database: list):
        self._db = list(database)
        try:
            from dictionary_loader import load_dictionary
            load_dictionary()
        except ImportError:
            logger.warning("dictionary_loader not found — aliases disabled")

    # ------------------------------------------------------------------ #

    def match(self, item: dict, debug: bool = False) -> MatchResult:
        if not self._db:
            return MatchResult("new", 0.0, None, None, "Empty database")

        pdf_name     = _alias(str(item.get("name","") or ""))
        pdf_strength = str(item.get("strength","") or "") or pdf_name
        pdf_form     = str(item.get("form","") or item.get("uom","") or "")
        pdf_company  = str(item.get("company","") or "")
        pdf_code     = str(item.get("code","") or "").strip()
        pdf_base     = extract_base_name(pdf_name)

        if debug:
            logger.debug(
                f"\n{'─'*55}\n"
                f"INPUT    : {item.get('name')}\n"
                f"BASE     : {pdf_base}\n"
                f"FORM_N   : {normalize_form_str(pdf_form)}\n"
                f"STR_N    : {parse_strength(pdf_strength)}\n"
            )

        # Step 1 — Code shortcut
        if pdf_code:
            for db in self._db:
                if str(db.get("code","") or "").strip() == pdf_code:
                    if debug: logger.debug("  CODE MATCH")
                    return MatchResult("exact",1.0,db.get("id"),db,
                                       f"Code match: {pdf_code}")

        # Step 2 — Name filter
        cands = []
        for db in self._db:
            db_name = _alias(str(db.get("name","") or ""))
            sim = name_similarity(pdf_name, db_name)
            if sim >= NAME_THRESHOLD:
                cands.append({"item":db,"sim":sim,"db_name":db_name,"notes":[]})

        if debug:
            logger.debug(f"  Step1 NAME : {len(cands)} cands (≥{NAME_THRESHOLD})")
            for c in sorted(cands,key=lambda x:-x["sim"])[:5]:
                logger.debug(f"    {c['sim']:.2f}  {c['db_name']}")

        if not cands:
            _log_unk(item.get("name",""))
            return MatchResult("new",0.0,None,None,
                               f"No name match ≥{NAME_THRESHOLD} for '{pdf_base}'")

        # Step 3 — Form filter
        form_ok = [c for c in cands
                   if forms_match(pdf_form, str(c["item"].get("form","") or
                                                c["item"].get("uom","") or ""))]
        if debug:
            logger.debug(f"  Step2 FORM : {len(form_ok)} survived "
                         f"(pdf_form='{normalize_form_str(pdf_form)}')")

        working = form_ok if form_ok else cands   # don't block on missing form

        # Step 4 — Strength filter
        str_ok = []
        for c in working:
            db_str = str(c["item"].get("strength","") or
                         c["item"].get("name","") or "")
            if strengths_match(pdf_strength, db_str):
                c["notes"].append(
                    f"str({parse_strength(pdf_strength)}"
                    f"≈{parse_strength(db_str)})")
                str_ok.append(c)
            else:
                if debug:
                    logger.debug(
                        f"  STR REJECT : '{c['db_name']}' "
                        f"pdf={parse_strength(pdf_strength)} "
                        f"db={parse_strength(db_str)}")

        if debug:
            logger.debug(f"  Step3 STR  : {len(str_ok)} survived")

        if not str_ok:
            _log_unk(item.get("name",""))
            return MatchResult("new",0.0,None,None,
                               f"Strength mismatch eliminated all candidates. "
                               f"PDF={parse_strength(pdf_strength)}")

        # Step 5 — Company bonus + pick best
        for c in str_ok:
            score = c["sim"]
            db_co = str(c["item"].get("company","") or "")
            if pdf_company and db_co and _co_match(pdf_company, db_co):
                score = min(score + 0.05, 1.0)
                c["notes"].append("company+")
            c["score"] = score

        str_ok.sort(key=lambda x: -x["score"])
        best = str_ok[0]

        match_type = "exact" if best["sim"] >= EXACT_NAME_MIN else "fuzzy"
        expl = (f"name_sim={best['sim']:.2f} "
                f"'{pdf_base}'→'{extract_base_name(best['db_name'])}' "
                + " ".join(best["notes"]))

        if debug:
            logger.debug(
                f"  RESULT   : {match_type.upper()} score={best['score']:.2f}\n"
                f"  MATCHED  : {best['item'].get('name')}\n"
                f"  REASON   : {expl}\n"
            )

        return MatchResult(
            match_type       = match_type,
            confidence_score = round(best["score"], 4),
            matched_item_id  = best["item"].get("id"),
            matched_item     = best["item"],
            explanation      = expl,
        )

    def match_batch(self, items: list, debug: bool = False) -> list:
        return [self.match(i, debug=debug) for i in items]

    def add_to_database(self, item: dict):
        self._db.append(item)


# ── helpers ──────────────────────────────────────────────────────────── #

def _alias(name: str) -> str:
    try:
        from dictionary_loader import apply_alias
        return apply_alias(name)
    except ImportError:
        return name.upper().strip()


def _log_unk(name: str):
    if not name: return
    try:
        from dictionary_loader import log_unknown
        log_unknown(name)
    except ImportError:
        pass


def _co_match(a: str, b: str) -> bool:
    ta = {t for t in a.upper().split() if len(t) >= 3}
    tb = {t for t in b.upper().split() if len(t) >= 3}
    return bool(ta and tb and jaccard(ta,tb) >= 0.5)
