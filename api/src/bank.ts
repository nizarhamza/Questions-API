// The question bank, decoded once per isolate and indexed for pool lookups.

import raw from "./data/bank.json";

export type Difficulty = "easy" | "medium" | "hard";

export interface Question {
  /** Position in the global array. Stable across deploys; session-token bitsets index by it. */
  index: number;
  id: string;
  question: string;
  options: string[];
  answerIndex: number;
  explanation: string;
  category: string;
  categoryName: string;
  categoryId: number;
  difficulty: Difficulty;
  pattern: string;
}

export interface Category {
  slug: string;
  id: number;
  name: string;
  counts: { easy: number; medium: number; hard: number; total: number };
}

type Tuple = [string, string, string[], number, string, number, number, number];

const data = raw as unknown as {
  version: number;
  generated: string;
  source: string;
  sources?: string[];
  seed: number;
  difficulties: Difficulty[];
  categories: Category[];
  patterns: string[];
  questions: Tuple[];
};

export const DIFFICULTIES = data.difficulties;
export const CATEGORIES = data.categories;
export const PATTERNS = data.patterns;
export const GENERATED = data.generated;
export const SOURCE = data.source;
export const SOURCES = data.sources ?? (data.source ? [data.source] : []);
export const BANK_VERSION = data.version;

export const QUESTIONS: Question[] = data.questions.map((t, i) => {
  const cat = data.categories[t[5]]!;
  return {
    index: i,
    id: t[0],
    question: t[1],
    options: t[2],
    answerIndex: t[3],
    explanation: t[4],
    category: cat.slug,
    categoryName: cat.name,
    categoryId: cat.id,
    difficulty: data.difficulties[t[6]]!,
    pattern: data.patterns[t[7]]!,
  };
});

export const TOTAL = QUESTIONS.length;

const byId = new Map<string, Question>();
for (const q of QUESTIONS) byId.set(q.id, q);

const bySlug = new Map<string, Category>();
const byNumericId = new Map<number, Category>();
for (const c of CATEGORIES) {
  bySlug.set(c.slug, c);
  byNumericId.set(c.id, c);
}

export function getQuestion(id: string): Question | undefined {
  return byId.get(id);
}

export function getQuestionByIndex(index: number): Question | undefined {
  return QUESTIONS[index];
}

/** Accepts a slug ("geography"), an OpenTDB numeric id ("22"), or a display name. */
export function resolveCategory(input: string | null): Category | null | undefined {
  if (input === null || input === "" || input === "any") return null; // null = every category
  const trimmed = input.trim();
  const bySlugHit = bySlug.get(trimmed.toLowerCase());
  if (bySlugHit) return bySlugHit;
  if (/^\d+$/.test(trimmed)) return byNumericId.get(Number(trimmed)); // undefined = not found
  const lowered = trimmed.toLowerCase();
  return CATEGORIES.find((c) => c.name.toLowerCase() === lowered);
}

export function resolveDifficulty(input: string | null): Difficulty | null | undefined {
  if (input === null || input === "" || input === "any") return null;
  const lowered = input.trim().toLowerCase();
  return (DIFFICULTIES as string[]).includes(lowered) ? (lowered as Difficulty) : undefined;
}

export interface PoolQuery {
  category?: string | null;
  difficulty?: Difficulty | null;
  pattern?: string | null;
}

/**
 * Pools are precomputed per (category, difficulty) and memoised per isolate, so a
 * request never walks all 6,255 records. `pattern` is the one filter narrow enough
 * to be worth a linear scan of an already-small pool.
 */
const poolCache = new Map<string, Int32Array>();

export function poolKey(q: PoolQuery): string {
  return `${q.category ?? "*"}|${q.difficulty ?? "*"}|${q.pattern ?? "*"}`;
}

export function getPool(q: PoolQuery): Int32Array {
  const key = poolKey(q);
  const cached = poolCache.get(key);
  if (cached) return cached;

  const out: number[] = [];
  for (const item of QUESTIONS) {
    if (q.category && item.category !== q.category) continue;
    if (q.difficulty && item.difficulty !== q.difficulty) continue;
    if (q.pattern && item.pattern !== q.pattern) continue;
    out.push(item.index);
  }
  const arr = Int32Array.from(out);
  poolCache.set(key, arr);
  return arr;
}

export function countPool(q: PoolQuery): number {
  return getPool(q).length;
}
