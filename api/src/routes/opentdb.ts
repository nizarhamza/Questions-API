// The OpenTDB-compatible surface.
//
// Paths, parameter names, field names and the response_code vocabulary all match
// opentdb.com, so a client can be repointed here by changing a base URL and nothing
// else. Where OpenTDB is quirky (HTML-entity encoding by default, numeric category
// ids, an `amount` ceiling of 50) the quirk is reproduced rather than improved.

import { CATEGORIES, countPool, resolveCategory, resolveDifficulty, type Question } from "../bank";
import { draw, createToken, isTokenShaped, resetToken } from "../draw";
import { encodeText, resolveEncoding, type EncodeMode } from "../encode";
import { intParam, json } from "../http";
import { shuffle } from "../rng";
import type { Env } from "../env";

export const RESPONSE_CODES = {
  SUCCESS: 0,
  NO_RESULTS: 1,
  INVALID_PARAMETER: 2,
  TOKEN_NOT_FOUND: 3,
  TOKEN_EMPTY: 4,
  RATE_LIMIT: 5,
} as const;

function codeOnly(code: number, status = 200): Response {
  return json({ response_code: code, results: [] }, { status });
}

function toOpenTdb(q: Question, encoding: EncodeMode, extended: boolean): Record<string, unknown> {
  const enc = (s: string) => encodeText(s, encoding);
  const incorrect = q.options.filter((_, i) => i !== q.answerIndex);

  const record: Record<string, unknown> = {
    type: "multiple",
    difficulty: q.difficulty,
    category: enc(q.categoryName),
    question: enc(q.question),
    correct_answer: enc(q.options[q.answerIndex]!),
    // OpenTDB leaves the client to shuffle, but ships them in a fixed order. Shuffling
    // here costs nothing and stops a lazy client from rendering the answer in a
    // predictable slot.
    incorrect_answers: shuffle(incorrect).map(enc),
  };

  if (extended) {
    record.id = q.id;
    record.pattern = q.pattern;
    record.explanation = enc(q.explanation);
  }
  return record;
}

export async function handleQuestions(url: URL, env: Env): Promise<Response> {
  const params = url.searchParams;

  const encoding = resolveEncoding(params.get("encode"));
  if (!encoding) return codeOnly(RESPONSE_CODES.INVALID_PARAMETER);

  const amount = intParam(params.get("amount"), 10, 1, 50);
  if (amount === undefined) return codeOnly(RESPONSE_CODES.INVALID_PARAMETER);

  const category = resolveCategory(params.get("category"));
  if (category === undefined) return codeOnly(RESPONSE_CODES.INVALID_PARAMETER);

  const difficulty = resolveDifficulty(params.get("difficulty"));
  if (difficulty === undefined) return codeOnly(RESPONSE_CODES.INVALID_PARAMETER);

  // Every question in this bank is four-option multiple choice. `type=boolean` is a
  // valid request that this bank simply cannot fill, which is No Results, not an
  // invalid parameter.
  const type = params.get("type");
  if (type && !["multiple", "boolean", "any"].includes(type)) {
    return codeOnly(RESPONSE_CODES.INVALID_PARAMETER);
  }
  if (type === "boolean") return codeOnly(RESPONSE_CODES.NO_RESULTS);

  const token = params.get("token");
  if (token && !isTokenShaped(token)) return codeOnly(RESPONSE_CODES.TOKEN_NOT_FOUND);

  const outcome = await draw(env, {
    query: { category: category?.slug ?? null, difficulty },
    amount,
    token,
  });

  switch (outcome.status) {
    case "unknown_token": return codeOnly(RESPONSE_CODES.TOKEN_NOT_FOUND);
    case "no_results": return codeOnly(RESPONSE_CODES.NO_RESULTS);
    case "exhausted": return codeOnly(RESPONSE_CODES.TOKEN_EMPTY);
  }
  if (outcome.questions.length === 0) return codeOnly(RESPONSE_CODES.NO_RESULTS);

  const extended = params.get("extended") === "1" || params.get("extended") === "true";
  return json({
    response_code: RESPONSE_CODES.SUCCESS,
    results: outcome.questions.map((q) => toOpenTdb(q, encoding, extended)),
  });
}

export async function handleToken(url: URL, env: Env): Promise<Response> {
  const command = url.searchParams.get("command");

  if (command === "request") {
    const token = await createToken(env);
    return json({
      response_code: RESPONSE_CODES.SUCCESS,
      response_message: "Token Generated Successfully!",
      token,
    });
  }

  if (command === "reset") {
    const token = url.searchParams.get("token") ?? "";
    const info = await resetToken(env, token);
    if (!info) return json({ response_code: RESPONSE_CODES.TOKEN_NOT_FOUND, token: "" });
    return json({ response_code: RESPONSE_CODES.SUCCESS, token });
  }

  return json({ response_code: RESPONSE_CODES.INVALID_PARAMETER, token: "" });
}

export function handleCategoryList(): Response {
  return json(
    { trivia_categories: CATEGORIES.map((c) => ({ id: c.id, name: c.name })) },
    { cacheSeconds: 3600 },
  );
}

export function handleCategoryCount(url: URL): Response {
  const category = resolveCategory(url.searchParams.get("category"));
  if (!category) return json({ category_id: 0, category_question_count: null }, { status: 400 });

  return json(
    {
      category_id: category.id,
      category_question_count: {
        total_question_count: category.counts.total,
        total_easy_question_count: category.counts.easy,
        total_medium_question_count: category.counts.medium,
        total_hard_question_count: category.counts.hard,
      },
    },
    { cacheSeconds: 3600 },
  );
}

export function handleGlobalCount(): Response {
  const total = countPool({});
  const categories: Record<string, unknown> = {};
  for (const c of CATEGORIES) {
    categories[String(c.id)] = {
      total_num_of_questions: c.counts.total,
      total_num_of_pending_questions: 0,
      total_num_of_verified_questions: c.counts.total,
      total_num_of_rejected_questions: 0,
    };
  }
  return json(
    {
      overall: {
        total_num_of_questions: total,
        total_num_of_pending_questions: 0,
        // Every record is machine-derived from Wikidata and validated at build time;
        // there is no human moderation queue, so everything is "verified".
        total_num_of_verified_questions: total,
        total_num_of_rejected_questions: 0,
      },
      categories,
    },
    { cacheSeconds: 3600 },
  );
}
