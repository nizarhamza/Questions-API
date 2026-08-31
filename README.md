# Questions-API — Engine A

A trivia question bank built from structured Wikidata facts, with no model asked
to recall anything.

A trivia question has three parts, and only one of them is hard:

| Part           | Where it comes from                  |
| -------------- | ------------------------------------ |
| the fact       | Wikidata — verified, sourced, typed  |
| the phrasing   | a template written once              |
| the wrong ones | sibling rows of the same query       |

Because no model touches the fact, the error rate is near zero: nothing is
recalling that Canberra is the capital of Australia, it is being read out of a
database and poured into a sentence.

## Install and run

```bash
pip install -r requirements.txt
export QBANK_USER_AGENT="YourBank/0.1 (https://github.com/you/repo; you@example.com)"

python3 -m qbank count                 # yield matrix, before you build anything
python3 -m qbank generate              # build the bank into content/
python3 -m qbank qa content            # check what came out
```

Set `QBANK_USER_AGENT` to something that identifies you. Wikidata throttles
anonymous clients hard, and a descriptive agent with contact details is the
difference between a run that finishes and a run that gets 429s.

Every SPARQL response is cached under `.cache/` keyed by exact query text, so a
second run costs nothing. `--no-cache` forces a refresh.

## Output

```
content/
├─ manifest.json                              counts, bytes, sha256 per shard
├─ report.json                                per-pattern and per-filter numbers
└─ imported/wikidata/<category>/<difficulty>.jsonl
```

One record per line:

```json
{"id":"geo-e-0016","q":"Which city is the seat of government of Spain?",
 "o":["Madrid","Asmara","Banjul","La Paz"],"a":0,
 "e":"Madrid is the capital of Spain.","t":["geography","capital-of"]}
```

Wikidata is CC0, so this content carries no share-alike obligation — unlike
OpenTDB or the Jeopardy archive, which belong in a separate `imported/` tree
with attribution if you ever add them.

## How a pattern works

A pattern is a SPARQL query, a set of phrasings, and a similarity axis. Every
query returns the same column contract, so nothing downstream needs to know
which pattern it is looking at:

| column         | meaning                                     |
| -------------- | ------------------------------------------- |
| `?subject`     | entity URI — identity, used for dedupe      |
| `?subjectLabel`| what goes into the question text            |
| `?objectLabel` | the correct answer                          |
| `?links`       | `wikibase:sitelinks`, which drives difficulty|
| `?sim*`        | optional similarity columns for distractors |

Adding a pattern means adding one `Pattern(...)` to `qbank/patterns.py`. Nothing
else changes.

## Distractors

Wrong options are sibling rows of the same query, so they are automatically the
same type and register as the answer. Difficulty decides how far down the
similarity ranking to sample:

- **easy** — far from the answer. Capital of Japan: Cairo, Lima, Oslo.
- **medium** — same neighbourhood. Seoul, Beijing, Manila.
- **hard** — the near-misses. Capital of Australia: Sydney, Melbourne, Brisbane.

A candidate that is also a correct answer for the same subject is rejected
outright, using the rows from *before* the multi-value drop — otherwise a
discarded second capital could come back as a distractor for its own country.

Options are then drawn from an oversample and trimmed to the three closest in
length to the answer, which is what kills the "the long one is correct" tell.

## The filters

1. **Multi-valued → drop.** Bolivia has two capitals. Two correct answers is a
   bug a user will find.
2. **Time-varying properties → refused at load.** `P39`, `P54`, `P1082` and
   friends are correct today and wrong in your shipped app next year. The
   `Pattern` constructor raises rather than letting one through.
3. **Obscurity → sitelink floor.** Without it you generate thousands of
   questions about Romanian communes.
4. **Answer leakage → whole-word check.** "Capital of Mexico?" → Mexico City.
   Matching is whole-word, so a one-letter answer like the symbol `O` is not a
   false positive. Roughly 7% of capitals get dropped this way.
5. **Duplicate facts.** Same fact, same wording, the same unordered
   subject/answer pair from the opposite direction ("capital of Japan" vs
   "Tokyo is in which country"), and a cap of three patterns per entity.

## Difficulty is computed, not guessed

`wikibase:sitelinks` counts how many language Wikipedias have an article on an
entity — a good popularity proxy that stays consistent across the whole bank and
does not drift between runs.

Absolute thresholds turned out to be wrong in practice. Every sovereign state
clears 150 sitelinks, so a fixed cut put 175 of 177 capitals into `easy`. The
default is therefore **percentile tiering within each pattern** — the top 20% of
that pattern's entities are easy, the next 35% medium, the rest hard. The cost is
that `hard` means something slightly different in geography than in art; the
benefit is a usable spread in every category. `--tiering absolute` restores fixed
thresholds if you want one global scale instead.

## The repetition cap

The binding constraint on a templated bank is not the data, it is repetition. No
single pattern may carry more than 15% of a category (`--cap`). Where a category
has too few patterns for that to be arithmetically possible — science has two —
the cap relaxes to `1/n` and the run reports the share it actually used.

That is also the honest reason to add patterns: a category with two patterns
cannot grow past twice its smaller one, however much data Wikidata holds.

Two details the arithmetic forces:

- The quota is found by **scanning the per-pattern quota**, not by binary-searching
  the total. `quota <= share * total(quota)` is a step function, not a monotonic
  predicate, and a binary search lands on whichever knife edge it happens to
  probe — which is how a five-pattern cell with 2,658 available questions first
  came out at 25.
- Patterns with fewer than 25 surviving questions are **included in full and left
  out of the cap arithmetic**. They cannot dominate anything, but under a strict
  reading they can strangle a category: one 3-item pattern among five caps a
  531-question cell at 15.

## What one full run produces

A run on 2026-08-31, eleven patterns, seed 1:

| category  | easy | medium | hard | total |
| --------- | ---- | ------ | ---- | ----- |
| geography |  143 |    253 |  276 |   672 |
| history   | 1026 |   1760 | 2194 |  4980 |
| film      |   80 |    125 |  150 |   355 |
| science   |   52 |     82 |  114 |   248 |
| **total** |      |        |      | **6255** |

The shape is lopsided and that is the real finding, not a bug: history has two
high-yield people patterns while science hits the templated ceiling at ~250 and
stops. `--target-per-cell` puts a ceiling on each cell if you want a flatter
bank; the better fix is more patterns in the thin categories.

`book-author` failed its whole run that day on WDQS 502s — the endpoint was
degraded, and the run continued without it, which is the intended behaviour.

## Working around the WDQS 60-second wall

The public endpoint kills any query at 60 seconds, and this shapes the whole
design:

- **Deep `LIMIT/OFFSET` paging does not work.** Each page re-runs the query from
  scratch, so page two of a large pattern times out even though page one
  returned in 30 seconds.
- **Appending `LIMIT` can change the plan** and turn a query that finishes into
  one that does not. Patterns already bounded by a sitelink subquery set
  `paged=False` and are fetched in a single request.
- **A large class cannot be scanned with a sitelink filter.** `?s wdt:P31 wd:Q5 ;
  wikibase:sitelinks ?l . FILTER(?l >= 150)` times out; there are 11M humans and
  the sitelink count is not indexed for range scans.
- **Sharding is the fix.** `Pattern.shards` holds SPARQL fragments substituted
  for `{SHARD}` and run as separate queries whose results are unioned. The people
  patterns run one occupation at a time — composers, monarchs, physicists — each
  small enough to finish. A shard that still times out is skipped with a warning
  instead of failing the run.

`painting-creator` and `album-artist` do not complete on the public endpoint in
any form tried so far. They are listed in `EXPERIMENTAL` and left out of a bare
`generate` run; call them by name to work on them. The fix for both is a
selective anchor — a curated list of painters, or shards by release year — not a
higher timeout.

## What Engine A cannot do

Templates produce recall of relations. They cannot produce "why does ice float",
"what happened at the Congress of Vienna", or anything needing explanation,
causation or narrative.

Science is the trap here. There are 118 elements, 8 planets, and a few dozen
constants — the templated ceiling is a few hundred questions and then it stops
dead. Everything conceptual is Engine B's job (an LLM grounded on fetched source
text, with a blind verification pass), and that is where the API spend and the
review time go.

## QA

`python3 -m qbank qa content` checks each output file for answer-position skew
above 10%, duplicate options, out-of-range answer indices, length tells, and
duplicate questions. It exits non-zero on failure, so it drops straight into CI.

Positions are assigned from a balanced shuffled sequence at write time, so skew
should be near zero by construction — the check is there to catch regressions.

`report.json` carries the per-pattern funnel: rows fetched, then how many each
filter removed, then what survived. When a pattern's yield looks wrong, that file
says which filter ate it.

## Licence

The generator code is MIT (see `LICENSE`). The generated bank under `content/`
is derived from Wikidata, which is CC0, so the questions carry no share-alike
obligation — attribution to Wikidata is courtesy, not a licence term.
