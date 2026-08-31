"""The shipped record format, and the id scheme."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DIFFICULTIES = ("easy", "medium", "hard")

# Three-letter prefixes used in question ids. Keep these locked; renumbering an
# existing bank is the one migration that is genuinely painful.
CATEGORY_CODES = {
    "geography": "geo",
    "science": "sci",
    "film": "flm",
    "music": "mus",
    "literature": "lit",
    "art": "art",
    "history": "his",
}

# Engine C imports questions in categories Engine A never produces (OpenTDB has
# ~24). These codes are purely additive -- they never renumber a category that
# already exists -- so the "locked" rule on CATEGORY_CODES still holds. Kept in
# a separate table so the provenance of a code is obvious at a glance. The
# numeric ids these slugs map to live in api/scripts/build-data.mjs (CATEGORY_META)
# and in qbank/opentdb.py (OTDB_CATEGORIES); keep the three in step.
IMPORTED_CATEGORY_CODES = {
    "general": "gen",
    "theatre": "thr",
    "television": "tel",
    "videogames": "vgm",
    "boardgames": "bgm",
    "computers": "cmp",
    "mathematics": "mth",
    "mythology": "myt",
    "sports": "spo",
    "politics": "pol",
    "celebrities": "cel",
    "animals": "ani",
    "vehicles": "veh",
    "comics": "cmc",
    "gadgets": "gad",
    "anime": "anm",
    "cartoons": "crt",
}

_ALL_CATEGORY_CODES = {**CATEGORY_CODES, **IMPORTED_CATEGORY_CODES}


@dataclass
class Question:
    id: str
    q: str                       # question text
    o: list[str]                 # 4 options
    a: int                       # index of the correct option in o
    e: str                       # provenance / short explanation
    t: list[str]                 # tags: [category, pattern id]
    category: str
    difficulty: str
    subject_qid: str = ""
    pattern: str = ""
    sitelinks: int = 0
    warnings: list[str] = field(default_factory=list)

    def record(self) -> dict:
        """The shape written to disk. Bookkeeping fields stay out of it."""
        return {
            "id": self.id,
            "q": self.q,
            "o": self.o,
            "a": self.a,
            "e": self.e,
            "t": self.t,
        }


def make_id(category: str, difficulty: str, index: int) -> str:
    code = _ALL_CATEGORY_CODES.get(category, category[:3].lower())
    return f"{code}-{difficulty[0]}-{index:04d}"


def write_jsonl(path: Path, questions: list[Question]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the shards are a committed artifact whose sha256 is recorded
    # in manifest.json and re-verified on Linux CI. Text mode on Windows would
    # write CRLF, so the manifest hashes would only match a Windows checkout.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            handle.write(json.dumps(question.record(), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
