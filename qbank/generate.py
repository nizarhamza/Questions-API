"""Orchestration: rows in, questions out."""

from __future__ import annotations

import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import filters
from .distractors import DistractorPool, balanced_positions, place_answer
from .patterns import Pattern
from .schema import Question, make_id
from .sparql import SparqlClient

# No single pattern may carry more than this share of a category, whatever the
# data allows. The binding constraint on a templated bank is not the data --
# it is repetition.
DEFAULT_PATTERN_CAP = 0.15

# A pattern with only a handful of surviving questions cannot dominate anything,
# but it *can* strangle its category: the cap is a share of the total, so one
# 3-item pattern among five drags a 531-question cell down to 15. Patterns below
# this size are therefore included in full and left out of the cap arithmetic.
MIN_PATTERN_FOR_CAP = 25


@dataclass
class Stats:
    pattern: str
    rows: int = 0
    dropped_multivalue: int = 0
    dropped_obscure: int = 0
    dropped_leak: int = 0
    dropped_no_distractors: int = 0
    produced: int = 0
    cutoffs: tuple[int, int] = (0, 0)
    failed_shards: list = field(default_factory=list)
    by_difficulty: Counter = field(default_factory=Counter)

    def line(self) -> str:
        return (
            f"{self.pattern:<24} rows={self.rows:<6} "
            f"multi={self.dropped_multivalue:<5} obscure={self.dropped_obscure:<6} "
            f"leak={self.dropped_leak:<5} nodist={self.dropped_no_distractors:<4} "
            f"-> {self.produced:<6} cut={self.cutoffs[0]}/{self.cutoffs[1]} "
            f"(e{self.by_difficulty['easy']}/m{self.by_difficulty['medium']}/h{self.by_difficulty['hard']})"
        )


def build_pattern(client: SparqlClient, pattern: Pattern, *, seed: int = 0,
                  refresh: bool = False, tiering: str = "percentile") -> tuple[list[Question], Stats]:
    stats = Stats(pattern=pattern.id)
    print(f"[{pattern.id}] querying...", file=sys.stderr, flush=True)

    rows: list[dict[str, str]] = []
    for fragment in pattern.shards or ("",):
        query = pattern.query.replace("{SHARD}", fragment)
        try:
            if pattern.paged:
                rows.extend(client.query_paged(
                    query, page_size=pattern.page_size, max_rows=pattern.max_rows,
                    refresh=refresh,
                ))
            else:
                rows.extend(client.query(query, refresh=refresh))
        except Exception as exc:
            # One dead shard must not lose the twelve that worked.
            stats.failed_shards.append(fragment or "(whole pattern)")
            print(f"  ! shard {fragment or 'whole'} failed: {str(exc)[:100]}",
                  file=sys.stderr, flush=True)
            if not pattern.shards:
                raise
    rows = filters.dedupe_rows(rows)
    stats.rows = len(rows)

    # The pool is built from every row, including the ones about to be dropped,
    # so a discarded second capital can never resurface as its own distractor.
    pool = DistractorPool(rows, pattern.sim_fields, numeric=pattern.numeric_object)

    kept, stats.dropped_multivalue = filters.drop_multivalued(rows)

    # Difficulty cutoffs are decided once, from this pattern's own distribution.
    eligible = [
        int(float(r.get("links", "0") or 0)) for r in kept
        if int(float(r.get("links", "0") or 0)) >= max(pattern.min_sitelinks, filters.HARD_MIN)
    ]
    if tiering == "percentile":
        easy_min, medium_min = filters.percentile_cutoffs(eligible)
    else:
        easy_min, medium_min = filters.EASY_MIN, filters.MEDIUM_MIN
    stats.cutoffs = (easy_min, medium_min)

    questions: list[Question] = []
    for row in kept:
        subject = row.get("subjectLabel", "").strip()
        answer = row.get("objectLabel", "").strip()
        qid = row.get("subject", "").rsplit("/", 1)[-1]
        if not subject or not answer or subject == qid:
            continue

        sitelinks = int(float(row.get("links", "0") or 0))
        difficulty = filters.tier_for(sitelinks, easy_min, medium_min)
        if difficulty is None or sitelinks < pattern.min_sitelinks:
            stats.dropped_obscure += 1
            continue

        rng = random.Random(f"{seed}:{pattern.id}:{qid}")
        phrasing = pattern.phrasings[rng.randrange(len(pattern.phrasings))]
        text = phrasing.format(subject=subject)

        if filters.leaks(text, subject, answer):
            stats.dropped_leak += 1
            continue

        wrong = pool.pick(row, difficulty, rng)
        if not wrong:
            stats.dropped_no_distractors += 1
            continue

        questions.append(
            Question(
                id="",  # assigned once the category bucket is final
                q=text,
                o=[answer] + wrong,
                a=0,
                e=pattern.explanation.format(subject=subject, answer=answer),
                t=[pattern.category, pattern.id],
                category=pattern.category,
                difficulty=difficulty,
                subject_qid=qid,
                pattern=pattern.id,
                sitelinks=sitelinks,
            )
        )
        stats.by_difficulty[difficulty] += 1

    stats.produced = len(questions)
    print(f"[{pattern.id}] {stats.line()}", file=sys.stderr, flush=True)
    return questions, stats


def _cap_total(counts: list[int], share: float) -> tuple[int, int]:
    """Largest total where no pattern exceeds `share` of it.

    Returns (total, per_pattern_quota). Solved by scanning the quota rather than
    binary-searching the total: `quota <= share * total(quota)` is a step
    function, not a monotonic predicate, so a binary search lands on whichever
    knife edge it happens to probe -- which is how a five-pattern category with
    2,658 available questions came out at 25.
    """
    if not counts:
        return 0, 0
    best_total, best_quota = 0, 0
    for quota in range(1, max(counts) + 1):
        total = sum(min(c, quota) for c in counts)
        if quota <= share * total + 1e-9 and total > best_total:
            best_total, best_quota = total, quota
    return best_total, best_quota


def assemble(questions: list[Question], *, seed: int = 0,
             cap: float = DEFAULT_PATTERN_CAP,
             max_patterns_per_entity: int = 3,
             target_per_cell: int | None = None) -> tuple[dict[tuple[str, str], list[Question]], dict]:
    """Dedupe globally, apply the per-pattern cap, balance answer positions."""
    rng = random.Random(seed)

    # Highest-sitelink questions first, so when the cap bites we keep the ones a
    # general audience actually recognises.
    ordered = sorted(questions, key=lambda q: (-q.sitelinks, q.pattern, q.q))

    deduper = filters.Deduper(max_patterns_per_entity=max_patterns_per_entity)
    surviving = [
        q for q in ordered
        if deduper.accept(
            subject_qid=q.subject_qid, subject=q.q, answer=q.o[0],
            question=q.q, prop=q.pattern, pattern=q.pattern,
        )
    ]

    buckets: dict[tuple[str, str], list[Question]] = defaultdict(list)
    for q in surviving:
        buckets[(q.category, q.difficulty)].append(q)

    report = {"deduped": dict(deduper.rejected), "buckets": {}}
    out: dict[tuple[str, str], list[Question]] = {}

    for key, items in sorted(buckets.items()):
        by_pattern: dict[str, list[Question]] = defaultdict(list)
        for q in items:
            by_pattern[q.pattern].append(q)

        # A category served by two patterns cannot honour a 15% cap; relax it to
        # the best that is arithmetically possible rather than silently failing.
        big = {n: v for n, v in by_pattern.items() if len(v) >= MIN_PATTERN_FOR_CAP}
        small = {n: v for n, v in by_pattern.items() if n not in big}
        if not big:  # nothing large enough to need capping
            big, small = by_pattern, {}

        share = max(cap, 1.0 / len(big))
        capped_total, per_pattern = _cap_total([len(v) for v in big.values()], share)
        total = capped_total + sum(len(v) for v in small.values())
        if target_per_cell:
            total = min(total, target_per_cell)
            per_pattern = min(per_pattern, max(1, int(share * total)))

        chosen: list[Question] = []
        for name, group in sorted(big.items()):
            chosen.extend(group[: min(len(group), per_pattern)])
        for name, group in sorted(small.items()):
            chosen.extend(group)
        rng.shuffle(chosen)
        chosen = chosen[:total]

        chosen.sort(key=lambda q: (q.pattern, q.subject_qid))
        positions = balanced_positions(len(chosen), rng)
        for index, (question, slot) in enumerate(zip(chosen, positions), start=1):
            answer, wrong = question.o[0], question.o[1:]
            question.o = place_answer(answer, wrong, slot)
            question.a = slot
            question.id = make_id(question.category, question.difficulty, index)

        out[key] = chosen
        report["buckets"][f"{key[0]}/{key[1]}"] = {
            "available": len(items),
            "written": len(chosen),
            "cap_share": round(share, 3),
            "per_pattern": per_pattern,
            "patterns": {
                name: (min(len(v), per_pattern) if name in big else len(v))
                for name, v in sorted(by_pattern.items())
            },
            "uncapped_small_patterns": sorted(small),
        }

    return out, report
