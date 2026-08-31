// Boots `wrangler dev` and walks every endpoint over real HTTP.
// The vitest suite exercises the handlers; this proves the deployed shape works:
// routing, headers, status codes, and the docs page actually rendering.

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const PORT = Number(process.env.SMOKE_PORT ?? 8788);
const BASE = `http://127.0.0.1:${PORT}`;

const server = spawn("npx", ["wrangler", "dev", "--port", String(PORT), "--log-level", "error"], {
  stdio: ["ignore", "pipe", "pipe"],
});
server.stdout.on("data", () => {});
server.stderr.on("data", (d) => process.env.SMOKE_VERBOSE && process.stderr.write(d));

async function waitForReady(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${BASE}/health`);
      if (r.ok) return true;
    } catch { /* not up yet */ }
    await sleep(400);
  }
  return false;
}

let failures = 0;
async function check(name, path, assertion, init) {
  try {
    const response = await fetch(BASE + path, init);
    const text = await response.text();
    const body = response.headers.get("content-type")?.includes("json") ? JSON.parse(text) : text;
    const problem = assertion(response, body);
    if (problem) throw new Error(problem);
    console.log(`  ok   ${String(response.status).padEnd(3)} ${name}`);
  } catch (error) {
    failures++;
    console.log(`  FAIL      ${name} — ${error.message}`);
  }
}

if (!(await waitForReady())) {
  console.error("wrangler dev never became ready");
  server.kill("SIGTERM");
  process.exit(1);
}

console.log(`\nsmoke against ${BASE}\n`);

await check("docs page renders", "/", (r, b) =>
  r.status === 200 && b.includes("<h1>Questions API</h1>") && b.includes("6,255") ? null : "docs page wrong");

await check("health", "/health", (r, b) => (b.questions === 6255 ? null : "bad count"));

await check("native draw", "/v1/questions?amount=5&category=geography", (r, b) =>
  b.count === 5 && b.questions.every((q) => q.answer === q.options[q.answer_index]) ? null : "bad draw");

await check("native filters", "/v1/questions?amount=3&difficulty=hard&pattern=person-birth-year", (r, b) =>
  b.questions.every((q) => q.difficulty === "hard" && q.pattern === "person-birth-year") ? null : "filter leaked");

await check("single question", "/v1/questions/sci-e-0001", (r, b) => (b.id === "sci-e-0001" ? null : "wrong id"));
await check("categories", "/v1/categories", (r, b) => (b.count === 4 ? null : "bad categories"));
await check("patterns", "/v1/patterns", (r, b) => (b.count === 10 ? null : "bad patterns"));
await check("stats", "/v1/stats", (r, b) => (b.total === 6255 ? null : "bad stats"));
await check("unknown route 404s", "/v1/banana", (r) => (r.status === 404 ? null : "should 404"));
await check("bad filter 400s", "/v1/questions?category=atlantis", (r, b) =>
  r.status === 400 && b.error.code === "unknown_category" ? null : "should 400");

await check("opentdb draw", "/api.php?amount=3&category=23&difficulty=medium", (r, b) =>
  b.response_code === 0 && b.results.length === 3 && b.results[0].type === "multiple" ? null : "bad opentdb draw");
await check("opentdb invalid param", "/api.php?amount=-1", (r, b) => (b.response_code === 2 ? null : "expected code 2"));
await check("opentdb categories", "/api_category.php", (r, b) =>
  b.trivia_categories.some((c) => c.id === 22) ? null : "missing geography");
await check("opentdb counts", "/api_count.php?category=23", (r, b) =>
  b.category_question_count.total_question_count === 4980 ? null : "bad count");
await check("opentdb global counts", "/api_count_global.php", (r, b) =>
  b.overall.total_num_of_questions === 6255 ? null : "bad global count");

await check("CORS preflight", "/v1/questions", (r) =>
  r.status === 204 && r.headers.get("access-control-allow-origin") === "*" ? null : "bad preflight",
  { method: "OPTIONS" });

// Sealed round trip over the wire.
const sealed = await (await fetch(`${BASE}/v1/questions?amount=1&reveal=false`)).json();
const deal = sealed.questions[0].deal;
const verdicts = [];
for (let i = 0; i < 4; i++) {
  const r = await fetch(`${BASE}/v1/check`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ deal, answer: i }),
  });
  verdicts.push((await r.json()).correct);
}
if (verdicts.filter(Boolean).length === 1 && sealed.questions[0].answer === undefined) {
  console.log("  ok   200 sealed question scores exactly one option and leaks no answer");
} else {
  failures++;
  console.log(`  FAIL      sealed round trip — verdicts ${JSON.stringify(verdicts)}`);
}

// Token round trip over the wire.
const minted = await (await fetch(`${BASE}/v1/tokens`, { method: "POST" })).json();
const seen = new Set();
let repeated = false;
for (let i = 0; i < 3; i++) {
  const page = await (await fetch(`${BASE}/v1/questions?amount=10&pattern=language-of&token=${minted.token}`)).json();
  for (const q of page.questions ?? []) {
    if (seen.has(q.id)) repeated = true;
    seen.add(q.id);
  }
}
const drained = await fetch(`${BASE}/v1/questions?amount=5&pattern=language-of&token=${minted.token}`);
if (!repeated && seen.size === 28 && drained.status === 409) {
  console.log("  ok   409 session token drew all 28 without a repeat, then reported exhaustion");
} else {
  failures++;
  console.log(`  FAIL      token round trip — repeated=${repeated} seen=${seen.size} drained=${drained.status}`);
}

server.kill("SIGTERM");
console.log(failures === 0 ? "\nall smoke checks passed\n" : `\n${failures} smoke check(s) failed\n`);
process.exit(failures === 0 ? 0 : 1);
