"""Text normalisation shared by the leakage filter and the deduper."""

from __future__ import annotations

import re
import unicodedata

_ARTICLES = {"the", "a", "an", "la", "le", "les", "el", "los", "las", "der", "die", "das"}
_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, drop leading articles."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = _NON_WORD.sub(" ", ascii_only.lower())
    tokens = _SPACES.sub(" ", lowered).strip().split(" ")
    while tokens and tokens[0] in _ARTICLES:
        tokens.pop(0)
    return " ".join(tokens)


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split(" ") if t}


def looks_like_qid(value: str) -> bool:
    return bool(re.fullmatch(r"Q\d+", value))
