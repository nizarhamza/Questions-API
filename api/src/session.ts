// Session tokens: "never show me the same question twice".
//
// State is a bitset over global bank indexes — 6,255 questions is 782 bytes, so the
// whole seen-set is read and written on every draw without thinking about it. A
// Durable Object rather than KV because a client pulling ten questions in a row
// would blow straight through KV's one-write-per-second-per-key ceiling, and would
// read its own stale writes when it did.

import { DurableObject } from "cloudflare:workers";
import { TOTAL, getPool, type PoolQuery } from "./bank";
import { sampleIndexes } from "./rng";

export type DrawStatus = "ok" | "exhausted" | "no_results" | "unknown_token";

export interface DrawResult {
  status: DrawStatus;
  indexes: number[];
  /** How many of this pool the token has not yet been shown, after the draw. */
  remaining: number;
}

export interface TokenInfo {
  created: number;
  lastUsed: number;
  seen: number;
  total: number;
}

interface Env {
  TOKEN_TTL_SECONDS?: string;
}

export class SessionToken extends DurableObject<Env> {
  private async bitset(): Promise<Uint8Array> {
    const stored = await this.ctx.storage.get<ArrayBuffer>("seen");
    const size = Math.ceil(TOTAL / 8);
    if (!stored) return new Uint8Array(size);
    const existing = new Uint8Array(stored);
    // The bank grows between deploys; widen rather than reset, so a token issued
    // before new questions landed keeps its history and simply gains candidates.
    if (existing.length < size) {
      const widened = new Uint8Array(size);
      widened.set(existing);
      return widened;
    }
    return existing;
  }

  private async touch(): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    if (!(await this.ctx.storage.get<number>("created"))) {
      await this.ctx.storage.put("created", now);
    }
    await this.ctx.storage.put("lastUsed", now);

    const ttl = Number(this.env.TOKEN_TTL_SECONDS ?? "21600");
    if (Number.isFinite(ttl) && ttl > 0) {
      // Idle tokens evaporate; each use pushes the alarm out again.
      await this.ctx.storage.setAlarm(Date.now() + ttl * 1000);
    }
  }

  /** Called when the token has gone untouched for its TTL. */
  async alarm(): Promise<void> {
    await this.ctx.storage.deleteAll();
  }

  async draw(query: PoolQuery, amount: number): Promise<DrawResult> {
    // A Durable Object exists for every name you can spell, so "was this token ever
    // issued?" is a storage question, not an addressing one. Without this check any
    // 40-hex string would behave like a valid token.
    if (!(await this.ctx.storage.get<number>("created"))) {
      return { status: "unknown_token", indexes: [], remaining: 0 };
    }

    const pool = getPool(query);
    if (pool.length === 0) return { status: "no_results", indexes: [], remaining: 0 };

    const seen = await this.bitset();
    const candidates: number[] = [];
    for (const idx of pool) {
      if ((seen[idx >> 3]! & (1 << (idx & 7))) === 0) candidates.push(idx);
    }

    if (candidates.length === 0) {
      await this.touch();
      return { status: "exhausted", indexes: [], remaining: 0 };
    }

    const picked = sampleIndexes(candidates, amount);
    for (const idx of picked) {
      const byte = idx >> 3;
      seen[byte] = seen[byte]! | (1 << (idx & 7));
    }

    await this.ctx.storage.put("seen", seen.buffer as ArrayBuffer);
    await this.touch();

    return { status: "ok", indexes: picked, remaining: candidates.length - picked.length };
  }

  async reset(): Promise<TokenInfo | null> {
    if (!(await this.ctx.storage.get<number>("created"))) return null;
    await this.ctx.storage.put("seen", new Uint8Array(Math.ceil(TOTAL / 8)).buffer as ArrayBuffer);
    await this.touch();
    return this.info();
  }

  async info(): Promise<TokenInfo | null> {
    const created = await this.ctx.storage.get<number>("created");
    if (!created) return null;
    const seen = await this.bitset();
    let count = 0;
    for (const byte of seen) {
      // Brian Kernighan: one iteration per set bit, not per bit.
      let b = byte;
      while (b) {
        b &= b - 1;
        count++;
      }
    }
    return {
      created,
      lastUsed: (await this.ctx.storage.get<number>("lastUsed")) ?? 0,
      seen: count,
      total: TOTAL,
    };
  }

  /** First contact for a freshly minted token, so `created` is set before any draw. */
  async init(): Promise<TokenInfo> {
    await this.touch();
    return (await this.info())!;
  }
}
