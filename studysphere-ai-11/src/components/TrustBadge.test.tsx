/**
 * TrustBadge — the learner-facing trust indicator (M2 P2.27).
 *
 * The property under test is not "a badge renders". It is that the frontend
 * NEVER classifies trust itself: every branch is driven by the backend's
 * `trust` object, and anything absent, malformed or ambiguous renders
 * nothing rather than implying verification the UI has no evidence for.
 *
 * Real fixtures, taken from the production database:
 *   q1436 Destination City  PUBLISHED / ORACLE_VERIFIED  -> verified
 *   q1    Two Sum           DRAFT / UNVERIFIED, servable -> practice mode
 *   q1947 Three Divisors    placeholder, not servable    -> nothing
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import TrustBadge, { TrustNote } from "./TrustBadge";

afterEach(cleanup);

/** q1436 — verified and adaptive-eligible. */
const VERIFIED = {
  status: "PUBLISHED",
  trust_state: "ORACLE_VERIFIED",
  adaptive_eligible: true,
  servable: true,
};

/** q1 Two Sum — the honest gap: served every day, never verified. */
const PRACTICE = {
  status: "DRAFT",
  trust_state: "UNVERIFIED",
  adaptive_eligible: false,
  servable: true,
};

/** q1947 — carries the placeholder marker, so not servable at all. */
const NOT_SERVABLE = {
  status: "DRAFT",
  trust_state: "UNVERIFIED",
  adaptive_eligible: false,
  servable: false,
};

describe("A — verified, adaptive-eligible question", () => {
  it("shows a verified badge and no warning", () => {
    render(<TrustBadge trust={VERIFIED} />);

    expect(screen.getByTestId("trust-badge-verified")).toBeTruthy();
    expect(screen.queryByTestId("trust-badge-practice")).toBeNull();
  });

  it("shows no practice-mode note", () => {
    render(<TrustNote trust={VERIFIED} />);
    expect(screen.queryByTestId("trust-note-practice")).toBeNull();
  });
});

describe("B — servable but unverified", () => {
  it("shows the practice-mode badge, not the verified one", () => {
    render(<TrustBadge trust={PRACTICE} />);

    expect(screen.getByTestId("trust-badge-practice")).toBeTruthy();
    expect(screen.queryByTestId("trust-badge-verified")).toBeNull();
  });

  it("explains that the result does not affect rating or progress", () => {
    render(<TrustNote trust={PRACTICE} />);

    const note = screen.getByTestId("trust-note-practice");
    expect(note.textContent).toMatch(/not been verified/i);
    expect(note.textContent).toMatch(/rating/i);
    expect(note.textContent).toMatch(/progress/i);
  });

  it("does not imply the question is broken, and says it is still solvable", () => {
    render(<TrustNote trust={PRACTICE} />);

    const text = screen.getByTestId("trust-note-practice").textContent ?? "";
    expect(text).toMatch(/still solve it/i);
    for (const alarming of [/broken/i, /error/i, /invalid/i, /do not attempt/i]) {
      expect(text).not.toMatch(alarming);
    }
  });
});

describe("C — not servable", () => {
  it("renders no badge, so nothing claims the question is available", () => {
    render(<TrustBadge trust={NOT_SERVABLE} />);

    expect(screen.queryByTestId("trust-badge-practice")).toBeNull();
    expect(screen.queryByTestId("trust-badge-verified")).toBeNull();
  });

  it("renders no note", () => {
    render(<TrustNote trust={NOT_SERVABLE} />);
    expect(screen.queryByTestId("trust-note-practice")).toBeNull();
  });
});

describe("D — missing or malformed trust object fails safe", () => {
  const broken: Array<[string, any]> = [
    ["undefined", undefined],
    ["null", null],
    ["empty object", {}],
    ["a string", "ORACLE_VERIFIED"],
    ["a number", 1],
    ["an array", []],
    ["adaptive_eligible as a truthy string", { adaptive_eligible: "true", servable: "true" }],
    ["only status present", { status: "PUBLISHED" }],
  ];

  for (const [label, value] of broken) {
    it(`does not crash and never claims verification for ${label}`, () => {
      render(
        <div>
          <TrustBadge trust={value} />
          <TrustNote trust={value} />
        </div>,
      );

      // The critical assertion: absence of evidence must never render as
      // evidence of trust.
      expect(screen.queryByTestId("trust-badge-verified")).toBeNull();
    });
  }

  it("treats a truthy STRING as not-eligible rather than coercing it", () => {
    // `"false"` is truthy in JS. Strict === comparison is what stops a
    // serialisation quirk from silently promoting a question to verified.
    render(<TrustBadge trust={{ adaptive_eligible: "false" as any, servable: true }} />);
    expect(screen.queryByTestId("trust-badge-verified")).toBeNull();
  });
});

describe("I — no grading information is rendered", () => {
  it("ignores any extra field the backend never promised", () => {
    const contaminated = {
      ...PRACTICE,
      expected_output: "[0,1]",
      hidden_test_cases: [{ stdin: "x", expected_output: "y" }],
      reference_source: "class Solution: ...",
      approved_by: "operator",
    } as any;

    const { container } = render(
      <div>
        <TrustBadge trust={contaminated} />
        <TrustNote trust={contaminated} />
      </div>,
    );

    const rendered = container.textContent ?? "";
    for (const secret of ["[0,1]", "stdin", "class Solution", "operator"]) {
      expect(rendered).not.toContain(secret);
    }
  });
});

describe("J — accessibility", () => {
  it("conveys state as text, not colour alone", () => {
    render(<TrustBadge trust={PRACTICE} />);
    expect(screen.getByTestId("trust-badge-practice").textContent)
      .toMatch(/practice mode/i);
  });

  it("gives the verified badge a meaningful accessible name", () => {
    render(<TrustBadge trust={VERIFIED} />);

    const badge = screen.getByTestId("trust-badge-verified");
    expect(badge.getAttribute("role")).toBe("status");
    expect(badge.getAttribute("aria-label")).toMatch(/verified/i);
  });

  it("gives the practice badge an accessible name that explains the consequence", () => {
    render(<TrustBadge trust={PRACTICE} />);

    const badge = screen.getByTestId("trust-badge-practice");
    expect(badge.getAttribute("role")).toBe("status");
    const label = badge.getAttribute("aria-label") ?? "";
    expect(label).toMatch(/not yet verified/i);
    expect(label).toMatch(/rating/i);
  });

  it("hides the decorative icon from assistive technology", () => {
    const { container } = render(<TrustBadge trust={PRACTICE} />);
    const icon = container.querySelector("svg");
    expect(icon?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("E/F — the same object from either recommendation path", () => {
  it("classifies identically regardless of which endpoint produced it", () => {
    // Both paths build this with Question.trust_summary(); the component has
    // no notion of which one answered, and must not acquire one.
    const legacy = { ...VERIFIED };
    const agent = { ...VERIFIED };

    const { container: a } = render(<TrustBadge trust={legacy} />);
    const legacyHtml = a.innerHTML;
    cleanup();
    const { container: b } = render(<TrustBadge trust={agent} />);

    expect(b.innerHTML).toBe(legacyHtml);
  });
});
