/**
 * Architectural guard: how the SPA is allowed to reach the API
 * (N3 Phase 1a, Task 5).
 *
 * Three pages — /schedule, /notifications, /settings — were broken in
 * production for months and nothing reported it. Each hand-rolled a `fetch`
 * to a RELATIVE path like "/api/schedule/". On Vercel that resolves against
 * the SPA's own origin, where the catch-all rewrite in vercel.json serves
 * index.html with HTTP 200 — so the pages parsed the app's own HTML as JSON,
 * threw, and swallowed it. No test could see it: the failure is a property
 * of the deployed origin, not of any function.
 *
 * Phase 1a migrated those three onto the shared axios client. This file
 * stops the class from returning, by asserting invariants over the source
 * itself rather than over any single component.
 *
 *   1. NO relative "/api" URL is ever passed to fetch — repository-wide,
 *      permanently. This is the actual defect, and it is closed everywhere
 *      today: the calls Phase 1b has yet to migrate all use VITE_API_URL.
 *
 *   2. DIRECT TRANSPORT — raw `fetch(`, a direct `axios` import, or
 *      `axios.create()` — appears ONLY in files on an explicit allowlist.
 *      The allowlist is what Phase 1b deletes, entry by entry. It can only
 *      shrink: a file using neither transport must be removed from it, and
 *      an already-migrated page may never be added back.
 *
 * Checking BOTH transports is the correction the Phase 1a audit forced. The
 * original guard looked for `fetch(` alone, so CodingPortal — a routed page
 * (/code) issuing three calls through a directly imported axios — passed
 * while doing precisely what this file exists to forbid. A guard blind to
 * half the transports is worse than none: it certifies what it cannot see.
 *
 * Why an allowlist rather than a blanket ban: seven fetch calls and three
 * axios calls remain in components Phase 1b owns. A rule that cannot pass
 * today is not a guard, it is a broken build — and it would be disabled
 * within the week.
 *
 * Deliberately NOT asserted: that getAccessToken() is unused outside the
 * client. The WebSocket consumers (GroupChat, useGroupChat,
 * LiveCollaborativeWorkspace) and ProtectedRoute legitimately need the raw
 * token — axios cannot open a WebSocket. A rule forbidding it would be
 * wrong, not merely inconvenient.
 */

import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const SRC = join(__dirname, "..");
const CLIENT = join("services", "api.js");

/**
 * Files still permitted to reach the API directly — Phase 1b's remaining
 * work. DELETE an entry when its file is migrated. Never add one.
 *
 * CodingPortal was added by the Phase 1a audit. It was missing because the
 * original guard only looked for `fetch(`, and CodingPortal uses a directly
 * imported axios — a whole transport the guard could not see. It is a routed
 * page (/code) making three API calls outside the shared instance, so
 * Phase 1b would have missed it entirely.
 */
const PHASE_1B_ALLOWLIST = [
  join("components", "CodingOnboardingModal.tsx"),
  join("components", "LearningPathVisualizer.tsx"),
  join("components", "ReviewQueueCard.tsx"),
  join("pages", "AdaptiveCodingPortal.tsx"),
  join("pages", "CodingHub.tsx"),
];

/** Pages migrated in Phase 1a — these may never reappear in the allowlist. */
const PHASE_1A_MIGRATED = [
  join("pages", "Schedule.tsx"),
  join("pages", "Notifications.tsx"),
  join("pages", "Settings.tsx"),
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(tsx?|jsx?)$/.test(entry) && !/\.test\./.test(entry) ? [full] : [];
  });
}

/**
 * Prepares source for scanning.
 *
 * 1. Drops comment-only lines. Without this, DOCUMENTING the rule breaks the
 *    build: a comment reading `never write fetch("/api/x")` trips the very
 *    check it describes. Verified — it did.
 *
 *    Only whole-line comments are removed, never a trailing `// ...`. A
 *    blanket strip from `//` to end-of-line would also cut through
 *    "http://localhost:8000" inside a string literal and could hide a real
 *    violation later on that line. A false negative in a guard is far worse
 *    than a false positive, so this errs toward scanning too much.
 *
 * 2. Collapses whitespace after `fetch(`, so a call whose URL sits on the
 *    next line is still inspected. ReviewQueueCard is written that way, and
 *    a single-line regex skips it silently.
 */
function normalise(text: string): string {
  const withoutCommentLines = text
    .split(/\r?\n/)
    .filter((line) => {
      const t = line.trimStart();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*");
    })
    .join("\n");

  return withoutCommentLines.replace(/fetch\(\s*/g, "fetch(");
}

/**
 * True if the file pulls in axios by any route.
 *
 * Matches on the MODULE SPECIFIER, never on the local identifier — that is
 * what makes renaming useless as an evasion. `import a from "axios"`,
 * `import * as http from "axios"` and `const c = require("axios")` are all
 * the same string `"axios"` at the end.
 *
 * Multi-line imports need no special handling: `\s*` and `[^;]*?` both span
 * newlines. An earlier version collapsed all whitespace first; mutation
 * testing showed removing that changed no result, so it is gone.
 *
 * `import type` is deliberately allowed: a type-only import is erased at
 * build time and cannot issue a request. Blocking it would be theatre, and
 * would push people toward `any`.
 */
function importsAxios(text: string): boolean {
  const flat = normalise(text);
  const specifier = /(?:from|require\(|import\()\s*["']axios["']/;

  if (!specifier.test(flat)) return false;

  // Allow a purely type-level import; anything else counts.
  const typeOnly = /import\s+type\s+[^;]*?from\s*["']axios["']/g;
  const withoutTypeImports = flat.replace(typeOnly, "");
  return specifier.test(withoutTypeImports);
}

/** Raw `fetch(` anywhere in the file. */
function usesRawFetch(text: string): boolean {
  return normalise(text).includes("fetch(");
}

/** A `fetch` whose URL is a RELATIVE /api path — the production defect. */
function fetchesRelativeApi(text: string): boolean {
  const scanned = normalise(text);
  return ['"', "'", "`"].some((q) => scanned.includes(`fetch(${q}/api`));
}

/**
 * A second axios instance, built outside the shared client.
 *
 * Whitespace is permitted around the dot and before the paren. A plain
 * substring test for "axios.create(" misses
 *
 *     const client = axios
 *       .create({ baseURL });
 *
 * which is not an evasion — it is what Prettier produces for a long chain.
 * Found by the fixture below, not by review.
 */
function createsAxiosInstance(text: string): boolean {
  return /axios\s*\.\s*create\s*\(/.test(normalise(text));
}

const FILES = sourceFiles(SRC).filter((f) => !f.endsWith(CLIENT));
const rel = (f: string) => relative(SRC, f);

/**
 * Proves the detectors above can actually SEE a violation.
 *
 * Every other assertion in this file has the shape
 * `expect(offenders).toEqual([])`. That shape cannot fail for the reason it
 * claims: a working detector finding nothing and a BROKEN detector finding
 * nothing produce the identical empty list. Verified by mutation — disabling
 * the relative-/api check, the fetch-confinement check and the axios.create
 * check each left all ten tests green.
 *
 * So the repo-wide scans validate the repository, and these fixtures validate
 * the scanner. Neither is sufficient alone. Each case below was first
 * confirmed by injecting the real thing into a real page and watching the
 * suite go red; the fixtures make that permanent instead of a one-off.
 */
describe("network access: the detectors detect", () => {
  it.each([
    ['import axios from "axios";', "default import"],
    ["import axios from 'axios';", "single quotes"],
    ['import http from "axios";', "renamed binding"],
    ['import * as http from "axios";', "namespace import"],
    ['import axios, { AxiosError } from "axios";', "mixed import"],
    ['import axios\n  from "axios";', "multi-line import"],
    ['const axios = require("axios");', "require"],
    ['const axios = await import("axios");', "dynamic import"],
  ])("catches %s (%s)", (source) => {
    expect(importsAxios(source)).toBe(true);
  });

  it.each([
    ['import type { AxiosError } from "axios";', "type-only import"],
    ['// import axios from "axios"; -- never do this', "line comment"],
    ['/* import axios from "axios"; */', "block comment"],
    ['import { get } from "axios-retry";', "another module named axios-*"],
    ["const axiosStyle = { get() {} };", "identifier resembling axios"],
  ])("allows %s (%s)", (source) => {
    expect(importsAxios(source)).toBe(false);
  });

  it("catches a second axios instance", () => {
    expect(createsAxiosInstance('axios.create({ baseURL: "/" })')).toBe(true);
    expect(createsAxiosInstance("axios\n  .create({})")).toBe(true);
    expect(createsAxiosInstance("userAPI.getProfile()")).toBe(false);
  });

  it("catches a relative /api fetch in every quote style", () => {
    expect(fetchesRelativeApi('fetch("/api/x")')).toBe(true);
    expect(fetchesRelativeApi("fetch('/api/x')")).toBe(true);
    expect(fetchesRelativeApi("fetch(`/api/x`)")).toBe(true);
    // The line break ReviewQueueCard actually uses.
    expect(fetchesRelativeApi('fetch(\n  "/api/x")')).toBe(true);
    // Absolute URLs are what the migrated code does — must stay allowed.
    expect(fetchesRelativeApi('fetch(`${import.meta.env.VITE_API_URL}/api/x`)')).toBe(false);
  });

  it("catches raw fetch, but not the word inside an identifier", () => {
    expect(usesRawFetch('fetch("/x")')).toBe(true);
    expect(usesRawFetch("const refetchData = () => {};")).toBe(false);
  });
});

describe("network access: no relative /api URLs", () => {
  it("scans a non-trivial number of files", () => {
    // Guards the guard: a broken walker finds nothing and passes everything.
    expect(FILES.length).toBeGreaterThan(20);
  });

  it("no fetch call targets a relative /api path, anywhere", () => {
    // The exact production defect. A relative path resolves against the SPA
    // origin and is served index.html with HTTP 200 — a silent failure that
    // looks like an empty page. Enforced repo-wide, including files Phase 1b
    // has yet to migrate.
    const offenders = FILES.filter((f) =>
      fetchesRelativeApi(readFileSync(f, "utf8"))
    ).map(rel);

    expect(offenders).toEqual([]);
  });
});

describe("network access: raw fetch is confined to the allowlist", () => {
  const filesWithFetch = FILES.filter((f) =>
    usesRawFetch(readFileSync(f, "utf8"))
  ).map(rel);

  it("only allowlisted files call fetch directly", () => {
    const unexpected = filesWithFetch.filter(
      (f) => !PHASE_1B_ALLOWLIST.includes(f)
    );

    expect(unexpected).toEqual([]);
  });

  it("only allowlisted files import axios directly", () => {
    // The gap the Phase 1a audit found. The guard checked `fetch(` only, so
    // CodingPortal — a routed page making three API calls through a directly
    // imported axios — passed while doing exactly what this file forbids.
    const unexpected = FILES.filter((f) => importsAxios(readFileSync(f, "utf8")))
      .map(rel)
      .filter((f) => !PHASE_1B_ALLOWLIST.includes(f));

    expect(unexpected).toEqual([]);
  });

  it("nothing outside the client creates its own axios instance", () => {
    // axios.create() produces a second client with none of the shared
    // interceptors — no refresh, no CSRF sentinel, no baseURL. It would look
    // like centralised code while being anything but.
    const offenders = FILES.filter((f) =>
      createsAxiosInstance(readFileSync(f, "utf8"))
    ).map(rel);

    expect(offenders).toEqual([]);
  });

  it("the allowlist has no stale entries", () => {
    // Forces the list to SHRINK as Phase 1b lands. Without this, a migrated
    // file would linger and quietly re-permit direct transport later.
    // Checks BOTH transports: an entry earns its place by still using one.
    const stillDirect = FILES.filter((f) => {
      const text = readFileSync(f, "utf8");
      return usesRawFetch(text) || importsAxios(text);
    }).map(rel);

    const stale = PHASE_1B_ALLOWLIST.filter((f) => !stillDirect.includes(f));

    expect(stale).toEqual([]);
  });

  it("no Phase 1a page is in the allowlist", () => {
    const regressed = PHASE_1A_MIGRATED.filter((f) =>
      PHASE_1B_ALLOWLIST.includes(f)
    );

    expect(regressed).toEqual([]);
  });

  it("the Phase 1a pages use NO direct transport at all", () => {
    // The outcome of Tasks 2-4, asserted directly rather than inferred from
    // the allowlist being correct. Covers both transports, so a migrated
    // page cannot regress from fetch to raw axios and still pass.
    const regressed = PHASE_1A_MIGRATED.filter((f) => {
      const text = readFileSync(join(SRC, f), "utf8");
      return usesRawFetch(text) || importsAxios(text);
    });

    expect(regressed).toEqual([]);
  });
});

describe("network access: the shared client is the only transport", () => {
  it("every Phase 1a page imports the shared client", () => {
    const missing = PHASE_1A_MIGRATED.filter((f) => {
      const text = readFileSync(join(SRC, f.split(sep).join(sep)), "utf8");
      return !text.includes("@/services/api");
    });

    expect(missing).toEqual([]);
  });

  it("no Phase 1a page builds an Authorization header by hand", () => {
    // Duplicated auth logic is what let `Bearer null` ship: the pages read a
    // localStorage key nothing ever wrote. The client owns the header now.
    const offenders = PHASE_1A_MIGRATED.filter((f) => {
      const text = readFileSync(join(SRC, f), "utf8");
      return text.includes("Authorization");
    });

    expect(offenders).toEqual([]);
  });
});
