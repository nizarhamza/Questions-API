"""Command line: count (yield survey), generate, qa."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

from . import manifest as manifest_mod
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
        shards.append(manifest_mod.shard_entry(
            out_root, path, category=category, difficulty=difficulty,
            engine="wikidata", source="Wikidata", license="CC0",
        ))

    # Engine A owns the `wikidata` shards; merge, don't clobber anything Engine C
    # (`qbank import`) has written into the same tree.
    manifest = manifest_mod.write_merged(
        out_root,
        engine="wikidata",
        engine_meta={
            "engine": "qbank engine-a",
            "source": "Wikidata",
            "license": "CC0",
            "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "seed": args.seed,
        },
        shards=shards,
        extra_top={
            "seed": args.seed,
            "difficulty_thresholds": {"easy": EASY_MIN, "medium": MEDIUM_MIN, "hard": HARD_MIN},
        },
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


# -------------------------------------------------------------------- import (C)

def _existing_question_texts(out_root: Path, exclude_engine: str) -> set[str]:
    """Normalised question text of every shard not owned by `exclude_engine`.

    Lets an import drop rows that Engine A (or an earlier import) already covers,
    so the same fact is not asked twice across engines.
    """
    texts: set[str] = set()
    skip = out_root / "imported" / exclude_engine
    for path in out_root.rglob("*.jsonl"):
        try:
            path.relative_to(skip)
            continue
        except ValueError:
            pass
        for record in read_jsonl(path):
            texts.add(normalize(record.get("q", "")))
    return texts


def cmd_import(args) -> int:
    if args.source != "opentdb":
        print(f"unknown source {args.source!r} (only 'opentdb' is implemented)", file=sys.stderr)
        return 2

    from . import opentdb

    out_root = Path(args.out)
    client = opentdb.OpenTdbClient(use_cache=not args.no_cache, verbose=not args.quiet)
    raw = client.load_or_fetch(
        amount=args.amount, max_requests=args.max_requests, refresh=args.no_cache,
    )
    print(f"opentdb: {len(raw)} raw results", file=sys.stderr)

    existing = _existing_question_texts(out_root, exclude_engine="opentdb")
    produced, stats = opentdb.to_questions(raw, seed=args.seed, existing_texts=existing)
    print(stats.line(), file=sys.stderr)
    if not produced:
        print("no questions produced", file=sys.stderr)
        return 1

    buckets = opentdb.assemble(produced, seed=args.seed)

    shards = []
    for (category, difficulty), questions in sorted(buckets.items()):
        path = out_root / "imported" / "opentdb" / category / f"{difficulty}.jsonl"
        write_jsonl(path, questions)
        shards.append(manifest_mod.shard_entry(
            out_root, path, category=category, difficulty=difficulty,
            engine="opentdb", source=opentdb.ATTRIBUTION, license=opentdb.LICENSE,
        ))

    manifest = manifest_mod.write_merged(
        out_root,
        engine="opentdb",
        engine_meta={
            "engine": "qbank engine-c",
            "source": opentdb.ATTRIBUTION,
            "license": opentdb.LICENSE,
            "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "seed": args.seed,
            "note": "OpenTDB session token drained to response_code 4.",
        },
        shards=shards,
        # Kept only if the manifest has no seed yet (import into an empty
        # content/); a real bank already carries Engine A's seed and keeps it.
        default_top={"seed": args.seed},
    )
    notice = opentdb.write_notice(out_root)

    print()
    for (category, difficulty), questions in sorted(buckets.items()):
        print(f"  {category:<12} {difficulty:<6} {len(questions):>5}")
    print(f"\n{sum(s['count'] for s in shards)} imported  "
          f"(bank total {manifest['total']}) -> {out_root / 'imported' / 'opentdb'}")
    print(f"licence notice: {notice}")
    return 0


# ------------------------------------------------------------------------ qa

def cmd_qa(args) -> int:
    paths = sorted(Path(args.path).rglob("*.jsonl")) if Path(args.path).is_dir() else [Path(args.path)]
    failures = 0

    for path in paths:
        records = read_jsonl(path)
        if not records:
            continue

        # A shard under imported/<engine>/ for an engine other than Engine A's
        # `wikidata` is an import: its distractors and answer strings come from
        # someone else's bank, so the length-tell rate is a fact about that
        # source, not a defect this pipeline can fix. It is reported, not failed.
        parts = path.parts
        imported = (
            "imported" in parts
            and parts.index("imported") + 1 < len(parts)
            and parts[parts.index("imported") + 1] != "wikidata"
        )

        positions = Counter(r["a"] for r in records)
        expected = len(records) / 4
        max_dev = max(abs(positions[i] - expected) for i in range(4))
        # You cannot split n four ways more evenly than +/-1, so tolerate an
        # absolute deviation of 1 on top of the 10% relative bound. Without this
        # every cell of fewer than ~40 questions fails by arithmetic alone.
        skew_bad = max_dev > max(1.0, 0.10 * expected) + 1e-9
        skew_pct = max_dev / max(expected, 1) * 100

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

        # Hard failures: structural corruption, true whatever the source.
        problems = []
        if option_dupes:
            problems.append(f"{len(option_dupes)} with duplicate options")
        if bad_answer:
            problems.append(f"{len(bad_answer)} with out-of-range answer index")
        if duplicates:
            problems.append(f"{len(duplicates)} duplicate questions")

        # Distribution tells: a defect for a generated shard, advisory for an
        # imported one.
        soft = []
        tell_share = len(length_tells) / len(records)
        if skew_bad:
            soft.append(f"answer-position skew {skew_pct:.1f}%")
        if tell_share > LENGTH_TELL_MAX_SHARE:
            soft.append(f"{len(length_tells)} length tells ({tell_share*100:.1f}%)")
        if imported:
            warnings = soft
        else:
            problems += soft
            warnings = []

        status = "FAIL" if problems else "WARN" if warnings else "ok  "
        failures += bool(problems)
        counts = "/".join(str(positions[i]) for i in range(4))
        notes = "; ".join(problems + warnings)
        print(f"{status} {str(path):<52} n={len(records):<5} pos={counts:<16} "
              f"tells={len(length_tells):<4} " + notes)
        if (problems or warnings) and args.verbose:
            for label, ids in (("length tell", length_tells), ("dupe options", option_dupes)):
                for qid in ids[:10]:
                    print(f"       {label}: {qid}")

    return 1 if failures else 0


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qbank", description=__doc__)
    parser.add_argument("--no-cache", action="store_true",
                        help="ignore the on-disk response cache (SPARQL for generate, the raw dump for import)")
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

    p_imp = sub.add_parser("import", help="Engine C: import an existing bank (OpenTDB)")
    p_imp.add_argument("--source", default="opentdb", choices=("opentdb",),
                       help="which bank to import (only opentdb so far)")
    p_imp.add_argument("--out", default="content")
    p_imp.add_argument("--seed", type=int, default=1)
    p_imp.add_argument("--amount", type=int, default=50,
                       help="questions per OpenTDB request (1-50, its ceiling)")
    p_imp.add_argument("--max-requests", type=int, default=None,
                       help="cap the fetch loop for a quick pull; omit to drain the token")
    p_imp.set_defaults(func=cmd_import)

    p_qa = sub.add_parser("qa", help="check a generated file or directory")
    p_qa.add_argument("path", nargs="?", default="content")
    p_qa.add_argument("--verbose", action="store_true")
    p_qa.set_defaults(func=cmd_qa)

    args = parser.parse_args(argv)
    return args.func(args)
