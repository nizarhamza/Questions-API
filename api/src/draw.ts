// The one place questions are selected, shared by both API surfaces.

import { QUESTIONS, getPool, type PoolQuery, type Question } from "./bank";
import { sampleIndexes, seededRandom, hashString, randomHex } from "./rng";
import type { DrawStatus, TokenInfo } from "./session";
import type { Env } from "./env";

/** OpenTDB tokens are 40 alphanumeric characters; ours are 40 hex. */
const TOKEN_PATTERN = /^[0-9a-f]{40}$/;

export function isTokenShaped(token: string): boolean {
  return TOKEN_PATTERN.test(token);
}

export function newToken(): string {
  return randomHex(20);
}

function stub(env: Env, token: string) {
  return env.SESSIONS.get(env.SESSIONS.idFromName(token));
}

export interface DrawOutcome {
  status: DrawStatus;
  questions: Question[];
  /** Only meaningful for token draws. */
  remaining: number | null;
}

export interface DrawOptions {
  query: PoolQuery;
  amount: number;
  token?: string | null;
  /** Reproducible draw. Ignored when a token is in play — the token owns the ordering. */
  seed?: string | null;
}

export async function draw(env: Env, options: DrawOptions): Promise<DrawOutcome> {
  const { query, amount } = options;

  if (options.token) {
    const result = await stub(env, options.token).draw(query, amount);
    return {
      status: result.status,
      questions: result.indexes.map((i) => QUESTIONS[i]!),
      remaining: result.remaining,
    };
  }

  const pool = getPool(query);
  if (pool.length === 0) return { status: "no_results", questions: [], remaining: null };

  const rand = options.seed ? seededRandom(hashString(options.seed)) : undefined;
  const picked = sampleIndexes(pool, amount, rand);
  return { status: "ok", questions: picked.map((i) => QUESTIONS[i]!), remaining: null };
}

export async function createToken(env: Env): Promise<string> {
  const token = newToken();
  await stub(env, token).init();
  return token;
}

export async function resetToken(env: Env, token: string): Promise<TokenInfo | null> {
  if (!isTokenShaped(token)) return null;
  return stub(env, token).reset();
}

export async function tokenInfo(env: Env, token: string): Promise<TokenInfo | null> {
  if (!isTokenShaped(token)) return null;
  return stub(env, token).info();
}
