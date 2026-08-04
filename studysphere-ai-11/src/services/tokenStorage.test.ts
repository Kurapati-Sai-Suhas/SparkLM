/**
 * Static guard: no access token may be read from or written to localStorage
 * outside the shared auth layer (M5 Phase 4 follow-up).
 *
 * The migration replaced 21 direct localStorage token reads across 13
 * components with getAccessToken(). Nothing in the type system stops the
 * next component from reintroducing one, and a single reintroduced read
 * puts a script-readable credential back on the page — the exact property
 * this phase removed.
 *
 * Scanning source is the only way to assert "nowhere does X" rather than
 * "this one file does not do X". It is the frontend equivalent of the
 * backend authorization matrix's drift guards.
 *
 * The last assertion exists because of a defect this migration nearly
 * shipped: three inserted imports landed inside multi-line import blocks
 * and one inside a template literal, leaving getAccessToken undefined at
 * runtime. `tsc --noEmit` passed over all of it, because the ROOT tsconfig
 * has "files": [] and compiles nothing at all.
 */
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..");
const AUTH_LAYER = join("services", "api.js");
const TOKEN_KEYS = ["authToken", "access_token", "access", "token"];
const QUOTES = ["'", '"', "`"];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(tsx?|jsx?)$/.test(entry) && !/\.test\./.test(entry) ? [full] : [];
  });
}

function offendersFor(files: string[], call: string): string[] {
  const found: string[] = [];
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    for (const key of TOKEN_KEYS) {
      for (const q of QUOTES) {
        if (text.includes(`localStorage.${call}(${q}${key}${q}`)) {
          found.push(`${file.replace(SRC, "src")} -> ${key}`);
        }
      }
    }
  }
  return found;
}

describe("access token storage", () => {
  const files = sourceFiles(SRC).filter((f) => !f.endsWith(AUTH_LAYER));

  it("scans a non-trivial number of files", () => {
    // Guards the guard: a broken walker that finds nothing would pass every
    // assertion below while checking nothing at all.
    expect(files.length).toBeGreaterThan(20);
  });

  it("no component reads an access token from localStorage", () => {
    expect(offendersFor(files, "getItem")).toEqual([]);
  });

  it("no component writes an access token to localStorage", () => {
    expect(offendersFor(files, "setItem")).toEqual([]);
  });

  it("the auth layer itself never persists the access token", () => {
    const api = readFileSync(join(SRC, AUTH_LAYER), "utf8");
    expect(api.includes("localStorage.setItem('authToken'")).toBe(false);
    expect(api.includes("localStorage.getItem('authToken'")).toBe(false);
  });

  it("every file calling getAccessToken imports it at module scope", () => {
    const broken: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      if (!text.includes("getAccessToken(")) continue;
      const importsIt = text
        .split("\n")
        .some(
          (line) =>
            line.trimStart().startsWith("import") &&
            line.includes("getAccessToken")
        );
      if (!importsIt) broken.push(file.replace(SRC, "src"));
    }
    expect(broken).toEqual([]);
  });
});
