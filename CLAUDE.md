# Questions-API

Two halves that share one artifact:

- **`qbank/`** — a Python generator (Engine A) that turns Wikidata facts into trivia
  questions via SPARQL + templates + sibling-row distractors. No model touches a
  fact, so the error rate is near zero.
- **`api/`** — a Cloudflare Worker (TypeScript) that serves the generated bank over
  HTTP, on two surfaces: native `/v1/*` and an OpenTDB-bit-compatible `/api.php` set.

The artifact between them is `content/` — 12 JSONL shards plus `manifest.json`
(counts, bytes, sha256 per shard). It is committed deliberately, as a released
artifact rather than build output.

Full prose lives in `README.md` and `api/README.md`. This file is the operational
brief: the things that will bite you.

## Commands

```bash
# generator (repo root)
pip install -r requirements.txt
export QBANK_USER_AGENT="YourBank/0.1 (https://github.com/nizarhamza/Questions-API; you@example.com)"
python3 -m qbank count                 # yield matrix, before building anything
python3 -m qbank generate              # Engine A: build from Wikidata into content/
python3 -m qbank import --source opentdb   # Engine C: fold OpenTDB into content/
python3 -m qbank qa content            # skew, duplicates, bad indices, length tells

# worker (api/)
cd api && npm install
npm run build:data     # content/ -> src/data/bank.json, with verification
npm run dev            # wrangler dev on :8787
npm test               # 31 tests inside workerd, real bindings
npm run smoke          # boots wrangler dev, walks every endpoint over real HTTP
npm run typecheck
npm run deploy         # rebuilds bank.json first, then wrangler deploy
```

`npm test` and `npm run typecheck` regenerate `bank.json` and `worker-configuration.d.ts`
via pre-scripts, so they work from a clean checkout.

## Hard constraints

**The WDQS public endpoint has a 60-second server-side wall,** and it shaped the whole
generator. Deep LIMIT/OFFSET paging re-runs the query; appending LIMIT changes the
plan; large classes (Q5 human, Q3305213 painting, Q482994 album, Q7725634 literary
work) cannot be scanned with a sitelink range filter at all. Use `Pattern.shards`
(SPARQL fragments substituted for `{SHARD}`, unioned, failures skipped) or raise the
sitelink floor. Do not reach for a longer client timeout — the wall is not yours.

**`build-data.mjs` must never reorder records.** Global bank indexes are baked into
live session-token bitsets, so a reshuffle silently corrupts every token in flight.
Shards sort by `(category, difficulty)` then `path` (a cell can be fed by two
engines); records keep file order. Re-running `qbank import` reshuffles the
imported indexes the same way a re-`generate` does — it is a bank-version bump,
not a routine refresh.

**Engine C (`qbank import`) shares the `content/` tree and merges the manifest.**
`generate` owns `imported/wikidata/`, `import` owns `imported/opentdb/`, and
`qbank/manifest.py` rewrites `manifest.json` keeping the other engine's shards.
OpenTDB is CC BY-SA 4.0: the `imported/opentdb/` tree carries attribution and
share-alike (per-record `e` string, `NOTICE.md`, per-shard manifest tags) — do
not treat it as CC0 like the rest of `content/`. OpenTDB rate-limits to ~1
request / 5s / IP; the client paces itself and caches the raw dump under
`.cache/opentdb/`. OpenTDB's ~24 categories map 1:1 onto bank categories to keep
the numeric ids round-tripping; the codes live in three places that must stay in
step — `IMPORTED_CATEGORY_CODES` (`qbank/schema.py`), `OTDB_CATEGORIES`
(`qbank/opentdb.py`), `CATEGORY_META` (`api/scripts/build-data.mjs`).

**`CATEGORY_CODES` in `qbank/schema.py` is locked.** Renumbering an existing bank is
the one migration that is genuinely painful.

**Difficulty is percentile-within-pattern, not absolute sitelinks.** Every sovereign
state clears 150 sitelinks, so absolute cuts put 175 of 177 capitals in `easy`.
`--tiering absolute` exists but is not the default; expect `hard` to mean something
slightly different per category.

**The per-pattern repetition cap needs a quota scan, not a binary search.**
`quota <= share * total(quota)` is a step function; binary search gave 25 questions
from a 2,658-question cell. Patterns under 25 items bypass the cap, or one 3-item
pattern strangles its category.

`painting-creator` and `album-artist` are in EXPERIMENTAL — they do not complete on
the public endpoint in any form tried. Science hits the templated ceiling around 250
questions; anything conceptual is Engine B's job (an LLM grounded on fetched source
text plus a blind verification pass), which does not exist yet.

## Worker invariants

**The bank is bundled, not in a database.** 0.95 MB of JSON compiled into the Worker:
no cold-start round trip, no per-request query, and a deploy is atomic — the Worker
either has the whole bank or fails to deploy. `build-data.mjs` re-verifies every
shard's sha256 against `manifest.json` and rejects bad option counts, duplicate
options, or out-of-range answer indices. A bad record cannot reach a client because
it cannot reach the bundle. Keep it that way: validation belongs at build time.

**Session tokens are a SQLite-backed Durable Object, not KV.** A client pulling ten
questions in a row blows through KV's one-write-per-second-per-key ceiling and reads
its own stale writes. State is a bitset over global indexes (782 bytes). A Durable
Object exists for every name you can spell, so "was this token issued?" is a
`created` key check, not an addressing question.

**Sealed questions are stateless.** `reveal=false` returns an HMAC "deal" over
(question index, correct position in *that* shuffle, expiry). The server stores
nothing. `SIGNING_SECRET` falls back to a dev constant — production needs
`wrangler secret put SIGNING_SECRET` or deals are forgeable.

**OpenTDB compatibility is exact by default.** Six-field result objects, `response_code`
0–5, four encodings, OpenTDB's own numeric category ids. `extended=1` is the opt-in
that adds `id`/`pattern`/`explanation`. Do not "improve" the compat surface — a client
pointed here must not need a code change. `type=boolean` returns code 1 because this
bank is entirely four-option multiple choice.

Rate limiting and the `SESSIONS` binding are both checked before use: a missing rate
limiter means unthrottled, not failing closed.

## Toolchain notes

- `wrangler types` supersedes `@cloudflare/workers-types` (now v5) — the generated
  `worker-configuration.d.ts` is gitignored and regenerated by pre-scripts.
- `@cloudflare/vitest-pool-workers` 0.22 dropped the `./config` export. With vitest 4
  the pool is a Vite plugin: `cloudflareTest({...})` in `plugins:`, not
  `defineWorkersConfig`.
- `src/data/bank.json` is generated and gitignored. `content/` is committed.

## Working in this repo

Verify by running things, not by reading them. There is a test suite that runs in the
real runtime and a smoke script that speaks real HTTP; both are fast. A change to
sampling, tokens, or the OpenTDB surface is not done until `npm test` and
`npm run smoke` are green.

`git status` should be run with `--no-optional-locks` if you are driving git from a
sandboxed shell against this working tree — orphaned `.git/index.lock` files break the
*next* git command, not the one that made them.

Remote is `origin` → `github.com/nizarhamza/Questions-API.git`, branch `main`.
