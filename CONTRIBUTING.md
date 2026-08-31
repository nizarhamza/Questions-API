# Adding a pattern

1. Write the SPARQL by hand at https://query.wikidata.org first. If it does not
   return inside the 60-second wall there, it will not work here either.
2. Return the column contract: `?subject ?subjectLabel ?objectLabel ?links`, plus
   any `?sim*` columns you want the distractor picker to rank by.
3. Include an `ORDER BY`. Paging without one is not stable.
4. Check the property against `TIME_VARYING_PROPERTIES`. If the answer can change
   after you ship, the pattern is wrong however good the data looks.
5. Add the `Pattern(...)` to `PATTERNS` and run
   `python3 -m qbank generate <your-pattern-id> --out /tmp/check`, then
   `python3 -m qbank qa /tmp/check`.
6. Read fifty of them. The filters catch mechanical faults, not boring questions.

## If the query times out

In rough order of what to try:

- Move the class-plus-sitelinks scan into a subquery so the optimiser shrinks the
  candidate set before the property join.
- Set `paged=False`. Appending `LIMIT` changes the plan and can break a query
  that otherwise finishes.
- Split it with `shards`: SPARQL fragments substituted for `{SHARD}`, run
  separately and unioned. One occupation, one decade, or one collection per
  shard. A shard that still fails is skipped, not fatal.
- Raise the sitelink floor. Often the right answer anyway — the entities you lose
  are the ones no player would recognise.

Large classes that will not scan by sitelinks on the public endpoint, as of the
last attempt: `Q5` (human), `Q3305213` (painting), `Q7725634` (literary work,
intermittently), `Q482994` (album).

# Adding an import source (Engine C)

`qbank import` folds a finished external bank into `content/`. To add a source
beyond OpenTDB:

1. Write a module like `qbank/opentdb.py`: a client that pulls the raw rows, a
   `to_questions()` that yields `schema.Question` objects (answer first in `o`,
   `pattern` set to the source name, `e` naming the source and its licence), and
   an `assemble()` that buckets by `(category, difficulty)` and calls
   `distractors.balanced_positions` / `make_id`. Reuse `filters.leaks` and
   `text.normalize`.
2. If the source has categories the bank lacks, add codes to
   `IMPORTED_CATEGORY_CODES` (`qbank/schema.py`) and matching entries to
   `CATEGORY_META` (`api/scripts/build-data.mjs`). Never renumber an existing
   category.
3. Write shards to `content/imported/<source>/…` and call
   `manifest.write_merged(..., engine="<source>", ...)` so the other engines'
   shards survive.
4. Record the licence: per-record `e` string, a `NOTICE.md` in the source tree,
   and the `license` field on every shard entry.
5. Run `python3 -m qbank qa content/imported/<source>` and, from `api/`,
   `npm run build:data`.
