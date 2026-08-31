// Compiles content/ into a single JSON module bundled into the Worker.
//
// The bank is ~1.4 MB of JSONL and read-only between generator runs, so it goes
// into the bundle rather than a database: no cold-start round trip, no per-request
// query, and the deployed Worker is a self-contained artifact that either has the
// whole bank or fails to deploy.
//
// Records are emitted as positional tuples, not objects. Field names repeated 6,255
// times cost more than the decode loop that puts them back at module init.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..");
const contentDir = join(repoRoot, "content");
const outFile = join(here, "..", "src", "data", "bank.json");

const DIFFICULTY_ORDER = ["easy", "medium", "hard"];

// OpenTDB's own numeric category ids, so a client pointed at this API instead of
// opentdb.com keeps working with the category numbers it already hardcodes. The
// first eight are what Engine A can produce; the rest only appear once Engine C
// (`qbank import`) has run. Kept in step with qbank/opentdb.py OTDB_CATEGORIES
// and qbank/schema.py category codes.
const CATEGORY_META = {
  general:     { opentdb_id:  9, name: "General Knowledge" },
  literature:  { opentdb_id: 10, name: "Entertainment: Books" },
  film:        { opentdb_id: 11, name: "Entertainment: Film" },
  music:       { opentdb_id: 12, name: "Entertainment: Music" },
  theatre:     { opentdb_id: 13, name: "Entertainment: Musicals & Theatres" },
  television:  { opentdb_id: 14, name: "Entertainment: Television" },
  videogames:  { opentdb_id: 15, name: "Entertainment: Video Games" },
  boardgames:  { opentdb_id: 16, name: "Entertainment: Board Games" },
  science:     { opentdb_id: 17, name: "Science & Nature" },
  computers:   { opentdb_id: 18, name: "Science: Computers" },
  mathematics: { opentdb_id: 19, name: "Science: Mathematics" },
  mythology:   { opentdb_id: 20, name: "Mythology" },
  sports:      { opentdb_id: 21, name: "Sports" },
  geography:   { opentdb_id: 22, name: "Geography" },
  history:     { opentdb_id: 23, name: "History" },
  politics:    { opentdb_id: 24, name: "Politics" },
  art:         { opentdb_id: 25, name: "Art" },
  celebrities: { opentdb_id: 26, name: "Celebrities" },
  animals:     { opentdb_id: 27, name: "Animals" },
  vehicles:    { opentdb_id: 28, name: "Vehicles" },
  comics:      { opentdb_id: 29, name: "Entertainment: Comics" },
  gadgets:     { opentdb_id: 30, name: "Science: Gadgets" },
  anime:       { opentdb_id: 31, name: "Entertainment: Japanese Anime & Manga" },
  cartoons:    { opentdb_id: 32, name: "Entertainment: Cartoon & Animations" },
};

const manifest = JSON.parse(readFileSync(join(contentDir, "manifest.json"), "utf8"));

// Deterministic order. Global indexes are baked into session-token bitsets, so a
// reshuffle here would silently invalidate every live token; sort, never rely on
// manifest order. When two engines both feed one (category, difficulty) cell the
// path is the final tiebreak, so the order does not depend on manifest layout.
const shards = [...manifest.shards].sort((a, b) => {
  if (a.category !== b.category) return a.category < b.category ? -1 : 1;
  const d = DIFFICULTY_ORDER.indexOf(a.difficulty) - DIFFICULTY_ORDER.indexOf(b.difficulty);
  if (d !== 0) return d;
  return a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
});

const categorySlugs = [];
const patterns = [];
const questions = [];
const counts = {};
const problems = [];

for (const shard of shards) {
  const path = join(contentDir, shard.path);
  const raw = readFileSync(path);

  const sha = createHash("sha256").update(raw).digest("hex");
  if (sha !== shard.sha256) {
    problems.push(`${shard.path}: sha256 mismatch (manifest ${shard.sha256.slice(0, 12)}, file ${sha.slice(0, 12)})`);
  }

  const lines = raw.toString("utf8").split("\n").filter((l) => l.trim());
  if (lines.length !== shard.count) {
    problems.push(`${shard.path}: ${lines.length} records, manifest says ${shard.count}`);
  }

  let catIndex = categorySlugs.indexOf(shard.category);
  if (catIndex === -1) {
    catIndex = categorySlugs.push(shard.category) - 1;
    counts[shard.category] = { easy: 0, medium: 0, hard: 0, total: 0 };
  }
  const diffIndex = DIFFICULTY_ORDER.indexOf(shard.difficulty);
  if (diffIndex === -1) throw new Error(`unknown difficulty ${shard.difficulty}`);

  for (const line of lines) {
    const r = JSON.parse(line);

    // A record that fails these has no business reaching a client; the API answers
    // by index into `o`, so a bad `a` is a wrong answer served confidently.
    if (!Array.isArray(r.o) || r.o.length !== 4) problems.push(`${r.id}: ${r.o?.length} options, expected 4`);
    if (!Number.isInteger(r.a) || r.a < 0 || r.a > 3) problems.push(`${r.id}: answer index ${r.a} out of range`);
    if (new Set(r.o).size !== r.o.length) problems.push(`${r.id}: duplicate options`);
    if (!r.q || !r.q.trim()) problems.push(`${r.id}: empty question text`);

    const patternTag = r.t?.[1] ?? "";
    let patIndex = patterns.indexOf(patternTag);
    if (patIndex === -1) patIndex = patterns.push(patternTag) - 1;

    questions.push([r.id, r.q, r.o, r.a, r.e ?? "", catIndex, diffIndex, patIndex]);
  }

  // `+=`, not `=`: a cell can be split across engines (Engine A + an OpenTDB
  // import both writing e.g. geography/easy).
  counts[shard.category][shard.difficulty] += lines.length;
  counts[shard.category].total += lines.length;
}

if (problems.length) {
  console.error(`\nRefusing to build: ${problems.length} problem(s) in the bank\n`);
  for (const p of problems.slice(0, 25)) console.error(`  - ${p}`);
  if (problems.length > 25) console.error(`  ... and ${problems.length - 25} more`);
  process.exit(1);
}

const categories = categorySlugs.map((slug, i) => {
  const meta = CATEGORY_META[slug] ?? { opentdb_id: 1000 + i, name: slug[0].toUpperCase() + slug.slice(1) };
  return { slug, id: meta.opentdb_id, name: meta.name, counts: counts[slug] };
});

const bank = {
  version: 1,
  generated: manifest.generated,
  source: manifest.source,
  seed: manifest.seed,
  difficulties: DIFFICULTY_ORDER,
  categories,
  patterns,
  questions,
};

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, JSON.stringify(bank));

const bytes = readFileSync(outFile).length;
console.log(`bank.json  ${questions.length} questions  ${(bytes / 1024 / 1024).toFixed(2)} MB uncompressed`);
for (const c of categories) {
  console.log(`  ${c.slug.padEnd(11)} id=${String(c.id).padEnd(3)} ${String(c.counts.total).padStart(5)}  ` +
    `easy ${c.counts.easy}  medium ${c.counts.medium}  hard ${c.counts.hard}`);
}
