"""The five filters, plus the sitelink-based difficulty tiering.

Each of these will bite you if skipped, so they run unconditionally and the
generator reports how many rows each one removed.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .text import normalize, tokens

# wikibase:sitelinks = how many language Wikipedias have an article on the
# entity. It is the best free proxy for "would a general audience recognise
# this", and unlike asking a model it does not drift between runs.
EASY_MIN = 150
MEDIUM_MIN = 60
HARD_MIN = 25

MIN_SHARED_TOKEN_LEN = 5

# What share of a pattern's entities each tier gets when tiering by percentile.
# Deliberately not uniform: the recognisable end of any distribution is thin.
EASY_SHARE = 0.20
MEDIUM_SHARE = 0.35


def difficulty_for(sitelinks: int) -> str | None:
    """None means: too obscure to ship. This single threshold is the difference
    between a usable bank and 5,000 questions about Estonian municipalities."""
    if sitelinks >= EASY_MIN:
        return "easy"
    if sitelinks >= MEDIUM_MIN:
        return "medium"
    if sitelinks >= HARD_MIN:
        return "hard"
    return None


def percentile_cutoffs(
    sitelinks: list[int], easy_share: float = EASY_SHARE, medium_share: float = MEDIUM_SHARE
) -> tuple[int, int]:
    """Sitelink cutoffs taken from one pattern's own popularity distribution.

    Absolute thresholds do not discriminate inside a pattern whose entities are
    uniformly famous -- every sovereign state clears 150 sitelinks, so a fixed
    cut puts the whole of `capital-of` in `easy`. Ranking within the pattern
    keeps a usable spread everywhere, at the cost of `hard` meaning something
    slightly different in geography than it does in art.
    """
    if not sitelinks:
        return EASY_MIN, MEDIUM_MIN
    ranked = sorted(sitelinks, reverse=True)
    last = len(ranked) - 1
    easy_at = min(last, max(0, int(len(ranked) * easy_share) - 1))
    medium_at = min(last, max(0, int(len(ranked) * (easy_share + medium_share)) - 1))
    return ranked[easy_at], ranked[medium_at]


def tier_for(sitelinks: int, easy_min: int, medium_min: int) -> str | None:
    if sitelinks < HARD_MIN:
        return None
    if sitelinks >= easy_min:
        return "easy"
    if sitelinks >= medium_min:
        return "medium"
    return "hard"


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse rows duplicated by an OPTIONAL with several values."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("subject", ""), row.get("objectLabel", ""))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def drop_multivalued(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Filter 1. Bolivia has two capitals; Everest is in two countries. Those
    produce questions with two correct answers, and a user will find them."""
    by_subject: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_subject[row["subject"]].append(row)
    kept = [group[0] for group in by_subject.values() if len(group) == 1]
    return kept, len(rows) - len(kept)


def _contains_phrase(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?:^| ){re.escape(needle)}(?: |$)", haystack) is not None


def leaks(question: str, subject: str, answer: str) -> bool:
    """Filter 4. 'What is the capital of Mexico?' -> Mexico City. Whole-word
    matching, so a one-letter answer like the symbol 'O' is not a false hit."""
    q_norm, s_norm, a_norm = normalize(question), normalize(subject), normalize(answer)
    if _contains_phrase(q_norm, a_norm) or _contains_phrase(a_norm, s_norm):
        return True
    if _contains_phrase(s_norm, a_norm):
        return True
    shared = tokens(subject) & tokens(answer)
    return any(len(token) >= MIN_SHARED_TOKEN_LEN for token in shared)


class Deduper:
    """Filter 5 plus text-level duplicates.

    Three things get tracked: the exact fact, the question wording, and how many
    different patterns have already used the same entity. The last one is what
    stops one famous country carrying nine near-identical questions.
    """

    def __init__(self, max_patterns_per_entity: int = 3) -> None:
        self.max_patterns_per_entity = max_patterns_per_entity
        self._facts: set[tuple[str, str]] = set()
        self._texts: set[str] = set()
        self._pairs: set[frozenset[str]] = set()
        self._entity_patterns: dict[str, set[str]] = defaultdict(set)
        self.rejected: dict[str, int] = defaultdict(int)

    def accept(self, *, subject_qid: str, subject: str, answer: str,
               question: str, prop: str, pattern: str) -> bool:
        fact = (subject_qid, prop)
        if fact in self._facts:
            self.rejected["duplicate-fact"] += 1
            return False

        text = normalize(question)
        if text in self._texts:
            self.rejected["duplicate-text"] += 1
            return False

        # 'What is the capital of Japan?' and 'Tokyo is in which country?' test
        # the same knowledge from opposite ends. Same unordered pair, so one key.
        pair = frozenset({normalize(subject), normalize(answer)})
        if len(pair) == 2 and pair in self._pairs:
            self.rejected["inverse-fact"] += 1
            return False

        used = self._entity_patterns[subject_qid]
        if pattern not in used and len(used) >= self.max_patterns_per_entity:
            self.rejected["entity-overused"] += 1
            return False

        self._facts.add(fact)
        self._texts.add(text)
        self._pairs.add(pair)
        used.add(pattern)
        return True
