import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import fs from "fs";
import path from "path";
import { componentTagger } from "lovable-tagger";

/**
 * Serve Monaco from our own origin instead of a CDN (M4 Phase A, CSP).
 *
 * `@monaco-editor/react` defaults to loading the editor from
 * `cdn.jsdelivr.net/npm/monaco-editor@<version>/min/vs`. Allowlisting that in
 * `script-src` would mean a jsDelivr compromise is arbitrary JavaScript
 * execution inside SparkLM — which, with tokens in localStorage, is full
 * account takeover. A third-party origin in `script-src` is categorically
 * worse than one in `font-src`.
 *
 * `monaco-editor` is ALREADY a direct dependency at the exact version the CDN
 * was serving, so the asset was being paid for in the lockfile and fetched
 * from a third party anyway. This copies it into `public/` so Vite serves it
 * in dev and emits it to `dist/` on build; `src/main.tsx` points the loader at
 * `/monaco/vs`. No new dependency, no bundler surgery.
 *
 * `public/monaco/` is gitignored — it is a build artifact, not source. The
 * version stamp makes the copy idempotent so it does not re-copy 16 MB on
 * every dev-server start, and DOES re-copy when monaco-editor is upgraded.
 */
function selfHostMonaco() {
  return {
    name: "sparklm-self-host-monaco",
    buildStart() {
      const pkg = path.resolve(__dirname, "node_modules/monaco-editor/package.json");
      if (!fs.existsSync(pkg)) {
        throw new Error(
          "monaco-editor is not installed. The editor is self-hosted to keep " +
          "cdn.jsdelivr.net out of script-src — run `npm install` before building."
        );
      }
      const version = JSON.parse(fs.readFileSync(pkg, "utf8")).version;
      const src = path.resolve(__dirname, "node_modules/monaco-editor/min/vs");
      const dest = path.resolve(__dirname, "public/monaco/vs");
      const stamp = path.resolve(__dirname, "public/monaco/.version");

      if (fs.existsSync(stamp) && fs.readFileSync(stamp, "utf8") === version) {
        return;
      }
      fs.rmSync(path.dirname(dest), { recursive: true, force: true });
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.cpSync(src, dest, { recursive: true });
      fs.writeFileSync(stamp, version);
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: 8080,
  },
  plugins: [
    react(),
    selfHostMonaco(),
    mode === "development" && componentTagger(),
  ].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
