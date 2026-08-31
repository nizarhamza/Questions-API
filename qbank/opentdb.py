"""Engine C, OpenTDB source.

OpenTDB ships four-option multiple choice with the three wrong answers already
attached, so this is an import, not a generation. The work is: loop a session
token until it is exhausted (`response_code` 4) so the pull is the whole bank
and never a repeat, un-escape the HTML entities OpenTDB encodes by default, map
the category string onto this bank's taxonomy, take the stated difficulty
verbatim, and hand the records to the shared assembler for id assignment and
answer-position balancing.

OpenTDB content is CC BY-SA 4.0. The output tree carries per-record attribution
in the `e` field, a NOTICE.md, and the same licence tag in the manifest.
"""

from __future__ import annotations

import html
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import filters
from .distractors import balanced_positions, place_answer
from .schema import DIFFICULTIES, Question, make_id
from .text import normalize

API = "https://opentdb.com/api.php"
TOKEN_API = "https://opentdb.com/api_token.php"

USER_AGENT = "QuestionsAPI-BankImporter/0.1 (https://github.com/nizarh393/Questions-API)"

# OpenTDB rate-limits an IP to roughly one request every 5 seconds and answers
# `response_code` 5 when you cross it. Pace under the wall rather than eat the
# rejections.
MIN_INTERVAL = 5.0

ATTRIBUTION = "Open Trivia Database (https://opentdb.com)"
LICENSE = "CC BY-SA 4.0"

# response_code vocabulary, from https://opentdb.com/api_config.php
CODE_SUCCESS = 0
CODE_NO_RESULTS = 1
CODE_INVALID_PARAM = 2
CODE_TOKEN_NOT_FOUND = 3
CODE_TOKEN_EMPTY = 4
CODE_RATE_LIMIT = 5


class OpenTdbError(RuntimeError):
    pass


# --------------------------------------------------------------------- taxonomy

@dataclass(frozen=True)
class OtdbCategory:
    otdb_id: int
    slug: str
    name: str          # display name, kept byte-identical to OpenTDB's own


# One bank category per OpenTDB category. The numeric ids round-trip through the
# Worker's OpenTDB-compatible surface (`/api.php?category=NN`), so folding
# several OpenTDB categories into one bank slug would break a client's
# hardcoded number. The seven slugs Engine A already uses are reused verbatim;
# the rest get codes from qbank/schema.py IMPORTED_CATEGORY_CODES and display
# names + ids from api/scripts/build-data.mjs CATEGORY_META. Keep all three lists
# in step.
OTDB_CATEGORIES: tuple[OtdbCategory, ...] = (
    OtdbCategory(9,  "general",     "General Knowledge"),
    OtdbCategory(10, "literature",  "Entertainment: Books"),
    OtdbCategory(11, "film",        "Entertainment: Film"),
    OtdbCategory(12, "music",       "Entertainment: Music"),
    OtdbCategory(13, "theatre",     "Entertainment: Musicals & Theatres"),
    OtdbCategory(14, "television",  "Entertainment: Television"),
    OtdbCategory(15, "videogames",  "Entertainment: Video Games"),
    OtdbCategory(16, "boardgames",  "Entertainment: Board Games"),
    OtdbCategory(17, "science",     "Science & Nature"),
    OtdbCategory(18, "computers",   "Science: Computers"),
    OtdbCategory(19, "mathematics", "Science: Mathematics"),
    OtdbCategory(20, "mythology",   "Mythology"),
    OtdbCategory(21, "sports",      "Sports"),
    OtdbCategory(22, "geography",   "Geography"),
    OtdbCategory(23, "history",     "History"),
    OtdbCategory(24, "politics",    "Politics"),
    OtdbCategory(25, "art",         "Art"),
    OtdbCategory(26, "celebrities", "Celebrities"),
    OtdbCategory(27, "animals",     "Animals"),
    OtdbCategory(28, "vehicles",    "Vehicles"),
    OtdbCategory(29, "comics",      "Entertainment: Comics"),
    OtdbCategory(30, "gadgets",     "Science: Gadgets"),
    OtdbCategory(31, "anime",       "Entertainment: Japanese Anime & Manga"),
    OtdbCategory(32, "cartoons",    "Entertainment: Cartoon & Animations"),
)

_BY_NAME = {c.name: c for c in OTDB_CATEGORIES}


# ------------------------------------------------------------------------ client

class OpenTdbClient:
    """Polite loop over the OpenTDB HTTP API with a whole-dump disk cache.

    Individual requests are token-stateful and randomised, so there is nothing
    to cache per URL. What is worth keeping is the assembled raw dump: rerunning
    `qbank import` then costs no requests unless `--no-cache` is passed.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path = ".cache/opentdb",
        min_interval: float = MIN_INTERVAL,
        use_cache: bool = True,
        verbose: bool = True,
        timeout: int = 30,
        max_retries: int = 6,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.use_cache = use_cache
        self.verbose = verbose
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # -- internals

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    @property
    def _dump_path(self) -> Path:
        return self.cache_dir / "dump.json"

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, url: str, params: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                delay = min(60.0, 2 ** attempt + random.uniform(0, 1))
                self._log(f"  ! {type(exc).__name__}; retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise OpenTdbError(f"non-JSON response: {resp.text[:200]}") from exc
            if resp.status_code in (429, 500, 502, 503, 504):
                delay = min(60.0, 2 ** attempt + random.uniform(0, 1))
                self._log(f"  ! HTTP {resp.status_code}; retrying in {delay:.1f}s")
                time.sleep(delay)
                last_error = OpenTdbError(f"HTTP {resp.status_code}")
                continue
            raise OpenTdbError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        raise OpenTdbError(f"gave up after {self.max_retries} attempts: {last_error}")

    # -- public

    def request_token(self) -> str:
        payload = self._get(TOKEN_API, {"command": "request"})
        if payload.get("response_code") != CODE_SUCCESS or not payload.get("token"):
            raise OpenTdbError(f"token request failed: {payload}")
        return payload["token"]

    def fetch_all(
        self,
        *,
        token: str | None = None,
        amount: int = 50,
        max_requests: int | None = None,
    ) -> list[dict]:
        """Drain one session token. Stops on `response_code` 4 (exhausted).

        `max_requests` caps the loop for a quick functional pull; leave it None
        to take the whole bank.
        """
        token = token or self.request_token()
        results: list[dict] = []
        made = 0
        while max_requests is None or made < max_requests:
            payload = self._get(
                API, {"amount": max(1, min(50, amount)), "token": token, "type": "multiple"}
            )
            made += 1
            code = payload.get("response_code", -1)
            if code == CODE_TOKEN_EMPTY:
                self._log(f"  .. token exhausted after {made} request(s)")
                break
            if code == CODE_NO_RESULTS:
                self._log("  .. no results")
                break
            if code == CODE_RATE_LIMIT:
                self._log("  ! rate limited; backing off 5s")
                time.sleep(5.0)
                made -= 1  # a throttled call did not consume the bank
                continue
            if code != CODE_SUCCESS:
                raise OpenTdbError(f"response_code {code}")
            batch = payload.get("results", [])
            if not batch:
                break
            results.extend(batch)
            self._log(f"  .. {len(results)} questions")
        return results

    def load_or_fetch(
        self,
        *,
        amount: int = 50,
        max_requests: int | None = None,
        refresh: bool = False,
    ) -> list[dict]:
        if self.use_cache and not refresh and self._dump_path.exists():
            self._log(f"opentdb: cache hit ({self._dump_path})")
            return json.loads(self._dump_path.read_text(encoding="utf-8"))
        results = self.fetch_all(amount=amount, max_requests=max_requests)
        if self.use_cache:
            self._dump_path.write_text(json.dumps(results), encoding="utf-8")
        return results


# --------------------------------------------------------------------- convert

@dataclass
class ImportStats:
    rows: int = 0
    dropped_not_multiple: int = 0
    dropped_unknown_category: int = 0
    dropped_bad_difficulty: int = 0
    dropped_malformed: int = 0
    dropped_dupe_options: int = 0
    dropped_duplicate: int = 0
    dropped_leak: int = 0
    produced: int = 0
    by_category: Counter = field(default_factory=Counter)

    def line(self) -> str:
        return (
            f"opentdb rows={self.rows} "
            f"notmc={self.dropped_not_multiple} unkcat={self.dropped_unknown_category} "
            f"baddiff={self.dropped_bad_difficulty} malformed={self.dropped_malformed} "
            f"dupeopt={self.dropped_dupe_options} dup={self.dropped_duplicate} "
            f"leak={self.dropped_leak} -> {self.produced}"
        )


def to_questions(
    raw_results: list[dict],
    *,
    seed: int = 1,
    existing_texts: set[str] | None = None,
) -> tuple[list[Question], ImportStats]:
    """OpenTDB result dicts -> validated Questions, not yet bucketed or numbered."""
    stats = ImportStats()
    seen: set[str] = set(existing_texts or ())
    out: list[Question] = []

    for item in raw_results:
        stats.rows += 1
        if item.get("type") != "multiple":
            stats.dropped_not_multiple += 1
            continue

        cat = _BY_NAME.get(html.unescape(item.get("category", "")).strip())
        if cat is None:
            stats.dropped_unknown_category += 1
            continue

        difficulty = html.unescape(item.get("difficulty", "")).strip().lower()
        if difficulty not in DIFFICULTIES:
            stats.dropped_bad_difficulty += 1
            continue

        question = html.unescape(item.get("question", "")).strip()
        correct = html.unescape(item.get("correct_answer", "")).strip()
        wrong = [html.unescape(w).strip() for w in item.get("incorrect_answers", [])]
        wrong = [w for w in wrong if w]
        if not question or not correct or len(wrong) != 3:
            stats.dropped_malformed += 1
            continue

        if len({normalize(correct), *(normalize(w) for w in wrong)}) != 4:
            stats.dropped_dupe_options += 1
            continue

        key = normalize(question)
        if key in seen:
            stats.dropped_duplicate += 1
            continue
        seen.add(key)

        # The answer sitting verbatim in the prompt. OpenTDB is human-written and
        # mostly clean, but the odd "What is X? / X" slips through.
        if filters.leaks(question, "", correct):
            stats.dropped_leak += 1
            continue

        out.append(
            Question(
                id="",  # assigned by assemble()
                q=question,
                o=[correct, *wrong],  # answer first; assemble() places it
                a=0,
                e=f"Imported from {ATTRIBUTION}, licensed CC BY-SA 4.0.",
                t=[cat.slug, "opentdb"],
                category=cat.slug,
                difficulty=difficulty,
                subject_qid="",
                pattern="opentdb",
                sitelinks=0,
            )
        )
        stats.by_category[cat.slug] += 1
        stats.produced += 1

    return out, stats


def assemble(
    questions: list[Question], *, seed: int = 1
) -> dict[tuple[str, str], list[Question]]:
    """Bucket by (category, difficulty), balance answer positions, assign ids.

    Order is fixed by normalised question text: OpenTDB rows have no natural
    key, and the Worker bakes global indexes from file order, so a re-import is
    a deliberate bank-version bump (same as re-running Engine A).
    """
    rng = random.Random(f"opentdb:{seed}")
    buckets: dict[tuple[str, str], list[Question]] = {}
    for q in questions:
        buckets.setdefault((q.category, q.difficulty), []).append(q)

    out: dict[tuple[str, str], list[Question]] = {}
    for key, items in sorted(buckets.items()):
        items.sort(key=lambda q: normalize(q.q))
        positions = balanced_positions(len(items), rng)
        for index, (q, slot) in enumerate(zip(items, positions), start=1):
            answer, wrong = q.o[0], q.o[1:]
            q.o = place_answer(answer, wrong, slot)
            q.a = slot
            q.id = make_id(q.category, q.difficulty, index)
        out[key] = items
    return out


NOTICE = f"""\
# OpenTDB import

The `.jsonl` shards in this directory are imported from the
**Open Trivia Database** (<https://opentdb.com>) via `python -m qbank import
--source opentdb`.

## Licence

OpenTDB content is licensed **CC BY-SA 4.0**
(<https://creativecommons.org/licenses/by-sa/4.0/>). That licence travels with
these questions:

- **Attribution** — credit the Open Trivia Database.
- **ShareAlike** — if you redistribute these questions or adaptations of them,
  do so under CC BY-SA 4.0.

This is different from the rest of `content/`, which is derived from Wikidata
(CC0) and carries no such obligation. The generator code remains MIT.

Each record's `e` field also names the source, so attribution survives even
when a single question is served in isolation.
"""


def write_notice(out_root: Path | str) -> Path:
    path = Path(out_root) / "imported" / "opentdb" / "NOTICE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NOTICE, encoding="utf-8")
    return path
