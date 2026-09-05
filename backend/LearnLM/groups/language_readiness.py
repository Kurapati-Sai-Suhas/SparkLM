"""
Whether a question can actually be executed in a given language (M2 P2.35).

READ-ONLY. Nothing here writes.

── The gap this closes ─────────────────────────────────────────────────────

`CodeSubmitSerializer.validate_language` checks that a language is REGISTERED
— that the platform knows what "cpp" means. It has never checked that the
QUESTION can run in it. Those are different questions and the second one is
the one that matters to a learner.

The P2.34 audit measured the consequence. Every one of the 638 shipped C++
starters is a Python-shaped `class Solution` with no `main()` and no
`#include`; C++ is `self_contained`, so `_build_executable` passes the source
to Judge0 unwrapped, and a translation unit with no `main` does not link.
A C++ learner submitting the starter they were handed receives a compile
failure — and because `adaptive_eligible` carries no language term, that
failure is a genuine attempt against a "verified" question.

── What "ready" means, per execution model ─────────────────────────────────

Two models, and they need different evidence:

  REFLECTION  python, java, javascript — the harness wraps the learner's
              `Solution`, discovers one public method and binds arguments.
              Ready requires: a starter for the language, and a starter that
              defines the entry point the harness will look for.

  SELF-CONTAINED  c, cpp — no wrapper exists at any contract version; the
              learner writes a complete program. Ready requires a starter
              that IS a complete program, which minimally means a `main`.

── What this does NOT claim ────────────────────────────────────────────────

READY means "no blocker is provable without executing it". It is a necessary
condition, not a sufficient one. Static analysis cannot see an unannotated
parameter bound to the wrong type, or a starter that compiles and computes
nonsense. Where the check cannot decide, it says UNKNOWN rather than READY —
the gate treats UNKNOWN as servable, because refusing every question a
checker cannot fully prove would be hiding failures behind a checker's
limits, which is precisely what M1 forbids.
"""

import ast
import builtins
import re
from dataclasses import asdict, dataclass

from common import languages
from groups import execution_contract

#: Verdicts.
READY = "READY"
NOT_READY = "NOT_READY"
UNKNOWN = "UNKNOWN"

#: Structural types no contract deserializes, in any language (P2.34).
#: A signature naming one receives a raw string instead.
STRUCTURAL_TYPES = frozenset({"TreeNode", "ListNode", "Node"})

#: The reflection harness emits no imports, so an annotation naming anything
#: outside builtins raises before the learner's first line.
_PYTHON_PROVIDES = frozenset(dir(builtins))


@dataclass(frozen=True)
class Readiness:
    language: str
    verdict: str
    reason: str = ""

    @property
    def ready(self):
        """UNKNOWN counts as servable — see the module docstring."""
        return self.verdict != NOT_READY

    def as_dict(self):
        return asdict(self)


def boilerplate_for(question, language):
    """
    The starter for `language`, trying every accepted spelling.

    Seed generations filed JavaScript under both "javascript" and "js", so a
    single-key lookup silently reported a language as absent when it was
    merely spelled differently — the same class of bug `wrapper_spellings`
    exists to prevent for wrappers.
    """
    stored = question.boilerplate_code or {}
    for spelling in languages.wrapper_spellings(language):
        source = stored.get(spelling)
        if source and source.strip():
            return source
    return None


def assess(question, language):
    """Readiness of one question in one language. Reads only."""
    lang = languages.get(language)
    if lang is None:
        return Readiness(str(language), NOT_READY,
                         f"{language!r} is not a registered language")

    source = boilerplate_for(question, lang.key)
    if source is None:
        return Readiness(lang.key, NOT_READY,
                         "no starter code exists for this language")

    return assess_source(source, lang.key,
                         execution_contract.contract_version(question))


def assess_source(source, language, version=execution_contract.DEFAULT_CONTRACT):
    """
    Readiness of a starter SOURCE, independent of any Question row.

    The source-level entry point exists because two callers need the same
    judgement from different inputs: the serving gate has a Question, and the
    trusted-content worklist has only the stored starter. Splitting the rule
    across both would give the platform two answers to "can this run".
    """
    lang = languages.get(language)
    if lang is None:
        return Readiness(str(language), NOT_READY,
                         f"{language!r} is not a registered language")
    if not (source or "").strip():
        return Readiness(lang.key, NOT_READY,
                         "no starter code exists for this language")

    if lang.self_contained:
        return _assess_self_contained(lang, source)
    return _assess_reflection(lang, source, version)


def _assess_self_contained(lang, source):
    """
    C and C++: the learner's file IS the program, so it needs an entry point.

    Checked by regex rather than by parsing — there is no C++ parser here and
    pretending otherwise would be worse than a narrow check that is honest
    about being narrow. A `main` in a comment would pass; a starter with no
    `main` at all, which is every one of the 638 shipped C++ starters, does
    not.
    """
    if not re.search(r"\bmain\s*\(", source):
        return Readiness(
            lang.key, NOT_READY,
            f"{lang.label} is self-contained — no wrapper exists at any "
            f"contract version — but the starter defines no main(), so the "
            f"translation unit cannot link")
    return Readiness(lang.key, READY)


def _assess_reflection(lang, source, version):
    """python, java, javascript: the harness wraps a `Solution`."""
    if version == execution_contract.CONTRACT_V2:
        if execution_contract.V2_WRAPPERS.get(lang.key) is None:
            return Readiness(
                lang.key, NOT_READY,
                f"no v2 harness exists for {lang.label}")

    if not re.search(r"\bSolution\b", source):
        return Readiness(
            lang.key, NOT_READY,
            "the harness instantiates `Solution`, which the starter does not "
            "define")

    if lang.key == "python":
        return _assess_python(lang, source)

    # Java and JavaScript are structurally checked only as far as the shape
    # above. Deciding more would need a compiler (Java) or would duplicate
    # the prototype-chain walk the JS harness already does correctly.
    return Readiness(lang.key, UNKNOWN,
                     "starter shape is right; execution not statically decidable")


def _assess_python(lang, source):
    """
    Python is decidable further because annotations are evaluated at
    definition time, so an undefined name is a hard failure the AST can see.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return Readiness(lang.key, NOT_READY,
                         f"starter does not parse ({exc.msg})")

    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        annotations = [a.annotation for a in node.args.args if a.annotation]
        if node.returns:
            annotations.append(node.returns)
        for annotation in annotations:
            for child in ast.walk(annotation):
                if isinstance(child, ast.Name):
                    names.add(child.id)
                elif isinstance(child, ast.Constant) and isinstance(
                        child.value, str):
                    names.update(_identifiers(child.value))

    structural = sorted(names & STRUCTURAL_TYPES)
    if structural:
        return Readiness(
            lang.key, NOT_READY,
            f"signature declares {', '.join(structural)}, which no contract "
            f"deserializes — the harness would pass a string")

    undefined = sorted(names - _PYTHON_PROVIDES - defined - imported)
    if undefined:
        return Readiness(
            lang.key, NOT_READY,
            f"annotation names {', '.join(undefined)}, which the harness "
            f"never defines — NameError before the learner's first line")

    return Readiness(lang.key, READY)


def _identifiers(text):
    """Bare names inside a quoted forward-reference annotation."""
    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(parsed)
            if isinstance(node, ast.Name)}


def ready_languages(question):
    """Every registered language this question is not provably broken in."""
    return [lang.key for lang in languages.REGISTRY
            if assess(question, lang.key).ready]


def blocked_languages(question):
    """{language: reason} for the ones that are provably broken."""
    blocked = {}
    for lang in languages.REGISTRY:
        result = assess(question, lang.key)
        if not result.ready:
            blocked[lang.key] = result.reason
    return blocked
