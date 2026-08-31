# Questions API

A trivia API over the Engine A question bank, running on Cloudflare Workers.

Two surfaces on one Worker:

- **`/v1/*`** — the native API, with the bank's own vocabulary: pattern tags,
  provenance strings, sealed questions, batch scoring.
- **`/api.php` and friends** — bit-compatible with [Open Trivia DB][otdb]. Point an
  existing OpenTDB client at this host and it keeps working: same paths, same
  parameter names, same `response_code` numbers, same category ids.

[otdb]: https://opentdb.com/api_config.php

## Why the bank is in the bundle

6,255 questions is 0.95 MB of JSON, and it changes only when the generator runs.
That is small enough to compile into the Worker itself, which buys three things a
database would not: no cold-start round trip, no per-request query, and a deployed
Worker that is a single self-contained artifact — it either has the whole bank or
it fails to deploy.

`scripts/build-data.mjs` is what makes that safe. It re-checks every shard's sha256
against `content/manifest.json`, re-counts the records, and refuses to build if any
question has the wrong number of options, a duplicate option, or an answer index
pointing outside its own list. A bad record cannot reach a client, because it cannot
reach the bundle.

The one thing it must never do is reorder. Global indexes are baked into live
session tokens, so shards are sorted by `(category, difficulty)` and records keep
their file order — a reshuffle would silently corrupt every token in flight.

## Run it

```bash
npm install
npm run build:data        # content/ -> src/data/bank.json
npm run dev               # http://localhost:8787
```

`npm test` runs the suite inside workerd against real bindings — a real Durable
Object, the real bundled bank. `npm run smoke` boots `wrangler dev` and walks every
endpoint over real HTTP.

## Deploy

```bash
npx wrangler login
npx wrangler secret put SIGNING_SECRET     # openssl rand -hex 32
npm run deploy
```

`npm run deploy` rebuilds `bank.json` first, so a deploy can never ship a stale bank.

Two bindings are declared in `wrangler.jsonc`:

| Binding | What it is | If it is missing |
| --- | --- | --- |
| `SESSIONS` | SQLite-backed Durable Object holding token state | Session tokens 500; everything else works |
| `RL_GENERAL`, `RL_TOKENS` | Native rate limiting | The Worker runs unthrottled rather than failing closed |

If your account rejects the `ratelimits` blocks at deploy time, delete them from
`wrangler.jsonc` and redeploy — the code checks for the binding before using it.

`SIGNING_SECRET` falls back to a hardcoded development constant. That is fine
locally and wrong in production: without a real secret anyone can forge the deals
that `/v1/check` trusts.

## Native API

### `GET /v1/questions`

| Parameter | Default | Notes |
| --- | --- | --- |
| `amount` | 10 | 1–100. Clamped to the pool rather than erroring. |
| `category` | all | Slug (`geography`), OpenTDB id (`22`), or display name. |
| `difficulty` | all | `easy`, `medium`, `hard`. |
| `pattern` | all | One of the ten pattern tags. `GET /v1/patterns` lists them. |
| `token` | — | Session token; no question repeats while it lives. |
| `seed` | — | Reproducible draw. Same seed, same paper. |
| `reveal` | `true` | `false` withholds the answer. See below. |

```json
{
  "count": 1,
  "pool_size": 672,
  "filters": { "category": "geography", "difficulty": null, "pattern": null },
  "sealed": false,
  "questions": [{
    "id": "geo-e-0001",
    "question": "Which city is the seat of government of France?",
    "options": ["Majuro", "Muscat", "Lusaka", "Paris"],
    "answer_index": 3,
    "answer": "Paris",
    "explanation": "Paris is the capital of France.",
    "category": "geography", "category_id": 22, "category_name": "Geography",
    "difficulty": "easy", "pattern": "capital-of",
    "tags": ["geography", "capital-of"]
  }]
}
```

### Sealed questions

`reveal=false` keeps the answer on the server. Options come back in a per-request
order with a signed `deal` in place of the answer:

```json
{ "id": "geo-e-0001", "question": "…", "options": ["Paris", "Lusaka", "Majuro", "Muscat"],
  "deal": "0.0.1788174000.k3Jd…", "expires_at": "2026-08-31T15:00:00.000Z" }
```

The deal is an HMAC over (question index, correct position in *that* order, expiry).
`/v1/check` scores against it, so a client cannot read the answer off the wire, cannot
forge a position, and cannot replay a deal past its hour. The server stores nothing.

### `POST /v1/check`

One answer:

```json
{ "deal": "0.0.1788174000.k3Jd…", "answer": 2 }
{ "id": "geo-e-0001", "answer": "Paris" }
```

`answer` may be an index or the option text, matched case-insensitively.

A whole quiz, scored in one call:

```json
{ "answers": [ { "deal": "…", "answer": 0 }, { "deal": "…", "answer": 3 } ] }
```

```json
{ "count": 2, "score": 1, "out_of": 2, "results": [ … ] }
```

### Session tokens

```bash
TOKEN=$(curl -s -X POST https://your-worker.workers.dev/v1/tokens | jq -r .token)
curl -s "https://your-worker.workers.dev/v1/questions?amount=10&token=$TOKEN"
```

A token remembers every question it has been shown as a bitset over global bank
indexes — 6,255 questions is 782 bytes, so the whole seen-set is read and written on
every draw without thinking about it. When a filtered pool runs dry the API answers
`409 token_exhausted`; `POST /v1/tokens/{token}/reset` clears it. Tokens expire six
idle hours after their last use.

This is a Durable Object and not KV on purpose. A client pulling ten questions in a
row would blow straight through KV's one-write-per-second-per-key ceiling, and would
read its own stale writes when it did.

### The rest

| Endpoint | |
| --- | --- |
| `GET /v1/questions/{id}` | One question, with its answer and provenance |
| `GET /v1/categories` | Slugs, OpenTDB ids, per-difficulty counts |
| `GET /v1/patterns` | The ten patterns and how many of each exist |
| `GET /v1/stats` | Totals, generation date, source |
| `GET /v1/tokens/{token}` | How much of the bank that token has burned through |
| `GET /health` | Liveness and bank size |

Errors are `{"error":{"code":"unknown_category","message":"…"}}` with a matching HTTP
status. Codes: `invalid_amount`, `unknown_category`, `unknown_difficulty`,
`unknown_pattern`, `no_results`, `unknown_token`, `token_exhausted`, `not_found`,
`method_not_allowed`, `rate_limited`, `internal_error`.

## OpenTDB-compatible API

| Endpoint | Parameters |
| --- | --- |
| `GET /api.php` | `amount` (1–50), `category`, `difficulty`, `type`, `encode`, `token` |
| `GET /api_token.php` | `command=request`, or `command=reset&token=…` |
| `GET /api_category.php` | — |
| `GET /api_count.php` | `category` |
| `GET /api_count_global.php` | — |

Response codes match OpenTDB exactly: `0` success, `1` no results, `2` invalid
parameter, `3` token not found, `4` token empty, `5` rate limited. So do the four
encodings — default HTML entities, `url3986`, `base64`, `legacy`.

Two honest differences:

- Every question in this bank is four-option multiple choice, so `type=boolean`
  returns code `1` (no results) rather than true/false questions.
- `extended=1` is an addition, not a compatibility break: it adds `id`, `pattern` and
  `explanation` to each result. Without it the result objects have exactly OpenTDB's
  six fields.

Category ids are OpenTDB's own — geography `22`, history `23`, film `11`, science
`17` — so a client's hardcoded numbers keep pointing at the right thing. If the
bank was built with Engine C (`qbank import --source opentdb`) it also carries
the rest of OpenTDB's categories (Sports `21`, Television `14`, Video Games `15`,
Politics `24`, …), each on its own OpenTDB id. Those questions are imported under
**CC BY-SA 4.0** — see `content/imported/opentdb/NOTICE.md` — and every one names
its source in the `explanation` field.

## Using it from Flutter

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

const base = 'https://your-worker.workers.dev';

/// Sealed draw: the answer never reaches the device, so a player poking at
/// the network log learns nothing.
Future<List<Map<String, dynamic>>> drawQuiz({
  required String token,
  String? category,
  String difficulty = 'medium',
  int amount = 10,
}) async {
  final uri = Uri.parse('$base/v1/questions').replace(queryParameters: {
    'amount': '$amount',
    'difficulty': difficulty,
    'reveal': 'false',
    'token': token,
    if (category != null) 'category': category,
  });
  final body = jsonDecode(utf8.decode((await http.get(uri)).bodyBytes));
  return List<Map<String, dynamic>>.from(body['questions'] as List);
}

Future<Map<String, dynamic>> scoreQuiz(List<({String deal, int answer})> given) async {
  final response = await http.post(
    Uri.parse('$base/v1/check'),
    headers: {'content-type': 'application/json'},
    body: jsonEncode({
      'answers': [for (final g in given) {'deal': g.deal, 'answer': g.answer}],
    }),
  );
  return jsonDecode(response.body) as Map<String, dynamic>;
}
```

Mint the token once per player (`POST /v1/tokens`) and keep it in local storage;
they will not see a repeat until they have worked through the pool.

## Limits and caching

120 requests per minute per IP, and 12 token mints per minute — token minting gets
its own budget because it is the only endpoint that allocates durable storage.
Rate limits are per Cloudflare location and deliberately approximate.

Static reads (`/v1/categories`, `/v1/patterns`, `/v1/stats`, `/v1/questions/{id}`,
and the OpenTDB metadata endpoints) carry `s-maxage` and are cached at the edge.
Draws are `no-store` — a cached random draw is not random.

CORS is open to every origin, with preflight handled.

## Layout

```
api/
├─ scripts/build-data.mjs    content/ -> a verified, bundled JSON module
├─ scripts/smoke.mjs         boots wrangler dev, walks every endpoint
├─ src/
│  ├─ index.ts               router, rate limiting, error envelope
│  ├─ bank.ts                decodes the bundle, memoises pools
│  ├─ draw.ts                the one place questions get selected
│  ├─ session.ts             the Durable Object behind session tokens
│  ├─ sign.ts                HMAC deals for sealed questions
│  ├─ rng.ts                 seeded + crypto sampling, partial Fisher-Yates
│  ├─ encode.ts              OpenTDB's four output encodings
│  ├─ docs.ts                the page served at /
│  └─ routes/{native,opentdb}.ts
└─ test/api.test.ts
```

API code is MIT. Content is CC0 where it comes from Wikidata
(`content/imported/wikidata/`) and CC BY-SA 4.0 where it comes from the Open
Trivia Database (`content/imported/opentdb/`, added by Engine C).
