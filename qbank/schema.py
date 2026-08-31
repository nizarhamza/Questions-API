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
    code = CATEGORY_CODES.get(category, category[:3].lower())
    return f"{code}-{difficulty[0]}-{index:04d}"


def write_jsonl(path: Path, questions: list[Question]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for question in questions:
            handle.write(json.dumps(question.record(), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
