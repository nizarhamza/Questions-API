"""The distractor picker.

Wrong options are sibling rows of the same query, so they are automatically the
same type and the same register as the correct answer. That one detail is the
thing models are worst at, and here it comes for free.

Difficulty then decides how far down the similarity ranking to sample:
  easy    far from the answer   (Tokyo -> Cairo, Lima, Oslo)
  medium  same neighbourhood    (Tokyo -> Seoul, Beijing, Manila)
  hard    the near-misses       (Canberra -> Sydney, Melbourne, Brisbane)
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

from .text import normalize

# Fractions of the similarity ranking (0.0 = most similar to the answer).
BANDS = {
    "hard": (0.00, 0.20),
    "medium": (0.20, 0.55),
    "easy": (0.55, 1.00),
}
# Draw this many from the band, then keep the three closest in length. Small
# oversamples leave a visible tell: 'United States of America' against Chad,
# Peru and Mali is answerable without knowing anything.
OVERSAMPLE = 24


@dataclass(frozen=True)
class Candidate:
    label: str
    sim: tuple[tuple[str, str], ...]
    links: int
    numeric: float | None


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DistractorPool:
    """Built once per pattern from that pattern's full result set."""

    def __init__(self, rows: list[dict[str, str]], sim_fields: tuple[str, ...],
                 numeric: bool = False) -> None:
        self.sim_fields = sim_fields
        self.numeric = numeric

        # Every value that is a correct answer for a given subject, taken from
        # the rows *before* the multi-value drop -- otherwise a discarded second
        # capital could come back as a distractor for its own country.
        self.answers_by_subject: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            self.answers_by_subject[row.get("subject", "")].add(row.get("objectLabel", ""))

        best: dict[str, Candidate] = {}
        for row in rows:
            label = row.get("objectLabel", "")
            if not label or label in best:
                continue
            best[label] = Candidate(
                label=label,
                sim=tuple((f, row.get(f, "")) for f in sim_fields),
                links=int(_to_float(row.get("links", "0")) or 0),
                numeric=_to_float(label) if numeric else None,
            )
        self.candidates: list[Candidate] = list(best.values())

    # ------------------------------------------------------------------ scoring

    def _score(self, candidate: Candidate, answer: Candidate) -> float:
        if self.numeric and candidate.numeric is not None and answer.numeric is not None:
            return 3.0 / (1.0 + abs(candidate.numeric - answer.numeric))

        answer_sim = dict(answer.sim)
        matches = sum(
            1 for field, value in candidate.sim if value and value == answer_sim.get(field)
        )
        pop_a, pop_c = max(answer.links, 1), max(candidate.links, 1)
        closeness = 1.0 / (1.0 + abs(math.log10(pop_a) - math.log10(pop_c)))
        return matches + closeness

    # ------------------------------------------------------------------- picking

    def pick(self, row: dict[str, str], difficulty: str, rng: random.Random,
             count: int = 3) -> list[str] | None:
        answer_label = row.get("objectLabel", "")
        forbidden = {normalize(answer_label)}
        # Hard constraint: a distractor must not also be a correct answer here.
        forbidden |= {normalize(v) for v in self.answers_by_subject.get(row.get("subject", ""), set())}

        answer = Candidate(
            label=answer_label,
            sim=tuple((f, row.get(f, "")) for f in self.sim_fields),
            links=int(_to_float(row.get("links", "0")) or 0),
            numeric=_to_float(answer_label) if self.numeric else None,
        )

        pool = [c for c in self.candidates if normalize(c.label) not in forbidden]
        if len(pool) < count:
            return None

        pool.sort(key=lambda c: self._score(c, answer), reverse=True)
        lo_frac, hi_frac = BANDS[difficulty]
        lo, hi = int(len(pool) * lo_frac), int(len(pool) * hi_frac)
        band = pool[lo:hi]
        if len(band) < count:  # narrow category (six continents, say) -- widen
            band = pool

        sample = rng.sample(band, min(OVERSAMPLE, len(band)))
        # Filter 'length tell': a correct answer noticeably longer than the
        # others is the single most common leak, so bias toward similar lengths.
        sample.sort(key=lambda c: abs(len(c.label) - len(answer_label)))
        return [c.label for c in sample[:count]]


def place_answer(answer: str, distractors: list[str], index: int) -> list[str]:
    """Put the answer at a chosen index, distractors around it, order preserved."""
    options = list(distractors)
    options.insert(index, answer)
    return options


def balanced_positions(total: int, rng: random.Random, slots: int = 4) -> list[int]:
    """A shuffled, near-uniform sequence of answer indices.

    Models favour index 0 and 2; templated banks favour whatever the code does.
    Assigning positions from a balanced sequence removes the tell entirely.
    """
    reps = (total + slots - 1) // slots
    positions = [i for i in range(slots) for _ in range(reps)][:total]
    rng.shuffle(positions)
    return positions
