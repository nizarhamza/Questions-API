import type { SessionToken } from "./session";

export interface Env {
  SESSIONS: DurableObjectNamespace<SessionToken>;
  /** Both optional: the Worker runs without rate limiting rather than failing closed. */
  RL_GENERAL?: RateLimit;
  RL_TOKENS?: RateLimit;
  /** wrangler secret put SIGNING_SECRET. Falls back to a dev-only constant. */
  SIGNING_SECRET?: string;
  PUBLIC_BASE_URL?: string;
  TOKEN_TTL_SECONDS?: string;
}

export function signingSecret(env: Env): string {
  return env.SIGNING_SECRET ?? "questions-api-development-secret-do-not-ship";
}
