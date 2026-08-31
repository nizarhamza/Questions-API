import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    // Tests run inside workerd against the real bindings from wrangler.jsonc:
    // a real Durable Object for session tokens, the real bundled bank.
    cloudflareTest({
      singleWorker: true,
      wrangler: { configPath: "./wrangler.jsonc" },
    }),
  ],
});
