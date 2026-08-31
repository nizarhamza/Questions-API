declare module "cloudflare:test" {
  // The pool builds this from wrangler.jsonc; the tests only use SELF.
  export const SELF: { fetch(input: string | Request, init?: RequestInit): Promise<Response> };
}
