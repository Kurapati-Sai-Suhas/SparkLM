/**
 * Editor template resolution — the first frontend tests in this repo
 * (M4 Phase B).
 *
 * This module decides what a student sees when the editor opens. Milestone 2
 * shipped a defect here: students were served a blank editor and expected to
 * reconstruct a Solution class from nothing, because the question had no
 * template for their chosen language and nothing fell back. It was fixed by
 * hand and had no automated protection until now.
 *
 * The cases below are written against that failure and the ones adjacent to
 * it, not against the implementation.
 */

import { describe, expect, it } from "vitest";
import {
  BOILERPLATE_KEYS,
  CANONICAL_LANGUAGES,
  EMPTY_STUB,
  FALLBACK_STUB,
  LANGUAGE_REGISTRY,
  SELF_CONTAINED_LANGUAGES,
  availableLanguages,
  editorContentFor,
  templateFor,
} from "./editorTemplates";

const PY = "class Solution:\n    def solve(self): pass\n";
const JS = "var solve = function() {};\n";

describe("templateFor", () => {
  it("returns the stored template for a language", () => {
    expect(templateFor({ python: PY }, "python")).toBe(PY);
  });

  it("resolves js against the javascript key", () => {
    // The selector says "js"; questions store the template under
    // "javascript". This mismatch gave every JavaScript user an empty editor
    // while submissions still worked, which is why it stayed invisible.
    expect(templateFor({ javascript: JS }, "js")).toBe(JS);
  });

  it("resolves js against the js key too", () => {
    expect(templateFor({ js: JS }, "js")).toBe(JS);
  });

  it("returns null when the language has no template", () => {
    expect(templateFor({ python: PY }, "java")).toBeNull();
  });

  it("treats a blank or whitespace-only template as absent", () => {
    // A stray "" in seed data must not open an empty editor — that is
    // indistinguishable from the M2 bug from the student's side.
    expect(templateFor({ java: "" }, "java")).toBeNull();
    expect(templateFor({ java: "   \n  " }, "java")).toBeNull();
  });

  it("survives malformed boilerplate without throwing", () => {
    // Seed data is inconsistent across generations; a non-object here must
    // degrade to "no template", not crash the editor.
    for (const bad of [null, undefined, "a string", 42, []]) {
      expect(templateFor(bad, "python")).toBeNull();
    }
  });

  it("ignores non-string template values", () => {
    expect(templateFor({ python: { code: PY } }, "python")).toBeNull();
  });
});

describe("availableLanguages", () => {
  it("lists only languages with a usable template", () => {
    expect(availableLanguages({ python: PY, java: "" })).toEqual(["python"]);
  });

  it("returns an empty list when nothing is available", () => {
    expect(availableLanguages({})).toEqual([]);
    expect(availableLanguages(null)).toEqual([]);
  });

  it("counts a javascript-keyed template as js being available", () => {
    expect(availableLanguages({ javascript: JS })).toContain("js");
  });
});

describe("editorContentFor — the M2 regression", () => {
  it("never returns empty, whatever the input", () => {
    // The single most important assertion in this file.
    for (const lang of CANONICAL_LANGUAGES) {
      for (const boilerplate of [null, undefined, {}, { python: "" }, "junk"]) {
        expect(editorContentFor(boilerplate, lang).trim().length).toBeGreaterThan(0);
      }
    }
  });

  it("prefers the stored template over the stub", () => {
    expect(editorContentFor({ python: PY }, "python")).toBe(PY);
  });

  it("falls back to the language stub when there is no template", () => {
    expect(editorContentFor({}, "python")).toBe(EMPTY_STUB.python);
  });

  it("falls back to the generic stub for an unknown language", () => {
    expect(editorContentFor({}, "rust")).toBe(FALLBACK_STUB);
  });
});

describe("self-contained languages", () => {
  it("gives C and C++ a compilable skeleton, not a bare comment", () => {
    // C/C++ have no generic server-side wrapper, so the student's code is
    // compiled exactly as written. A comment-only stub cannot build, so
    // "no template" would still leave them stuck.
    for (const lang of ["c", "cpp"]) {
      expect(SELF_CONTAINED_LANGUAGES.has(lang)).toBe(true);
      expect(EMPTY_STUB[lang]).toContain("main(");
      expect(EMPTY_STUB[lang]).toContain("#include");
    }
  });

  it("gives wrapped languages a method-body stub without main()", () => {
    for (const lang of ["python", "java", "js"]) {
      expect(SELF_CONTAINED_LANGUAGES.has(lang)).toBe(false);
      expect(EMPTY_STUB[lang]).not.toContain("main(");
    }
  });
});

describe("language set is generated, not duplicated", () => {
  it("every language from the backend has a stub", () => {
    // The set now comes from languages.generated.json, so drift in the SET
    // is impossible. What can still go wrong is a language arriving from the
    // backend with no editor content defined for it — this catches that.
    for (const lang of CANONICAL_LANGUAGES) {
      expect(BOILERPLATE_KEYS[lang], `${lang} missing from BOILERPLATE_KEYS`).toBeDefined();
      expect(EMPTY_STUB[lang], `${lang} missing from EMPTY_STUB`).toBeDefined();
    }
  });

  it("carries the fields the UI needs for every language", () => {
    for (const lang of LANGUAGE_REGISTRY) {
      expect(lang.key).toBeTruthy();
      expect(lang.label).toBeTruthy();
      expect(lang.extension).toBeTruthy();
      expect(Array.isArray(lang.spellings)).toBe(true);
      expect(lang.spellings.length).toBeGreaterThan(0);
    }
  });

  it("keeps the js/javascript spelling split the backend declares", () => {
    // The UI key is "js"; the backend canonical is "javascript". This is the
    // mismatch that gave every JavaScript user an empty editor, and it is now
    // declared once (Language.ui_key) instead of assumed on both sides.
    const js = LANGUAGE_REGISTRY.find((l) => l.key === "js");
    expect(js).toBeDefined();
    expect(js!.backendKey).toBe("javascript");
    expect(js!.spellings).toContain("javascript");
  });

  it("self-contained flags come from the backend, not a local guess", () => {
    const selfContained = LANGUAGE_REGISTRY.filter((l) => l.selfContained).map((l) => l.key);
    expect(selfContained.sort()).toEqual(["c", "cpp"]);
  });
});
