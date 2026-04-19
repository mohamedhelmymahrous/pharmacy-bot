"""
matching/
---------
Pharmaceutical item identity resolution system.

Quick usage:
    from matching import PharmaMatcher

    matcher = PharmaMatcher(database)
    result = matcher.match(new_item)
    print(result.to_dict())
"""
from .matcher import PharmaMatcher, MatchResult

__all__ = ["PharmaMatcher", "MatchResult"]
