// Sampling helpers.
//
// Draws use crypto randomness by default. The seeded generator exists so a client
// can ask for a reproducible quiz (?seed=), which matters for shared games where
// two players must get the same paper.

/** mulberry32 — small, fast, and good enough for shuffling a quiz. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function hashString(input: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

function cryptoRandom(): number {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return buf[0]! / 4294967296;
}

/**
 * Partial Fisher-Yates: touches `count` slots, not the whole pool. Drawing 10 of
 * 4,980 history questions costs 10 swaps rather than a 4,980-element shuffle, which
 * is the difference that keeps a full-bank draw off the CPU limit.
 */
export function sampleIndexes(pool: ArrayLike<number>, count: number, rand: () => number = cryptoRandom): number[] {
  const n = pool.length;
  const take = Math.min(count, n);
  if (take === 0) return [];

  const scratch = new Map<number, number>();
  const at = (i: number) => scratch.get(i) ?? pool[i]!;

  const out: number[] = [];
  for (let i = 0; i < take; i++) {
    const j = i + Math.floor(rand() * (n - i));
    const vi = at(i);
    const vj = at(j);
    scratch.set(j, vi);
    scratch.set(i, vj);
    out.push(vj);
  }
  return out;
}

export function shuffle<T>(items: T[], rand: () => number = cryptoRandom): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const tmp = out[i]!;
    out[i] = out[j]!;
    out[j] = tmp;
  }
  return out;
}

export function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}
