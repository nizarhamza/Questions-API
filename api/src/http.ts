// Response helpers: CORS, cache policy, and the shape of a native error.

const CORS_HEADERS: Record<string, string> = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
  "access-control-max-age": "86400",
};

export function withCors(response: Response): Response {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(CORS_HEADERS)) out.headers.set(k, v);
  return out;
}

export function preflight(): Response {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export interface JsonOptions {
  status?: number;
  /** Seconds of shared-cache lifetime. Omit for uncacheable (random) responses. */
  cacheSeconds?: number;
  headers?: Record<string, string>;
}

export function json(body: unknown, options: JsonOptions = {}): Response {
  const headers = new Headers(options.headers);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set(
    "cache-control",
    options.cacheSeconds ? `public, max-age=60, s-maxage=${options.cacheSeconds}` : "no-store",
  );
  for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
  return new Response(JSON.stringify(body, null, 2), { status: options.status ?? 200, headers });
}

export function apiError(status: number, code: string, message: string, extra?: Record<string, unknown>): Response {
  return json({ error: { code, message, ...extra } }, { status });
}

export function html(body: string, cacheSeconds = 3600): Response {
  return new Response(body, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": `public, max-age=300, s-maxage=${cacheSeconds}`,
      ...CORS_HEADERS,
    },
  });
}

/** Clamps a numeric query parameter, returning undefined when it is present but unusable. */
export function intParam(value: string | null, fallback: number, min: number, max: number): number | undefined {
  if (value === null || value === "") return fallback;
  if (!/^\d+$/.test(value.trim())) return undefined;
  const n = Number(value);
  if (n < min) return undefined;
  return Math.min(n, max);
}
