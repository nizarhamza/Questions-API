"""Command line: count (yield survey), generate, qa."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

from . import patterns as pattern_mod
from .filters import EASY_MIN, HARD_MIN, MEDIUM_MIN
from .generate import DEFAULT_PATTERN_CAP, assemble, build_pattern
from .schema import read_jsonl, write_jsonl
from .sparql import SparqlClient
from .text import normalize

ORDER_BY_TAIL = re.compile(r"\border\s+by\b[^}]*$", re.IGNORECASE)
LENGTH_TELL_RATIO = 1.5
# Some tells are unavoidable -- a few countries and composers simply have long
# names -- so QA fails on the rate, not on the existence of any.
LENGTH_TELL_MAX_SHARE = 0.02


def _client(args) -> SparqlClient:
    return SparqlClient(use_cache=not args.no_cache, verbose=not args.quiet)


# --------------------------------------------------------------------- count

def _count_query(pattern, threshold: int) -> str:
    inner = ORDER_BY_TAIL.sub("", pattern.query).strip()
    return (
        "SELECT (COUNT(DISTINCT ?subject) AS ?n) WHERE {\n"
        f"  {{ {inner} }}\n"
        f"  FILTER(?links >= {threshold})\n"
        "}"
    )


def cmd_count(args) -> int:
    """The yield matrix. Half an hour of this beats any estimate, mine included."""
    client = _client(args)
    thresholds = [int(t) for t in args.thresholds.split(",")]
    chosen = pattern_mod.resolve(args.patterns)

    header = f"{'pattern':<24}{'category':<12}" + "".join(f">={t:<8}" for t in thresholds)
    print(header)
    print("-" * len(header))
    for pattern in chosen:
        cells = []
        for threshold in thresholds:
            try:
                rows = client.query(_count_query(pattern, threshold), refresh=args.no_cache)
                cells.append(rows[0]["n"] if rows else "?")
            except Exception as exc:  # a timeout on one pattern must not kill the survey
                print(f"  ! {pattern.id} @{threshold}: {exc}", file=sys.stderr)
                cells.append("err")
        print(f"{pattern.id:<24}{pattern.category:<12}" + "".join(f"{c:<10}" for c in cells))
    return 0


# ------------------------------------------------------------------ generate

def cmd_generate(args) -> int:
    client = _client(args)
    chosen = pattern_mod.resolve(args.patterns)
    out_root = Path(args.out)

    produced, all_stats = [], []
    for pattern in chosen:
        try:
            questions, stats = build_pattern(
                client, pattern, seed=args.seed, refresh=args.no_cache,
                tiering=args.tiering,
            )
        except Exception as exc:
            print(f"  ! {pattern.id} failed: {exc}", file=sys.stderr)
            continue
        produced.extend(questions)
        all_stats.append(stats)

    if not produced:
        print("no questions produced", file=sys.stderr)
        return 1

    buckets, report = assemble(
        produced, seed=args.seed, cap=args.cap,
        max_patterns_per_entity=args.max_patterns_per_entity,
        target_per_cell=args.target_per_cell,
    )

    shards = []
    for (category, difficulty), questions in sorted(buckets.items()):
        path = out_root / "imported" / "wikidata" / category / f"{difficulty}.jsonl"
        write_jsonl(path, questions)
        raw = path.read_bytes()
        shards.append({
            "path": str(path.relative_to(out_root)).replace("\\", "/"),
            "category": category,
            "difficulty": difficulty,
            "count": len(questions),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })

    manifest = {
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "generator": "qbank engine-a",
        "source": "Wikidata (CC0)",
        "seed": args.seed,
        "difficulty_thresholds": {"easy": EASY_MIN, "medium": MEDIUM_MIN, "hard": HARD_MIN},
        "total": sum(s["count"] for s in shards),
        "shards": shards,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "report.json").write_text(
        json.dumps({"patterns": [vars(s) | {"by_difficulty": dict(s.by_difficulty)} for s in all_stats],
                    "assembly": report}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print()
    for stats in all_stats:
        print(stats.line())
    print()
    print("dropped in assembly:", report["deduped"])
    for name, info in report["buckets"].items():
        print(f"{name:<22} {info['written']:>6} written  (of {info['available']} available, "
              f"cap {info['cap_share']*100:.0f}% = {info['per_pattern']}/pattern)")
    print(f"\ntotal: {manifest['total']} questions -> {out_root}")
    return 0


# ------------------------------------------------------------------------ qa

def cmd_qa(args) -> int:
    paths = sorted(Path(args.path).rglob("*.jsonl")) if Path(args.path).is_dir() else [Path(args.path)]
    failures = 0

    for path in paths:
        records = read_jsonl(path)
        if not records:
            continue
        positions = Counter(r["a"] for r in records)
        expected = len(records) / 4
        skew = max(abs(positions[i] - expected) for i in range(4)) / max(expected, 1)

        length_tells, option_dupes, bad_answer = [], [], []
        for record in records:
            options = record["o"]
            if len(options) != 4 or len(set(map(normalize, options))) != 4:
                option_dupes.append(record["id"])
            if not 0 <= record["a"] < len(options):
                bad_answer.append(record["id"])
                continue
            correct = options[record["a"]]
            others = [o for i, o in enumerate(options) if i != record["a"]]
            mean_other = statistics.mean(len(o) for o in others) or 1
            if len(correct) > LENGTH_TELL_RATIO * mean_other:
                length_tells.append(record["id"])

        texts = Counter(normalize(r["q"]) for r in records)
        duplicates = [t for t, n in texts.items() if n > 1]

        problems = []
        if skew > 0.10:
            problems.append(f"answer-position skew {skew*100:.1f}%")
        if option_dupes:
            problems.append(f"{len(option_dupes)} with duplicate options")
        if bad_answer:
            problems.append(f"{len(bad_answer)} with out-of-range answer index")
        tell_share = len(length_tells) / len(records)
        if tell_share > LENGTH_TELL_MAX_SHARE:
            problems.append(f"{len(length_tells)} length tells ({tell_share*100:.1f}%)")
        if duplicates:
            problems.append(f"{len(duplicates)} duplicate questions")

        status = "FAIL" if problems else "ok  "
        failures += bool(problems)
        counts = "/".join(str(positions[i]) for i in range(4))
        print(f"{status} {str(path):<52} n={len(records):<5} pos={counts:<16} "
              f"tells={len(length_tells):<4} "
              + ("; ".join(problems) if problems else ""))
        if problems and args.verbose:
            for label, ids in (("length tell", length_tells), ("dupe options", option_dupes)):
                for qid in ids[:10]:
                    print(f"       {label}: {qid}")

    return 1 if failures else 0


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qbank", description=__doc__)
    parser.add_argument("--no-cache", action="store_true", help="ignore the on-disk SPARQL cache")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_count = sub.add_parser("count", help="yield matrix per pattern per sitelink threshold")
    p_count.add_argument("patterns", nargs="*", help="pattern ids or category names")
    p_count.add_argument("--thresholds", default=f"{HARD_MIN},{MEDIUM_MIN},{EASY_MIN}")
    p_count.set_defaults(func=cmd_count)

    p_gen = sub.add_parser("generate", help="build the bank")
    p_gen.add_argument("patterns", nargs="*", help="pattern ids or category names")
    p_gen.add_argument("--out", default="content")
    p_gen.add_argument("--seed", type=int, default=1)
    p_gen.add_argument("--cap", type=float, default=DEFAULT_PATTERN_CAP,
                       help="max share of a category one pattern may carry")
    p_gen.add_argument("--max-patterns-per-entity", type=int, default=3)
    p_gen.add_argument("--target-per-cell", type=int, default=None,
                       help="ceiling per category/difficulty cell; shapes the bank when "
                            "one category has far more raw material than the others")
    p_gen.add_argument("--tiering", choices=("percentile", "absolute"), default="percentile",
                       help="percentile: rank within each pattern (default); "
                            "absolute: fixed sitelink thresholds across the whole bank")
    p_gen.set_defaults(func=cmd_generate)

    p_qa = sub.add_parser("qa", help="check a generated file or directory")
    p_qa.add_argument("path", nargs="?", default="content")
    p_qa.add_argument("--verbose", action="store_true")
    p_qa.set_defaults(func=cmd_qa)

    args = parser.parse_args(argv)
    return args.func(args)
