// The native /v1 surface: the bank's own vocabulary, with the pattern tags and
// provenance that OpenTDB's schema has nowhere to put.

import {
  BANK_VERSION, CATEGORIES, GENERATED, PATTERNS, SOURCE, SOURCES, TOTAL,
  countPool, getQuestion, getQuestionByIndex, resolveCategory, resolveDifficulty,
  type Question,
} from "../bank";
import { createToken, draw, resetToken, tokenInfo } from "../draw";
import { apiError, intParam, json } from "../http";
import { hashString, seededRandom, shuffle } from "../rng";
import { signDeal, verifyDeal } from "../sign";
import { signingSecret, type Env } from "../env";

/** How long a sealed question stays answerable. Long enough for a slow quiz, short
 *  enough that a harvested deal is worthless by the time it is traded. */
const DEAL_TTL_SECONDS = 3600;

function openRecord(q: Question): Record<string, unknown> {
  return {
    id: q.id,
    question: q.question,
    options: q.options,
    answer_index: q.answerIndex,
    answer: q.options[q.answerIndex],
    explanation: q.explanation,
    category: q.category,
    category_id: q.categoryId,
    category_name: q.categoryName,
    difficulty: q.difficulty,
    pattern: q.pattern,
    tags: [q.category, q.pattern],
  };
}

async function sealedRecord(q: Question, secret: string, rand?: () => number): Promise<Record<string, unknown>> {
  const correct = q.options[q.answerIndex]!;
  const shown = shuffle(q.options, rand);
  const position = shown.indexOf(correct);
  const expires = Math.floor(Date.now() / 1000) + DEAL_TTL_SECONDS;

  return {
    id: q.id,
    question: q.question,
    options: shown,
    // No answer, no index. The deal is the only thing that can score this, and only
    // the server can read it.
    deal: await signDeal({ index: q.index, position, expires }, secret),
    expires_at: new Date(expires * 1000).toISOString(),
    category: q.category,
    category_id: q.categoryId,
    category_name: q.categoryName,
    difficulty: q.difficulty,
    pattern: q.pattern,
    tags: [q.category, q.pattern],
  };
}

export async function handleQuestions(url: URL, env: Env): Promise<Response> {
  const params = url.searchParams;

  const amount = intParam(params.get("amount") ?? params.get("limit"), 10, 1, 100);
  if (amount === undefined) return apiError(400, "invalid_amount", "amount must be an integer between 1 and 100.");

  const category = resolveCategory(params.get("category"));
  if (category === undefined) {
    return apiError(400, "unknown_category", `No such category. Known slugs: ${CATEGORIES.map((c) => c.slug).join(", ")}.`);
  }

  const difficulty = resolveDifficulty(params.get("difficulty"));
  if (difficulty === undefined) return apiError(400, "unknown_difficulty", "difficulty must be easy, medium or hard.");

  const pattern = params.get("pattern");
  if (pattern && !PATTERNS.includes(pattern)) {
    return apiError(400, "unknown_pattern", `No such pattern. Known patterns: ${PATTERNS.join(", ")}.`);
  }

  const token = params.get("token");
  const seed = params.get("seed");
  const reveal = params.get("reveal") !== "false" && params.get("reveal") !== "0";

  const query = { category: category?.slug ?? null, difficulty, pattern };
  const outcome = await draw(env, { query, amount, token, seed });

  switch (outcome.status) {
    case "unknown_token":
      return apiError(404, "unknown_token", "That token was never issued, or it has expired. Request a new one from POST /v1/tokens.");
    case "no_results":
      return apiError(404, "no_results", "No questions match that combination of filters.", {
        filters: { category: query.category, difficulty: query.difficulty, pattern: query.pattern },
      });
    case "exhausted":
      return apiError(409, "token_exhausted", "This token has already seen every question matching those filters. Reset it, or widen the filters.", {
        reset: "POST /v1/tokens/{token}/reset",
      });
  }

  // Sealed options are shuffled per request; with ?seed= that shuffle has to be
  // reproducible too, or the same seed would deal the same questions in different
  // option orders.
  const rand = seed ? seededRandom(hashString(seed)) : undefined;
  const questions = reveal
    ? outcome.questions.map(openRecord)
    : await Promise.all(outcome.questions.map((q) => sealedRecord(q, signingSecret(env), rand)));

  return json({
    count: questions.length,
    pool_size: countPool(query),
    filters: { category: query.category, difficulty: query.difficulty, pattern: query.pattern },
    sealed: !reveal,
    ...(token ? { token, remaining: outcome.remaining } : {}),
    questions,
  });
}

export function handleQuestionById(id: string): Response {
  const q = getQuestion(id);
  if (!q) return apiError(404, "not_found", `No question with id ${id}.`);
  return json(openRecord(q), { cacheSeconds: 86400 });
}

interface CheckItem {
  deal?: unknown;
  id?: unknown;
  answer?: unknown;
}

function resolveAnswer(q: Question, shownOptions: string[] | null, answer: unknown): number | null {
  if (typeof answer === "number" && Number.isInteger(answer)) {
    return answer >= 0 && answer < 4 ? answer : null;
  }
  if (typeof answer === "string") {
    const trimmed = answer.trim();
    if (/^[0-3]$/.test(trimmed)) return Number(trimmed);
    // Matching by text is the forgiving path a mobile client tends to want.
    const haystack = shownOptions ?? q.options;
    const hit = haystack.findIndex((o) => o.toLowerCase() === trimmed.toLowerCase());
    return hit === -1 ? null : hit;
  }
  return null;
}

async function checkOne(item: CheckItem, env: Env): Promise<Record<string, unknown>> {
  if (typeof item.deal === "string" && item.deal) {
    const verdict = await verifyDeal(item.deal, signingSecret(env));
    if (!verdict.ok) {
      return { ok: false, error: verdict.reason === "expired" ? "deal_expired" : "invalid_deal" };
    }
    const q = getQuestionByIndex(verdict.payload.index);
    if (!q) return { ok: false, error: "invalid_deal" };

    // The deal carries the position in the order the client was shown, so it scores
    // against that order — not against the bank's canonical option order.
    const given = typeof item.answer === "number" || /^[0-3]$/.test(String(item.answer ?? ""))
      ? Number(item.answer)
      : q.options.findIndex((o) => o.toLowerCase() === String(item.answer ?? "").trim().toLowerCase());

    const correct = given === verdict.payload.position;
    return {
      ok: true,
      id: q.id,
      correct,
      correct_position: verdict.payload.position,
      correct_answer: q.options[q.answerIndex],
      explanation: q.explanation,
    };
  }

  if (typeof item.id === "string" && item.id) {
    const q = getQuestion(item.id);
    if (!q) return { ok: false, error: "not_found", id: item.id };
    const given = resolveAnswer(q, null, item.answer);
    if (given === null) return { ok: false, error: "invalid_answer", id: q.id };
    return {
      ok: true,
      id: q.id,
      correct: given === q.answerIndex,
      correct_index: q.answerIndex,
      correct_answer: q.options[q.answerIndex],
      explanation: q.explanation,
    };
  }

  return { ok: false, error: "missing_id_or_deal" };
}

export async function handleCheck(request: Request, env: Env): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return apiError(400, "invalid_json", "Request body must be JSON.");
  }

  const asObject = body as { answers?: unknown };
  if (Array.isArray(asObject?.answers)) {
    if (asObject.answers.length > 100) {
      return apiError(400, "too_many", "Submit at most 100 answers per request.");
    }
    const results = await Promise.all(asObject.answers.map((a) => checkOne(a as CheckItem, env)));
    const scored = results.filter((r) => r.ok);
    return json({
      count: results.length,
      score: scored.filter((r) => r.correct).length,
      out_of: scored.length,
      results,
    });
  }

  return json(await checkOne(body as CheckItem, env));
}

export function handleCategories(): Response {
  return json(
    {
      count: CATEGORIES.length,
      categories: CATEGORIES.map((c) => ({
        slug: c.slug,
        id: c.id,
        name: c.name,
        counts: c.counts,
      })),
    },
    { cacheSeconds: 3600 },
  );
}

export function handlePatterns(): Response {
  return json(
    {
      count: PATTERNS.length,
      patterns: PATTERNS.map((p) => ({ pattern: p, count: countPool({ pattern: p }) })),
    },
    { cacheSeconds: 3600 },
  );
}

export function handleStats(): Response {
  return json(
    {
      total: TOTAL,
      bank_version: BANK_VERSION,
      generated: GENERATED,
      source: SOURCE,
      sources: SOURCES,
      license: SOURCES.join("; ") || "CC0 (Wikidata)",
      categories: Object.fromEntries(CATEGORIES.map((c) => [c.slug, c.counts])),
      patterns: Object.fromEntries(PATTERNS.map((p) => [p, countPool({ pattern: p })])),
    },
    { cacheSeconds: 3600 },
  );
}

export async function handleTokenCreate(env: Env): Promise<Response> {
  const token = await createToken(env);
  const ttl = Number(env.TOKEN_TTL_SECONDS ?? "21600");
  return json({
    token,
    total: TOTAL,
    seen: 0,
    expires_after_idle_seconds: ttl,
    usage: "Pass ?token=… to /v1/questions and no question repeats until the pool runs dry.",
  }, { status: 201 });
}

export async function handleTokenInfo(token: string, env: Env): Promise<Response> {
  const info = await tokenInfo(env, token);
  if (!info) return apiError(404, "unknown_token", "That token was never issued, or it has expired.");
  return json({
    token,
    seen: info.seen,
    total: info.total,
    remaining: info.total - info.seen,
    created_at: new Date(info.created * 1000).toISOString(),
    last_used_at: new Date(info.lastUsed * 1000).toISOString(),
  });
}

export async function handleTokenReset(token: string, env: Env): Promise<Response> {
  const info = await resetToken(env, token);
  if (!info) return apiError(404, "unknown_token", "That token was never issued, or it has expired.");
  return json({ token, seen: 0, total: info.total, remaining: info.total });
}
