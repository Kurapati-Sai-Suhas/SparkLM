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
 * stops the class from returning, by asserting two invariants over the
 * source itself rather than over any single component.
 *
 *   1. NO relative "/api" URL is ever passed to fetch — repository-wide,
 *      permanently. This is the actual defect, and it is closed everywhere
 *      today: the calls Phase 1b has yet to migrate all use VITE_API_URL.
 *
 *   2. Raw `fetch(` appears ONLY in files on an explicit allowlist. The
 *      allowlist is what Phase 1b deletes, entry by entry. It can only
 *      shrink: a file that no longer contains a fetch must be removed from
 *      it, and an already-migrated page may never be added back.
 *
 * Why an allowlist rather than a blanket ban: seven calls remain in
 * components Phase 1b owns. A rule that cannot pass today is not a guard,
 * it is a broken build — and it would be disabled within the week.
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
 * Files still permitted to call fetch directly — Phase 1b's remaining work.
 * DELETE an entry when its file is migrated. Never add one.
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

const FILES = sourceFiles(SRC).filter((f) => !f.endsWith(CLIENT));
const rel = (f: string) => relative(SRC, f);

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
    const offenders: string[] = [];

    for (const file of FILES) {
      const text = normalise(readFileSync(file, "utf8"));
      for (const q of ['"', "'", "`"]) {
        if (text.includes(`fetch(${q}/api`)) {
          offenders.push(`${rel(file)} -> fetch(${q}/api...`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});

describe("network access: raw fetch is confined to the allowlist", () => {
  const filesWithFetch = FILES.filter((f) =>
    normalise(readFileSync(f, "utf8")).includes("fetch(")
  ).map(rel);

  it("only allowlisted files call fetch directly", () => {
    const unexpected = filesWithFetch.filter(
      (f) => !PHASE_1B_ALLOWLIST.includes(f)
    );

    expect(unexpected).toEqual([]);
  });

  it("the allowlist has no stale entries", () => {
    // Forces the list to SHRINK as Phase 1b lands. Without this, a migrated
    // file would linger and quietly re-permit a fetch later.
    const stale = PHASE_1B_ALLOWLIST.filter((f) => !filesWithFetch.includes(f));

    expect(stale).toEqual([]);
  });

  it("no Phase 1a page is in the allowlist", () => {
    const regressed = PHASE_1A_MIGRATED.filter((f) =>
      PHASE_1B_ALLOWLIST.includes(f)
    );

    expect(regressed).toEqual([]);
  });

  it("the Phase 1a pages contain no fetch at all", () => {
    // The outcome of Tasks 2-4, asserted directly rather than inferred from
    // the allowlist being correct.
    const regressed = PHASE_1A_MIGRATED.filter((f) =>
      filesWithFetch.includes(f)
    );

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
