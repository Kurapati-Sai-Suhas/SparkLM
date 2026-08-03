import { defineConfig } from "vitest/config";
import path from "path";

/**
 * Vitest config, separate from vite.config.ts (M4 Phase B).
 *
 * Kept separate deliberately: vite.config.ts carries the Monaco self-hosting
 * plugin, which copies 16 MB out of node_modules on buildStart. Tests do not
 * need it, and paying that on every test run would make the suite slow enough
 * that people stop running it.
 *
 * `jsdom` rather than `node` because the extracted module is imported by
 * React components; the environment costs little and keeps the door open for
 * component tests without another config change.
 */
export default defineConfig({
  resolve: {
    // Mirrors vite.config.ts so the `@/` imports used across src/ resolve
    // identically under test.
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
