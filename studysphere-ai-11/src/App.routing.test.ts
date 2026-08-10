/**
 * Routing guard for retired surfaces (M1/P1.2-C).
 *
 * A SOURCE assertion, not a render test, and deliberately so: App.tsx mounts
 * its own <BrowserRouter>, so a test cannot inject a MemoryRouter to drive
 * navigation, and rendering the real tree would pull in the OAuth provider,
 * the query client, the auth bootstrap and every lazy page — a great deal of
 * mocking to assert one line of wiring.
 *
 * What this proves: the route table says what we think it says. What it does
 * NOT prove: that react-router honours it at runtime. That is covered by the
 * production verification step for this phase, and by react-router's own
 * tests for <Navigate>.
 *
 * Same technique as services/networkAccess.test.ts, which guards the transport
 * rules by reading source for the same reason.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const APP = readFileSync(join(__dirname, "App.tsx"), "utf8");
const SIDEBAR = readFileSync(
  join(__dirname, "components", "layout", "AppSidebar.tsx"),
  "utf8"
);

/** Strips whole-line comments so documenting a retired route cannot trip a check. */
function code(text: string): string {
  return text
    .split(/\r?\n/)
    .filter((line) => {
      const t = line.trimStart();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*") && !t.startsWith("{/*");
    })
    .join("\n");
}

describe("retired surfaces stay retired", () => {
  const app = code(APP);

  it("/code redirects to /coding-portal rather than 404ing", () => {
    // The route was linked from nowhere in-app, so only bookmarks reach it —
    // which is exactly why it redirects instead of being deleted outright.
    const route = app.match(/<Route\s+path="\/code"[^>]*>/);
    expect(route, "the /code route disappeared entirely").toBeTruthy();
    expect(route![0]).toContain("Navigate");
    expect(route![0]).toContain("/coding-portal");
    expect(route![0]).toContain("replace");
  });

  it("the legacy CodingPortal component is no longer imported", () => {
    // The frontend component. NOT groups.models.CodingPortal, which is a
    // production Django model with a serializer, an admin registration and
    // ~20 test files, and which this phase must not touch.
    expect(app).not.toMatch(/components\/CodingPortal/);
  });

  it("/coding-portal survives as the single coding surface", () => {
    expect(app).toMatch(/<Route\s+path="\/coding-portal"/);
    expect(app).toMatch(/AdaptiveCodingPortal/);
  });

  it("the Flashcards route and page import are gone", () => {
    expect(app).not.toMatch(/path="\/flashcards"/);
    expect(app).not.toMatch(/AIFlashcards/);
  });

  it("no navigation entry points at a retired surface", () => {
    const nav = code(SIDEBAR);
    expect(nav).not.toMatch(/url:\s*"\/flashcards"/);
    expect(nav).not.toMatch(/url:\s*"\/code"/);
  });

  it("navigation is 10 entries after retiring Flashcards", () => {
    // The roadmap predicted 11 -> 9. It was wrong: /code was never a nav
    // entry, so Flashcards is the only removal this phase makes.
    // The quote matters: `{ title: "Dashboard"` is an entry, whereas
    // `(item: { title: string; url: string; ... })` is renderNavItem's type
    // annotation. A looser pattern counted the annotation as an eleventh item.
    const entries = code(SIDEBAR).match(/\{\s*title:\s*"/g) ?? [];
    expect(entries).toHaveLength(10);
  });
});
