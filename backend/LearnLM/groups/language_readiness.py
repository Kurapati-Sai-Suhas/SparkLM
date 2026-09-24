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
import copy
import re
from dataclasses import asdict, dataclass

from common import languages
from groups import execution_contract, structural_types

#: Verdicts.
READY = "READY"
NOT_READY = "NOT_READY"
UNKNOWN = "UNKNOWN"

#: Machine-readable causes, so a report can group 1,788 questions without
#: parsing prose. `reason` stays the human sentence; `cause` is the bucket.
NO_STARTER = "no_starter"
NO_ENTRY_POINT = "no_entry_point"
NO_SOLUTION_CLASS = "no_solution_class"
UNPARSEABLE = "unparseable"
UNDEFINED_ANNOTATION = "undefined_annotation"
STRUCTURAL_TYPE = "structural_type"
NO_HARNESS = "no_harness"
UNREGISTERED = "unregistered_language"

#: Phase 1 M5 splits the old single `structural_type` verdict in two, because
#: it was answering two different questions with one word.
#:
#:   STRUCTURAL_TYPE       the platform CAN build this structure, but not at
#:                         the contract version this question declares. A
#:                         migration decision, per question, with oracle
#:                         re-verification — not a missing parser.
#:   STRUCTURAL_UNSUPPORTED  no adapter exists and none is planned, with the
#:                         reason named: `Node` is seven different structures,
#:                         `ImmutableListNode` is a different grading contract.
#:
#: Reporting both as "structural_type" hid that the first is a scheduling
#: question and the second is an architecture question.
STRUCTURAL_UNSUPPORTED = "structural_unsupported"

#: The two causes that describe the QUESTION's declared signature rather than
#: one starter's shape (Phase 1 M17). Both mean the harness, not the learner,
#: decides what the method receives — so no learner code can pass — and both
#: hold in every reflection language, because the contract picks the harness.
STRUCTURAL_CAUSES = frozenset({STRUCTURAL_TYPE, STRUCTURAL_UNSUPPORTED})

#: Structural types no contract deserializes, in any language (P2.34).
#: A signature naming one receives a raw string instead.
#:
#: Kept as the union of what `structural_types` knows about: the two it can
#: build plus the ones it names as unsupported. A literal set here would be a
#: third list of structural names to keep in step with the other two.
STRUCTURAL_TYPES = frozenset(
    {structural.name for structural in structural_types.REGISTRY}
    | set(structural_types.UNSUPPORTED))

#: The reflection harness emits no imports, so an annotation naming anything
#: outside builtins raises before the learner's first line.
_PYTHON_PROVIDES = frozenset(dir(builtins))

#: The project's canonical Python annotation convention (M2 P2.38 / M6.1):
#: PEP 585 builtin generics, not the `typing` aliases they replaced.
#:
#: Not a style preference, and not this module's invention. It is what
#: `ai_services.generate_full_question` instructs the model to emit, and it is
#: why Judge0 language id 92 (Python 3.11) was selected over 71 — 3.8 raises
#: `TypeError` on `list[int]`, so the convention and the runtime were chosen
#: together.
#:
#: Only aliases whose replacement is a BUILTIN belong here. `Deque` and
#: `DefaultDict` are deliberately absent: `deque[int]` names `collections`,
#: which the harness does not import either, so lowering them would turn one
#: NameError into another.
CANONICAL_GENERICS = {
    "List": "list",
    "Dict": "dict",
    "Set": "set",
    "FrozenSet": "frozenset",
    "Tuple": "tuple",
    "Type": "type",
}


@dataclass(frozen=True)
class Readiness:
    language: str
    verdict: str
    reason: str = ""
    #: Machine-readable bucket for the refusal; empty when ready.
    cause: str = ""

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
                         f"{language!r} is not a registered language",
                         UNREGISTERED)

    source = boilerplate_for(question, lang.key)
    if source is None:
        return Readiness(lang.key, NOT_READY,
                         "no starter code exists for this language",
                         NO_STARTER)

    version = execution_contract.contract_version(question)
    result = assess_source(source, lang.key, version)
    if result.verdict == UNKNOWN:
        declared = _declared_structure_blocker(question, lang, version)
        if declared is not None:
            return declared
    return result


def _declared_structure_blocker(question, lang, version):
    """
    A structural blocker the QUESTION declares, for a starter that shows none
    (Phase 1 M17).

    JavaScript carries no types, so `_assess_declared_structures` finds a
    structure in a JS starter only when a comment or a helper happens to name
    it. The M14 audit counted 47 of 130 structural v1 questions whose JS
    starter names none: all reported UNKNOWN, and UNKNOWN counts as servable.
    q100 was one, and executing it showed what that UNKNOWN hid — a correct JS
    solution scored 6/17, because the v1 harness hands the method parsed
    arrays and every `.val` reads `undefined`.

    The structure is not unknown. The question's Python starter declares it —
    the signature grading, the migration classifier and the reference all
    read — and what the harness BUILDS is decided by the contract, not by the
    language: no v1 or v3 harness builds a `TreeNode` in any language, and no
    contract builds `Node` anywhere. So a Python verdict of STRUCTURAL_TYPE or
    STRUCTURAL_UNSUPPORTED is evidence about this language too.

    Consulted only after this language's own starter was undecided, so
    evidence in the starter itself always wins. v2 with a buildable structure
    is untouched: Python is READY there, and whether this language's adapter
    binds it is exactly what its UNKNOWN already says.
    """
    python = boilerplate_for(question, "python")
    if python is None:
        return None
    declared = assess_source(python, "python", version)
    if declared.cause not in STRUCTURAL_CAUSES:
        return None
    return Readiness(
        lang.key, NOT_READY,
        f"the {lang.label} starter names no type, but the question's "
        f"signature does — its Python starter: {declared.reason}. The "
        f"harness is chosen by the contract, not the language, so the "
        f"{version} {lang.label} harness builds no such structure either",
        declared.cause)


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
                         f"{language!r} is not a registered language",
                         UNREGISTERED)
    if not (source or "").strip():
        return Readiness(lang.key, NOT_READY,
                         "no starter code exists for this language",
                         NO_STARTER)

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
            f"translation unit cannot link", NO_ENTRY_POINT)
    return Readiness(lang.key, READY)


def _assess_reflection(lang, source, version):
    """python, java, javascript: the harness wraps a `Solution`."""
    if version == execution_contract.CONTRACT_V2:
        if execution_contract.V2_WRAPPERS.get(lang.key) is None:
            return Readiness(
                lang.key, NOT_READY,
                f"no v2 harness exists for {lang.label}", NO_HARNESS)

    if not re.search(r"\bSolution\b", source):
        return Readiness(
            lang.key, NOT_READY,
            "the harness instantiates `Solution`, which the starter does not "
            "define", NO_SOLUTION_CLASS)

    if lang.key == "python":
        return _assess_python(lang, source, version)

    structural = _assess_declared_structures(lang, source, version)
    if structural is not None:
        return structural

    # Java and JavaScript are structurally checked only as far as the shape
    # above. Deciding more would need a compiler (Java) or would duplicate
    # the prototype-chain walk the JS harness already does correctly.
    return Readiness(lang.key, UNKNOWN,
                     "starter shape is right; execution not statically decidable")


def _assess_declared_structures(lang, source, version):
    """
    A structural blocker in a NON-Python reflection starter, or None
    (Phase 1 M11).

    Java and JavaScript starters carry no annotations, so the structural type
    is read from the type names the source mentions — `TreeNode root`,
    `ListNode head`. Narrower than Python's AST reading, and honest about it:
    it can only find a name that is present.

    Before M11 these questions were reported UNKNOWN, which counts as
    SERVABLE. A v1 Java question declaring `TreeNode root` cannot execute at
    all — the harness binds a raw String and `invoke` throws — so "we cannot
    decide" was the wrong answer to a question that was decidable. The
    structural gap was hidden inside UNKNOWN; this is what M11's brief means
    by not letting missing structural support stay hidden.
    """
    unsupported = sorted(
        name for name in structural_types.UNSUPPORTED
        if re.search(r"\b" + name + r"\b", source))
    if unsupported:
        first = unsupported[0]
        return Readiness(
            lang.key, NOT_READY,
            f"signature declares {', '.join(unsupported)}: "
            f"{structural_types.UNSUPPORTED[first]}", STRUCTURAL_UNSUPPORTED)

    buildable = sorted(
        structural.name for structural in structural_types.REGISTRY
        if re.search(r"\b" + structural.name + r"\b", source))
    if not buildable:
        return None

    if version != execution_contract.CONTRACT_V2:
        return Readiness(
            lang.key, NOT_READY,
            f"starter declares {', '.join(buildable)}, which only the v2 "
            f"harness builds; this question declares {version}, whose harness "
            f"would pass a string", STRUCTURAL_TYPE)

    if lang.key not in _STRUCTURAL_ADAPTERS:
        return Readiness(
            lang.key, NOT_READY,
            f"starter declares {', '.join(buildable)} but no structural "
            f"adapter is wired for {lang.label}", NO_HARNESS)

    # v2 with a wired adapter: the prelude defines the class and builds the
    # object from the canonical array. Still UNKNOWN rather than READY —
    # everything a checker without a compiler can establish is established,
    # and claiming more would be claiming a compile.
    return Readiness(
        lang.key, UNKNOWN,
        f"{', '.join(buildable)} is built by the v2 structural adapter; "
        f"execution not statically decidable")


#: Languages whose v2 harness actually carries a structural adapter.
#:
#: Read off the TEMPLATES rather than listed here, so wiring an adapter is
#: what changes readiness and a stale list cannot claim one that does not
#: exist. Java's prelude is assembled by `_java_prelude` rather than stored in
#: `STRUCTURAL_PRELUDES`, so the placeholder — which every wired language's
#: template must contain — is the one signal true of all three.
_STRUCTURAL_ADAPTERS = frozenset(
    key for key, template in execution_contract.V2_WRAPPERS.items()
    if "{structural_prelude_" in template)

#: The same set under a public name (Phase 1 M17): hidden-test admission asks
#: "is this language's adapter wired" and must get readiness's answer, not a
#: second reading of the templates.
STRUCTURAL_ADAPTERS = _STRUCTURAL_ADAPTERS


def _assess_python(lang, source, version=execution_contract.DEFAULT_CONTRACT):
    """
    Python is decidable further because annotations are evaluated at
    definition time, so an undefined name is a hard failure the AST can see.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return Readiness(lang.key, NOT_READY,
                         f"starter does not parse ({exc.msg})", UNPARSEABLE)

    provided = python_provided_names(tree)

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        annotations = [a.annotation for a in node.args.args if a.annotation]
        if node.returns:
            annotations.append(node.returns)
        for annotation in annotations:
            names |= annotation_names(annotation)

    unsupported = sorted(names & set(structural_types.UNSUPPORTED))
    if unsupported:
        first = unsupported[0]
        return Readiness(
            lang.key, NOT_READY,
            f"signature declares {', '.join(unsupported)}: "
            f"{structural_types.UNSUPPORTED[first]}", STRUCTURAL_UNSUPPORTED)

    buildable = sorted(names & {s.name for s in structural_types.REGISTRY})
    if buildable:
        if version != execution_contract.CONTRACT_V2:
            return Readiness(
                lang.key, NOT_READY,
                f"signature declares {', '.join(buildable)}, which only the v2 "
                f"harness builds; this question declares {version}, whose "
                f"harness would pass a string", STRUCTURAL_TYPE)
        # v2 renders the structural prelude ABOVE the learner's code, so these
        # names are defined by the time the annotation is evaluated and the
        # harness builds the object from the canonical array. Adding them to
        # `provided` rather than returning READY here on the spot: every other
        # Python check still has to pass.
        provided = provided | set(buildable)

    undefined = sorted(names - provided)
    if undefined:
        return Readiness(
            lang.key, NOT_READY,
            f"annotation names {', '.join(undefined)}, which the harness "
            f"never defines — NameError before the learner's first line",
            UNDEFINED_ANNOTATION)

    return Readiness(lang.key, READY)


def python_provided_names(tree):
    """
    Every name a Python annotation in this starter may use without raising.

    Builtins, plus whatever the starter itself defines or imports. Exposed
    because `remediate_boilerplate` decides whether a return annotation is
    broken, and "broken" must mean the same thing there as it does in the
    readiness verdict — a second copy of this set is how the generator and the
    validator disagreed for 293 questions in the first place.
    """
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                    ast.AsyncFunctionDef))}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
    return _PYTHON_PROVIDES | defined | imported


def annotation_names(annotation):
    """
    Every name one annotation expression references, quoted forms included.

    `"TreeNode"` as a string annotation is a forward reference and is resolved
    the same way at runtime, so it counts.
    """
    names = set()
    for child in ast.walk(annotation):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            names.update(_identifiers(child.value))
    return names


#: The name whose canonical spelling is a PEP 604 union rather than a rename
#: (Phase 1 M13). `Optional[X]` means `X | None` and always has; the two are
#: the same type, so rewriting one to the other changes spelling and nothing
#: else.
#:
#: Kept apart from `CANONICAL_GENERICS` because it is a different KIND of
#: rewrite — that map renames a node, this one restructures a subscript — and
#: folding them together would invite the next contributor to add
#: `Union`, `Sequence`, `Mapping` and a general typing rewriter behind them.
#: `Union[X, None]` is deliberately NOT handled: it is spelled several ways
#: (`Union[None, X]`, `Union[X, Y, None]`), and each spelling would be a
#: separate judgement rather than one mechanical rule.
_OPTIONAL = "Optional"


class _Canonicaliser(ast.NodeTransformer):
    """
    PEP 585 renames plus the one PEP 604 restructure. Refuses to guess.

    `refused` records why nothing was produced, so a caller can tell "already
    canonical" from "this is beyond the rule".
    """

    def __init__(self):
        self.refused = None

    def visit_Subscript(self, node):
        # The nesting check runs BEFORE descending. `generic_visit` is
        # depth-first, so by the time it returns the inner `Optional` has
        # already been rewritten and there is nothing left to detect —
        # `Optional[Optional[int]]` came out as `int | None | None`, which is
        # neither canonical nor what anyone wrote.
        is_optional = (isinstance(node.value, ast.Name)
                       and node.value.id == _OPTIONAL)
        if is_optional and _names_optional(node.slice):
            # Exactly ONE optional layer. `Optional[Optional[X]]` is
            # degenerate, and unwrapping it twice would be a rewriter making
            # a judgement about intent.
            self.refused = "nested Optional is not a mechanical rewrite"
            return node

        self.generic_visit(node)
        if not is_optional:
            return node
        inner = node.slice
        if isinstance(inner, ast.Tuple):
            # `Optional[X, Y]` is not valid typing; refuse rather than invent.
            self.refused = "Optional takes exactly one argument"
            return node
        return ast.BinOp(left=inner, op=ast.BitOr(),
                         right=ast.Constant(value=None))

    def visit_Name(self, node):
        if node.id in CANONICAL_GENERICS:
            node.id = CANONICAL_GENERICS[node.id]
        return node


def _names_optional(node):
    return any(isinstance(child, ast.Name) and child.id == _OPTIONAL
               for child in ast.walk(node))


def canonical_annotation(annotation):
    """
    One annotation expression rewritten in the canonical convention, as text.

    Pure: takes and returns syntax, touches no row and no file. The mapping is
    total and mechanical, which is the point — a caller can compare a proposed
    annotation against this and know the operator applied the convention
    rather than choosing a different type.

    Two rules, and deliberately only two: the PEP 585 renames in
    `CANONICAL_GENERICS`, and `Optional[X]` -> `X | None`. Anything the rule
    cannot do mechanically returns the annotation UNCHANGED, which a caller
    comparing against it reads as "no canonical repair exists" and refuses.
    Returning a best effort would be the general typing rewriter this is not.
    """
    original = ast.unparse(annotation)
    canonicaliser = _Canonicaliser()
    rewritten = canonicaliser.visit(copy.deepcopy(annotation))
    if canonicaliser.refused is not None:
        return original

    text = ast.unparse(ast.fix_missing_locations(rewritten))

    # A canonicalisation changes SPELLING. If it changed which structural type
    # the annotation names — or introduced or removed one — it is not a
    # canonicalisation, and the caller must not accept it as one. Defence in
    # depth: no rule above can do this today, and this is what stops the next
    # one from doing it quietly.
    if structural_types.by_annotation(original) is not \
            structural_types.by_annotation(text):
        return original
    return text


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
