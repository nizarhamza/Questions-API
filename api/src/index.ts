import { apiError, json, preflight } from "./http";
import { docsPage } from "./docs";
import { RESPONSE_CODES } from "./routes/opentdb";
import * as opentdb from "./routes/opentdb";
import * as v1 from "./routes/native";
import { TOTAL, GENERATED } from "./bank";
import type { Env } from "./env";

export { SessionToken } from "./session";

/** Token minting gets its own, much tighter budget: it is the only endpoint that
 *  allocates durable storage, so it is the only one worth abusing. */
async function rateLimited(request: Request, env: Env, path: string): Promise<boolean> {
  const key = request.headers.get("cf-connecting-ip") ?? "anonymous";
  const mintsToken =
    path === "/v1/tokens" ||
    (path === "/api_token.php" && new URL(request.url).searchParams.get("command") === "request");

  const limiter = mintsToken ? env.RL_TOKENS : env.RL_GENERAL;
  // No binding (local dev, or a plan without it) means no limiting — the API stays up
  // rather than failing closed on a missing convenience.
  if (!limiter) return false;

  const { success } = await limiter.limit({ key: `${mintsToken ? "t" : "g"}:${key}` });
  return !success;
}

async function route(request: Request, env: Env, url: URL): Promise<Response> {
  const path = url.pathname.replace(/\/+$/, "") || "/";

  switch (path) {
    case "/":
      return docsPage(url);
    case "/health":
      return json({ status: "ok", questions: TOTAL, bank_generated: GENERATED });

    // ---- OpenTDB-compatible ----
    case "/api.php":
      return opentdb.handleQuestions(url, env);
    case "/api_token.php":
      return opentdb.handleToken(url, env);
    case "/api_category.php":
      return opentdb.handleCategoryList();
    case "/api_count.php":
      return opentdb.handleCategoryCount(url);
    case "/api_count_global.php":
      return opentdb.handleGlobalCount();

    // ---- native ----
    case "/v1/questions":
      return v1.handleQuestions(url, env);
    case "/v1/categories":
      return v1.handleCategories();
    case "/v1/patterns":
      return v1.handlePatterns();
    case "/v1/stats":
      return v1.handleStats();
    case "/v1/check":
      if (request.method !== "POST") return apiError(405, "method_not_allowed", "POST a JSON body to /v1/check.");
      return v1.handleCheck(request, env);
    case "/v1/tokens":
      if (request.method !== "POST") return apiError(405, "method_not_allowed", "POST /v1/tokens to mint a session token.");
      return v1.handleTokenCreate(env);
  }

  const byId = path.match(/^\/v1\/questions\/([A-Za-z0-9_-]+)$/);
  if (byId) return v1.handleQuestionById(byId[1]!);

  const tokenReset = path.match(/^\/v1\/tokens\/([0-9a-f]{40})\/reset$/);
  if (tokenReset) {
    if (request.method !== "POST") return apiError(405, "method_not_allowed", "POST to reset a token.");
    return v1.handleTokenReset(tokenReset[1]!, env);
  }

  const tokenGet = path.match(/^\/v1\/tokens\/([0-9a-f]{40})$/);
  if (tokenGet) return v1.handleTokenInfo(tokenGet[1]!, env);

  return apiError(404, "not_found", `No route for ${request.method} ${path}. See ${url.origin}/ for the endpoint list.`);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") return preflight();

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method !== "GET" && request.method !== "POST") {
      return apiError(405, "method_not_allowed", "This API speaks GET and POST.");
    }

    if (await rateLimited(request, env, path)) {
      // OpenTDB clients read response_code, not HTTP status, so the compat surface has
      // to answer in its own vocabulary even while being throttled.
      if (path.startsWith("/api")) {
        return json({ response_code: RESPONSE_CODES.RATE_LIMIT, results: [] }, { status: 429 });
      }
      return apiError(429, "rate_limited", "Too many requests. Slow down and try again shortly.");
    }

    try {
      return await route(request, env, url);
    } catch (error) {
      console.error("unhandled", error);
      return apiError(500, "internal_error", "Something went wrong handling that request.");
    }
  },
} satisfies ExportedHandler<Env>;
