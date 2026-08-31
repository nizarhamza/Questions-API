import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const BASE = "https://questions.test";

async function get(path: string) {
  const response = await SELF.fetch(BASE + path);
  return { response, body: (await response.json()) as any };
}

async function post(path: string, payload?: unknown) {
  const response = await SELF.fetch(BASE + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  return { response, body: (await response.json()) as any };
}

describe("service basics", () => {
  it("reports health with the bank size", async () => {
    const { response, body } = await get("/health");
    expect(response.status).toBe(200);
    expect(body.status).toBe("ok");
    expect(body.questions).toBe(6255);
  });

  it("serves the docs page at the root", async () => {
    const response = await SELF.fetch(BASE + "/");
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/html");
    expect(await response.text()).toContain("Questions API");
  });

  it("answers CORS preflight and sets CORS on real responses", async () => {
    const preflight = await SELF.fetch(BASE + "/v1/questions", { method: "OPTIONS" });
    expect(preflight.status).toBe(204);
    expect(preflight.headers.get("access-control-allow-origin")).toBe("*");

    const { response } = await get("/v1/categories");
    expect(response.headers.get("access-control-allow-origin")).toBe("*");
  });

  it("404s an unknown route with a usable message", async () => {
    const { response, body } = await get("/v1/nope");
    expect(response.status).toBe(404);
    expect(body.error.code).toBe("not_found");
  });

  it("rejects verbs it does not speak", async () => {
    const response = await SELF.fetch(BASE + "/v1/questions", { method: "DELETE" });
    expect(response.status).toBe(405);
  });
});

describe("native: metadata", () => {
  it("lists categories whose counts sum to the bank total", async () => {
    const { body } = await get("/v1/categories");
    expect(body.count).toBe(4);

    const total = body.categories.reduce((sum: number, c: any) => sum + c.counts.total, 0);
    expect(total).toBe(6255);

    for (const c of body.categories) {
      expect(c.counts.easy + c.counts.medium + c.counts.hard).toBe(c.counts.total);
    }
  });

  it("keeps OpenTDB's numeric ids for the categories it shares", async () => {
    const { body } = await get("/v1/categories");
    const ids = Object.fromEntries(body.categories.map((c: any) => [c.slug, c.id]));
    expect(ids).toMatchObject({ geography: 22, history: 23, film: 11, science: 17 });
  });

  it("reports patterns that sum to the bank total", async () => {
    const { body } = await get("/v1/patterns");
    expect(body.count).toBe(10);
    const total = body.patterns.reduce((sum: number, p: any) => sum + p.count, 0);
    expect(total).toBe(6255);
  });

  it("caches static metadata at the edge but never a draw", async () => {
    const stats = await SELF.fetch(BASE + "/v1/stats");
    expect(stats.headers.get("cache-control")).toContain("s-maxage");

    const draw = await SELF.fetch(BASE + "/v1/questions?amount=1");
    expect(draw.headers.get("cache-control")).toBe("no-store");
  });
});

describe("native: drawing questions", () => {
  it("returns ten questions by default, each internally consistent", async () => {
    const { body } = await get("/v1/questions");
    expect(body.count).toBe(10);

    for (const q of body.questions) {
      expect(q.options).toHaveLength(4);
      expect(new Set(q.options).size).toBe(4);
      expect(q.answer).toBe(q.options[q.answer_index]);
      expect(q.id).toMatch(/^[a-z]{3}-[emh]-\d{4}$/);
      expect(q.tags).toEqual([q.category, q.pattern]);
    }
  });

  it("never repeats a question inside one draw", async () => {
    const { body } = await get("/v1/questions?amount=50&category=science");
    const ids = body.questions.map((q: any) => q.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("honours category, difficulty and pattern filters together", async () => {
    const { body } = await get("/v1/questions?amount=20&category=geography&difficulty=hard&pattern=currency-of");
    expect(body.count).toBeGreaterThan(0);
    for (const q of body.questions) {
      expect(q.category).toBe("geography");
      expect(q.difficulty).toBe("hard");
      expect(q.pattern).toBe("currency-of");
    }
  });

  it("accepts a numeric OpenTDB category id on the native surface too", async () => {
    const { body } = await get("/v1/questions?amount=5&category=22");
    for (const q of body.questions) expect(q.category).toBe("geography");
  });

  it("caps the draw at the pool size instead of erroring", async () => {
    const { body } = await get("/v1/questions?amount=100&pattern=language-of");
    expect(body.pool_size).toBe(28);
    expect(body.count).toBe(28);
  });

  it("is reproducible for a given seed and different without one", async () => {
    const a = await get("/v1/questions?amount=5&seed=kickoff-42");
    const b = await get("/v1/questions?amount=5&seed=kickoff-42");
    const c = await get("/v1/questions?amount=5&seed=kickoff-43");

    const ids = (r: any) => r.body.questions.map((q: any) => q.id);
    expect(ids(a)).toEqual(ids(b));
    expect(ids(a)).not.toEqual(ids(c));
  });

  it("rejects bad filters by name", async () => {
    expect((await get("/v1/questions?category=quidditch")).body.error.code).toBe("unknown_category");
    expect((await get("/v1/questions?difficulty=brutal")).body.error.code).toBe("unknown_difficulty");
    expect((await get("/v1/questions?pattern=nope")).body.error.code).toBe("unknown_pattern");
    expect((await get("/v1/questions?amount=0")).body.error.code).toBe("invalid_amount");
    expect((await get("/v1/questions?amount=abc")).body.error.code).toBe("invalid_amount");
  });

  it("fetches a single question by id", async () => {
    const { body } = await get("/v1/questions/geo-e-0001");
    expect(body.id).toBe("geo-e-0001");
    expect(body.answer).toBe(body.options[body.answer_index]);

    const missing = await get("/v1/questions/geo-e-9999");
    expect(missing.response.status).toBe(404);
  });
});

describe("native: sealed questions and checking", () => {
  it("withholds the answer and scores it through the deal", async () => {
    const { body } = await get("/v1/questions?amount=1&reveal=false");
    const q = body.questions[0];

    expect(body.sealed).toBe(true);
    expect(q.answer).toBeUndefined();
    expect(q.answer_index).toBeUndefined();
    expect(q.deal).toBeTruthy();
    expect(q.options).toHaveLength(4);

    // Find the right one the only way a client legitimately can: by trying.
    const verdicts = await Promise.all(
      [0, 1, 2, 3].map((i) => post("/v1/check", { deal: q.deal, answer: i })),
    );
    const correct = verdicts.filter((v) => v.body.correct);
    expect(correct).toHaveLength(1);
    expect(correct[0]!.body.id).toBe(q.id);
    expect(correct[0]!.body.explanation).toBeTruthy();
  });

  it("refuses a tampered deal", async () => {
    const { body } = await get("/v1/questions?amount=1&reveal=false");
    const deal: string = body.questions[0].deal;
    const parts = deal.split(".");

    // Same question, claim a different correct position, keep the old signature.
    const forged = [parts[0], String((Number(parts[1]) + 1) % 4), parts[2], parts[3]].join(".");
    const { body: verdict } = await post("/v1/check", { deal: forged, answer: 0 });
    expect(verdict.ok).toBe(false);
    expect(verdict.error).toBe("invalid_deal");
  });

  it("scores an open question by id, by index or by text", async () => {
    const { body: q } = await get("/v1/questions/geo-e-0001");

    const byIndex = await post("/v1/check", { id: q.id, answer: q.answer_index });
    expect(byIndex.body.correct).toBe(true);

    const byText = await post("/v1/check", { id: q.id, answer: q.answer.toUpperCase() });
    expect(byText.body.correct).toBe(true);

    const wrong = await post("/v1/check", { id: q.id, answer: (q.answer_index + 1) % 4 });
    expect(wrong.body.correct).toBe(false);
    expect(wrong.body.correct_answer).toBe(q.answer);
  });

  it("scores a whole quiz in one request", async () => {
    const { body } = await get("/v1/questions?amount=4");
    const answers = body.questions.map((q: any, i: number) => ({
      id: q.id,
      answer: i < 3 ? q.answer_index : (q.answer_index + 1) % 4,
    }));

    const { body: result } = await post("/v1/check", { answers });
    expect(result.out_of).toBe(4);
    expect(result.score).toBe(3);
  });

  it("reports useless input rather than guessing", async () => {
    expect((await post("/v1/check", { answer: 1 })).body.error).toBe("missing_id_or_dea"+"l");
    expect((await post("/v1/check", { id: "geo-e-0001", answer: "banana" })).body.error).toBe("invalid_answer");
    expect((await post("/v1/check", { deal: "garbage" })).body.error).toBe("invalid_deal");

    const badJson = await SELF.fetch(BASE + "/v1/check", { method: "POST", body: "{" });
    expect(badJson.status).toBe(400);
  });
});

describe("native: session tokens", () => {
  it("never repeats a question across draws, then reports exhaustion", async () => {
    const { body: minted } = await post("/v1/tokens");
    const token: string = minted.token;
    expect(token).toMatch(/^[0-9a-f]{40}$/);

    const seen = new Set<string>();
    for (let i = 0; i < 3; i++) {
      const { body } = await get(`/v1/questions?amount=10&pattern=language-of&token=${token}`);
      for (const q of body.questions) {
        expect(seen.has(q.id)).toBe(false);
        seen.add(q.id);
      }
    }
    // The language-of pool holds 28; three draws of ten empty it.
    expect(seen.size).toBe(28);

    const drained = await get(`/v1/questions?amount=10&pattern=language-of&token=${token}`);
    expect(drained.response.status).toBe(409);
    expect(drained.body.error.code).toBe("token_exhausted");

    const info = await get(`/v1/tokens/${token}`);
    expect(info.body.seen).toBe(28);

    await post(`/v1/tokens/${token}/reset`);
    const afterReset = await get(`/v1/questions?amount=10&pattern=language-of&token=${token}`);
    expect(afterReset.body.count).toBe(10);
  });

  it("rejects a token it never issued", async () => {
    const fake = "a".repeat(40);
    const draw = await get(`/v1/questions?amount=5&token=${fake}`);
    expect(draw.response.status).toBe(404);
    expect(draw.body.error.code).toBe("unknown_token");

    expect((await get(`/v1/tokens/${fake}`)).response.status).toBe(404);
  });
});

describe("opentdb compatibility", () => {
  it("returns OpenTDB's exact result shape", async () => {
    const { body } = await get("/api.php?amount=3&category=22&difficulty=easy");
    expect(body.response_code).toBe(0);
    expect(body.results).toHaveLength(3);

    for (const r of body.results) {
      expect(Object.keys(r).sort()).toEqual(
        ["category", "correct_answer", "difficulty", "incorrect_answers", "question", "type"],
      );
      expect(r.type).toBe("multiple");
      expect(r.difficulty).toBe("easy");
      expect(r.incorrect_answers).toHaveLength(3);
      expect(new Set([r.correct_answer, ...r.incorrect_answers]).size).toBe(4);
    }
  });

  it("adds ids and explanations only when asked", async () => {
    const { body } = await get("/api.php?amount=1&extended=1");
    expect(body.results[0].id).toMatch(/^[a-z]{3}-[emh]-\d{4}$/);
    expect(body.results[0].explanation).toBeTruthy();
  });

  it("uses the OpenTDB response_code vocabulary", async () => {
    expect((await get("/api.php?amount=0")).body.response_code).toBe(2);
    expect((await get("/api.php?category=999")).body.response_code).toBe(2);
    expect((await get("/api.php?difficulty=impossible")).body.response_code).toBe(2);
    expect((await get("/api.php?encode=rot13")).body.response_code).toBe(2);
    // A bank of four-option questions cannot fill a true/false request.
    expect((await get("/api.php?type=boolean")).body.response_code).toBe(1);
    expect((await get("/api.php?token=nope")).body.response_code).toBe(3);
  });

  it("empties a token with code 4", async () => {
    const minted = (await get("/api_token.php?command=request")).body;
    expect(minted.response_code).toBe(0);
    const token = minted.token;

    for (let i = 0; i < 6; i++) await get(`/api.php?amount=50&category=17&difficulty=easy&token=${token}`);
    const drained = (await get(`/api.php?amount=50&category=17&difficulty=easy&token=${token}`)).body;
    expect(drained.response_code).toBe(4);

    const reset = (await get(`/api_token.php?command=reset&token=${token}`)).body;
    expect(reset.response_code).toBe(0);
    expect((await get(`/api.php?amount=5&category=17&difficulty=easy&token=${token}`)).body.response_code).toBe(0);
  });

  it("rejects an unknown token command", async () => {
    expect((await get("/api_token.php?command=explode")).body.response_code).toBe(2);
    expect((await get("/api_token.php?command=reset&token=deadbeef")).body.response_code).toBe(3);
  });

  it("encodes text the four ways OpenTDB does", async () => {
    const plain = await get("/api.php?amount=1&category=22&difficulty=easy&seed=x");
    expect(plain.body.results[0].question).not.toContain("%");

    const url = await get("/api.php?amount=1&encode=url3986");
    expect(decodeURIComponent(url.body.results[0].question)).toMatch(/[a-z]/i);

    const b64 = await get("/api.php?amount=1&encode=base64");
    expect(atob(b64.body.results[0].question)).toMatch(/[a-z]/i);

    const legacy = await get("/api.php?amount=1&encode=legacy");
    expect(legacy.body.results[0].question).toContain("+");
  });

  it("serves the metadata endpoints in OpenTDB's shape", async () => {
    const cats = await get("/api_category.php");
    expect(cats.body.trivia_categories).toContainEqual({ id: 22, name: "Geography" });

    const count = await get("/api_count.php?category=22");
    expect(count.body.category_id).toBe(22);
    expect(count.body.category_question_count.total_question_count).toBe(672);

    const global = await get("/api_count_global.php");
    expect(global.body.overall.total_num_of_questions).toBe(6255);
    expect(global.body.categories["23"].total_num_of_questions).toBe(4980);
  });
});
