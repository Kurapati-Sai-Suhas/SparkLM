/**
 * editorTemplates — starter-template resolution for the code editor.
 *
 * Extracted verbatim from AdaptiveCodingPortal.tsx in M4 Phase B. This is a
 * pure move: the logic is unchanged, only its location. It lived inside a
 * 900-line component and was therefore untestable, despite deciding *what a
 * student sees when the editor opens* — the exact surface where Milestone 2's
 * empty-editor defect lived.
 *
 * The language SET is no longer duplicated. It is generated from
 * `common/languages.py` into `languages.generated.json` by
 * `manage.py export_languages`, and CI fails if the committed artifact drifts
 * from the backend registry. Before this there were four hand-maintained
 * copies (here, LanguageSelector, this module's test, and the backend), and
 * the failure mode was user-facing: a language in the picker but not in the
 * backend lets a student write a solution and be told the language is
 * unsupported.
 *
 * What stays hand-written here is the editor CONTENT (stubs) — that is UI
 * copy, not language identity, and has no business in a backend registry.
 */

import registry from './languages.generated.json';

type GeneratedLanguage = {
  key: string;
  backendKey: string;
  label: string;
  extension: string;
  spellings: string[];
  selfContained: boolean;
};

export const LANGUAGE_REGISTRY: GeneratedLanguage[] = registry.languages;

/** Frontend language keys, generated from the backend registry. */
export const CANONICAL_LANGUAGES = LANGUAGE_REGISTRY.map((l) => l.key);

// The selector's value and the API's boilerplate_code key are not always the
// same string: the selector uses "js" while questions store their template
// under "javascript". Submissions were unaffected (the backend accepts both
// spellings), which is why the mismatch stayed invisible while every
// JavaScript user got an empty editor. `spellings` comes from the backend, so
// the two can no longer disagree.
export const BOILERPLATE_KEYS: Record<string, string[]> = Object.fromEntries(
  LANGUAGE_REGISTRY.map((l) => [l.key, l.spellings]),
);

// Languages the server can wrap generically (see services.py): submitting a
// Solution class alone is enough, because the harness uses runtime reflection
// to find the method. C and C++ have no such mechanism, so unless a question
// ships its own wrapper their code is compiled and run exactly as written and
// must be a complete program. Telling users "no template" without saying that
// would still leave them writing code that cannot compile.
export const SELF_CONTAINED_LANGUAGES = new Set(
  LANGUAGE_REGISTRY.filter((l) => l.selfContained).map((l) => l.key),
);

// Shown only when a problem genuinely has no template for the chosen
// language, so the editor is never blank and never left holding the previous
// language's code. For C/C++ this is a compilable skeleton rather than a bare
// comment, since a bare comment cannot build.
export const EMPTY_STUB: Record<string, string> = {
  python: '# Write your solution here\n\n',
  java: '// Write your solution here\n\n',
  js: '// Write your solution here\n\n',
  cpp:
    '#include <bits/stdc++.h>\nusing namespace std;\n\n' +
    'int main() {\n' +
    '    // C++ runs as a complete program: read stdin, print the answer.\n' +
    '    // Write your code here\n' +
    '    return 0;\n}\n',
  c:
    '#include <stdio.h>\n\n' +
    'int main(void) {\n' +
    '    /* C runs as a complete program: read stdin, print the answer. */\n' +
    '    /* Write your code here */\n' +
    '    return 0;\n}\n',
};

export const FALLBACK_STUB = '// Write your solution here\n\n';

/** The stored starter template for a language, or null if there isn't one. */
export function templateFor(boilerplate: any, lang: string): string | null {
  if (!boilerplate || typeof boilerplate !== 'object') return null;
  for (const key of BOILERPLATE_KEYS[lang] ?? [lang]) {
    const template = boilerplate[key];
    if (typeof template === 'string' && template.trim()) return template;
  }
  return null;
}

/** Languages this problem actually ships a usable template for. */
export function availableLanguages(boilerplate: any): string[] {
  return Object.keys(BOILERPLATE_KEYS).filter(
    (lang) => templateFor(boilerplate, lang) !== null,
  );
}

/**
 * What the editor should contain for a language: the stored template if the
 * question has one, otherwise a compilable skeleton. Never returns empty —
 * that was the M2 defect.
 */
export function editorContentFor(boilerplate: any, lang: string): string {
  return templateFor(boilerplate, lang) ?? EMPTY_STUB[lang] ?? FALLBACK_STUB;
}
