"""
Whether a proposed hidden test case executes under its question's contract,
in each language that contract grades (Phase 1 M17).

READ-ONLY and PURE apart from reading the question it is handed. Nothing
executes: every verdict is derived from the harness's own binding rule, read
from the harness it describes.

── The gap ─────────────────────────────────────────────────────────────────

`expand_hidden_tests` promised that every added case "must bind under the
question's declared contract", then returned early for everything but v3 —
and zero production questions were v3 when the promise was written. A v1 or
v2 addition was accepted unchecked. q100 was expanded that way.

── One validator per contract, never a shared one ──────────────────────────

The three contracts bind stdin to arguments in three different ways, so a
case valid under one can be invalid under another. `"[1,2,3]"` for a
single-list method is ONE argument under v3's envelope and v2's line rule,
and THREE under v1, whose harness splats a top-level JSON array. Reusing one
contract's check for another would accept exactly the cases that fail.

  v1  Python and JavaScript: the whole input as JSON (an array is splatted,
      an object becomes keywords in Python and ONE argument in JavaScript),
      else one JSON value per non-blank line, else the raw text. Java: one
      line per parameter. No v1 harness builds a structure in any language.
  v2  One line per declared parameter, typed by the kinds `render_v2`
      injects into all three harnesses; `TreeNode`/`ListNode` lines must
      build, a structural answer must be the canonical serialisation, and
      the language's structural adapter must actually be wired.
  v3  Python only, through `execution_adapter.build_invocation` — the
      behaviour this command always had, preserved.

C and C++ are self-contained under every contract: the learner's program
reads stdin itself, so there is no binding to check and none is forced.

── Failing clearly ─────────────────────────────────────────────────────────

A language whose input format nothing here models — a question's own
`hidden_wrapper_code`, or a contract no harness knows — is REFUSED, never
waved through. A language the contract does not grade at all (v3 outside
Python) is reported as NOT_DEFINED: that is a property of the question,
shared by every case it already has, not of the addition.
"""

import ast
import inspect
import json
import re
from dataclasses import dataclass

from common import languages
from groups import execution_adapter, execution_contract, language_readiness
from groups import migration_readiness

#: Outcomes, per language.
VALIDATED = "validated"
REFUSED = "refused"
#: Self-contained: the program reads stdin itself; nothing binds it.
RAW_STDIN = "raw_stdin"
#: The contract has no harness for this language at all.
NOT_DEFINED = "not_defined"

#: The language whose starter declares the contract of record.
CONTRACT_LANGUAGE = "python"

#: Answer forms the reflection harnesses print for a declared scalar return.
#: v1 prints `str(res).lower()` for a bool, v2 prints true/false, and both
#: print `str(res)` for an int — so these hold under either contract.
_BOOLEAN_ANSWERS = frozenset({"true", "false"})
_INTEGER_ANSWER = re.compile(r"-?\d+")


@dataclass(frozen=True)
class LanguageCheck:
    language: str
    outcome: str
    detail: str

    @property
    def refused(self):
        return self.outcome == REFUSED


def check_case(question, case):
    """One `LanguageCheck` per registered language, in registry order."""
    try:
        version = execution_contract.contract_version(question)
    except execution_contract.UnknownExecutionContract as exc:
        return tuple(LanguageCheck(lang.key, REFUSED,
                                   f"{exc}; no validator exists for it")
                     for lang in languages.REGISTRY)

    fields = ("stdin", "expected_output")
    if not all(isinstance(case.get(field), str) for field in fields):
        # The M2 P2.7h repair class, seen again in the stored bank: an answer
        # key held as a JSON number. Nothing can be bound from a non-text
        # field, in any language.
        return tuple(LanguageCheck(lang.key, REFUSED,
                                   "stdin and expected_output must be text")
                     for lang in languages.REGISTRY)

    validate = _REFLECTION[version]
    context = _Context(question, version, case)
    checks = []
    for lang in languages.REGISTRY:
        if _own_wrapper(question, lang.key):
            checks.append(LanguageCheck(
                lang.key, REFUSED,
                "graded by the question's own wrapper, which defines its own "
                "input format; no validator models it"))
        elif lang.self_contained:
            checks.append(_self_contained(context, lang))
        else:
            checks.append(validate(context, lang))
    return tuple(checks)


class _Context:
    """What every language's check needs, read once per case."""

    def __init__(self, question, version, case):
        self.question = question
        self.version = version
        self.stdin = case["stdin"]
        self.expected = case["expected_output"]
        self.starter = language_readiness.boilerplate_for(
            question, CONTRACT_LANGUAGE) or ""

    def prepared(self, language):
        """The stdin the harness is actually fed — the grader's own seam."""
        from groups.services import GradingService

        return GradingService.prepare_stdin(self.question, language, self.stdin)

    def declared_blocker(self):
        """A structure the question declares that this contract cannot build."""
        verdict = language_readiness.assess_source(
            self.starter, CONTRACT_LANGUAGE, self.version)
        if verdict.cause in language_readiness.STRUCTURAL_CAUSES:
            return verdict.reason
        return None


def _own_wrapper(question, language):
    from groups.services import wrapper_for

    return wrapper_for(question, language) is not None


def _self_contained(context, lang):
    from groups.services import ExecutionContractError

    try:
        context.prepared(lang.key)
    except ExecutionContractError as exc:
        return LanguageCheck(lang.key, NOT_DEFINED, str(exc))
    return LanguageCheck(
        lang.key, RAW_STDIN,
        f"{lang.label} is self-contained: the learner's program reads this "
        f"stdin itself and no harness binds it, so there is nothing further "
        f"to validate")


# ── the declared signature ────────────────────────────────────────────────

def _graded_method(source):
    """(node, None) for the method every harness calls, or (None, reason)."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError as exc:
        return None, f"the Python starter does not parse ({exc.msg})"
    if not any(isinstance(node, ast.ClassDef)
               and node.name == "Solution" for node in tree.body):
        return None, ("the Python starter declares no `Solution` class, and "
                      "every harness instantiates one")
    node, is_method = execution_adapter.chosen_function(source)
    if node is None or not is_method:
        return None, "`Solution` declares no public method to call"
    return node, None


def _signature(node):
    """
    The method's signature as the harness calls it, built from the AST — the
    starter is never executed. The receiver is dropped by NAME, exactly as
    `execution_adapter.declared_signature` drops it, so the arity here is the
    arity `v2_parameter_kinds` and the v3 envelope see.
    """
    kind = inspect.Parameter
    arguments = node.args
    positional = list(arguments.posonlyargs) + list(arguments.args)
    required = len(positional) - len(arguments.defaults)
    parameters = [
        kind(argument.arg,
             kind.POSITIONAL_ONLY if index < len(arguments.posonlyargs)
             else kind.POSITIONAL_OR_KEYWORD,
             default=kind.empty if index < required else None)
        for index, argument in enumerate(positional)]
    if positional and positional[0].arg in ("self", "cls"):
        parameters = parameters[1:]
    if arguments.vararg:
        parameters.append(kind(arguments.vararg.arg, kind.VAR_POSITIONAL))
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        parameters.append(kind(argument.arg, kind.KEYWORD_ONLY,
                               default=kind.empty if default is None else None))
    if arguments.kwarg:
        parameters.append(kind(arguments.kwarg.arg, kind.VAR_KEYWORD))
    return inspect.Signature(parameters)


def _answer_form(node, expected):
    """
    A refusal when the stored answer cannot be what the method prints.

    Only for a return annotated EXACTLY `bool` or `int`: every reflection
    harness under every contract prints those as `true`/`false` and as digits.
    `Optional[int]`, a list, a float — each has a legitimate spelling this
    check would have to guess at, so it does not.
    """
    declared = ast.unparse(node.returns) if node.returns is not None else ""
    answer = (expected or "").strip()
    if declared == "bool" and answer not in _BOOLEAN_ANSWERS:
        return (f"the method returns bool, which every reflection harness "
                f"prints as true/false; the stored answer is {answer!r}")
    if declared == "int" and not _INTEGER_ANSWER.fullmatch(answer):
        return (f"the method returns int; the stored answer {answer!r} is not "
                f"an integer")
    return None


# ── declared values ───────────────────────────────────────────────────────
#
# Checked only for an EXACT declaration: `int`, `float`, `bool`, `str`, or a
# `list[...]` of one of those. `execution_adapter.classify_annotation` matches
# on substrings — `Optional[int]` reads as an integer — which is right for
# guessing and wrong for refusing, so anything else is left unchecked rather
# than judged by a guess.

_EXACT_LIST = re.compile(r"list\[(int|float|bool|str)\]")
_SCALARS = ("int", "float", "bool", "str")


def _exact(annotation_text):
    """(element, is_sequence) for an exact declaration, else None."""
    text = (annotation_text or "").replace(" ", "").lower()
    match = _EXACT_LIST.fullmatch(text)
    if match:
        return match.group(1), True
    if text in _SCALARS:
        return text, False
    return None


def _json_fits(value, element):
    if element == "bool":
        return isinstance(value, bool)
    if element == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if element == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


def _v1_value_problem(name, value, annotation_text):
    """A declared parameter the v1 JSON binding hands the wrong type."""
    declared = _exact(annotation_text)
    if declared is None:
        return None
    element, sequence = declared
    shown = f"list[{element}]" if sequence else element
    if sequence:
        if not isinstance(value, list):
            return (f"{name} is declared {shown}, but v1 passes "
                    f"{type(value).__name__} {value!r:.40}")
        misfits = [item for item in value if not _json_fits(item, element)]
        if misfits:
            return (f"{name} is declared {shown}, but v1 passes an element "
                    f"{misfits[0]!r:.40}")
        return None
    if not _json_fits(value, element):
        return (f"{name} is declared {shown}, but v1 passes "
                f"{type(value).__name__} {value!r:.40}")
    return None


def _v2_token(token):
    """What the v2 harness's `_sparklm_token` makes of one token."""
    try:
        int(token)
        return "int"
    except ValueError:
        pass
    try:
        float(token)
        return "float"
    except ValueError:
        pass
    return "bool" if token in ("true", "false") else "str"


#: Which token readings satisfy a declared element type.
_V2_ACCEPTS = {"int": {"int"}, "float": {"int", "float"}, "bool": {"bool"},
               "str": {"str"}}


def _v2_line_problem(position, line, annotation_text):
    """
    A declared parameter the v2 line rule hands the wrong value.

    v2 splits a line on whitespace and guesses each token's type. So a JSON
    array on a `list[int]` line is ONE token and arrives as the string
    "[1,2,3]"; a `str` line of "123" arrives as the int 123; a scalar line
    of two tokens arrives as a list.
    """
    declared = _exact(annotation_text)
    if declared is None:
        return None
    element, sequence = declared
    shown = f"list[{element}]" if sequence else element
    tokens = line.split()
    if not sequence and len(tokens) != 1:
        return (f"argument {position} is declared {shown}, but its line holds "
                f"{len(tokens)} token(s), which v2 passes as a list")
    for token in tokens:
        reading = _v2_token(token)
        if reading not in _V2_ACCEPTS[element]:
            return (f"argument {position} is declared {shown}, but v2 reads "
                    f"{token[:32]!r} as {reading}; the line format is "
                    f"whitespace-separated tokens, not JSON")
    return None


# ── v1 ────────────────────────────────────────────────────────────────────

POSITIONAL = "positional"
KEYWORD = "keyword"


def _json_value(text, strict):
    """
    `json.loads`, or JavaScript's `JSON.parse` when `strict`: the only
    difference that matters here is that JSON.parse rejects NaN and Infinity,
    which Python's decoder accepts.
    """
    if strict:
        return json.loads(text, parse_constant=_reject_constant)
    return json.loads(text)


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def v1_binding(stdin, strict=False):
    """
    (mode, arguments) exactly as the v1 reflection harness derives them.

    Mirrors `GENERIC_PYTHON_WRAPPER` (and `GENERIC_JS_WRAPPER` with `strict`)
    step for step. v1 is frozen byte-for-byte, so the mirror cannot drift
    from a template that may not change; `test_suite_admission` executes the
    real template against it to prove they agree.
    """
    text = stdin.strip()
    try:
        parsed = _json_value(text, strict)
    except Exception:                                      # noqa: BLE001
        lines = [line for line in text.split("\n") if line.strip() != ""]
        try:
            return POSITIONAL, [_json_value(line, strict) for line in lines]
        except Exception:                                  # noqa: BLE001
            return POSITIONAL, [text]
    if isinstance(parsed, list):
        return POSITIONAL, parsed
    if isinstance(parsed, dict):
        return KEYWORD, parsed
    return POSITIONAL, [parsed]


def _java_lines(stdin):
    """Java's `input.trim().split("\\n")`: trim strips every char <= U+0020."""
    return stdin.strip("".join(chr(code) for code in range(0x21))).split("\n")


def _v1(context, lang):
    blocker = context.declared_blocker()
    if blocker:
        return LanguageCheck(
            lang.key, REFUSED,
            f"the question declares a structure v1 cannot build ({blocker}). "
            f"No v1 harness builds one in any language, so no case of this "
            f"question executes as intended; migrate it to v2 first")
    node, problem = _graded_method(context.starter)
    if problem:
        return LanguageCheck(lang.key, REFUSED,
                             f"{problem}; there is no declared signature to "
                             f"bind against")
    signature = _signature(node)
    prepared = context.prepared(lang.key)

    if lang.key == "java":
        lines = _java_lines(prepared)
        arity = sum(1 for p in signature.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
        if len(lines) != arity:
            return LanguageCheck(
                lang.key, REFUSED,
                f"Java's v1 harness binds one line per parameter: "
                f"{len(lines)} line(s) against {arity} declared parameter(s) "
                f"of {node.name} — "
                + ("the surplus is silently ignored" if len(lines) > arity
                   else "the missing arguments are passed as null"))
        detail = (f"{len(lines)} line(s) for {arity} parameter(s); each line "
                  f"is coerced by the Java starter's own parameter type, "
                  f"which this check does not read")
    else:
        mode, arguments = v1_binding(prepared, strict=lang.key != "python")
        if mode == KEYWORD and lang.key != "python":
            return LanguageCheck(
                lang.key, REFUSED,
                "a top-level JSON object binds as KEYWORD arguments in "
                "Python's v1 harness but as ONE positional argument in "
                f"{lang.label}'s; the two cannot both be right")
        try:
            if mode == KEYWORD:
                bound = signature.bind(**arguments)
            else:
                bound = signature.bind(*arguments)
        except TypeError as exc:
            silently = ("" if lang.key == "python" else
                        f" ({lang.label} does not check arity, so this would "
                        f"mis-bind silently rather than fail)")
            return LanguageCheck(
                lang.key, REFUSED,
                f"the v1 harness calls {node.name} with "
                f"{_describe(mode, arguments)}, which does not fit "
                f"{node.name}{signature}: {exc}{silently}")
        annotations = {argument.arg: _annotation(argument)
                       for argument in node.args.args}
        for name, value in bound.arguments.items():
            problem = _v1_value_problem(name, value, annotations.get(name))
            if problem:
                return LanguageCheck(lang.key, REFUSED, problem)
        detail = (f"binds as {_describe(mode, arguments)} to "
                  f"{node.name}{signature}")

    answer = _answer_form(node, context.expected)
    if answer:
        return LanguageCheck(lang.key, REFUSED, answer)
    return LanguageCheck(lang.key, VALIDATED, detail)


def _annotation(argument):
    return ast.unparse(argument.annotation) if argument.annotation else ""


def _describe(mode, arguments):
    if mode == KEYWORD:
        return f"keyword argument(s) {sorted(arguments)}"
    return f"{len(arguments)} positional argument(s)"


# ── v2 ────────────────────────────────────────────────────────────────────

def _v2(context, lang):
    if execution_contract.V2_WRAPPERS.get(lang.key) is None:
        return LanguageCheck(lang.key, REFUSED,
                             f"no v2 harness exists for {lang.label}")
    blocker = context.declared_blocker()
    if blocker:
        return LanguageCheck(lang.key, REFUSED,
                             f"the question declares a structure no contract "
                             f"builds ({blocker})")
    node, problem = _graded_method(context.starter)
    if problem:
        return LanguageCheck(lang.key, REFUSED,
                             f"{problem}; there is no declared signature to "
                             f"bind against")
    if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
        return LanguageCheck(
            lang.key, REFUSED,
            f"{node.name} takes *args, **kwargs or keyword-only parameters; v2 "
            f"binds exactly one line per declared positional parameter")

    kinds = execution_contract.v2_parameter_kinds(context.starter)
    return_kind = execution_contract.v2_return_kind(context.starter)
    structural = [kind for kind in kinds + [return_kind]
                  if kind in execution_contract.STRUCTURAL_KINDS]
    if structural and lang.key not in language_readiness.STRUCTURAL_ADAPTERS:
        return LanguageCheck(
            lang.key, REFUSED,
            f"the signature declares {sorted(set(structural))} but no "
            f"structural adapter is wired into the {lang.label} v2 harness")

    arguments = migration_readiness.parse_arguments(context.prepared(lang.key))
    if len(arguments) > len(kinds) and any(
            value.strip() for value in arguments[len(kinds):]):
        return LanguageCheck(
            lang.key, REFUSED,
            f"{len(arguments)} line(s) against {len(kinds)} declared "
            f"parameter(s); the surplus {arguments[len(kinds):]!r} would be "
            f"dropped by every v2 harness")
    if len(arguments) < len(kinds):
        return LanguageCheck(
            lang.key, REFUSED,
            f"{len(arguments)} line(s) against {len(kinds)} declared "
            f"parameter(s); spell an empty argument as an empty line rather "
            f"than leaving it out")

    _name, declared = execution_adapter.declared_signature(context.starter)
    for position, kind in enumerate(kinds):
        # Exactly what every v2 harness reads for parameter N: its line, or
        # "" when the input ran out.
        line = arguments[position] if position < len(arguments) else ""
        if kind not in execution_contract.STRUCTURAL_KINDS:
            problem = _v2_line_problem(position + 1, line,
                                       declared[position][1])
            if problem:
                return LanguageCheck(lang.key, REFUSED, problem)
            continue
        fixed, canonical = migration_readiness.canonical_round_trip(line, kind)
        if canonical is None:
            return LanguageCheck(
                lang.key, REFUSED,
                f"argument {position + 1} is declared {kind} and "
                f"{line[:48]!r} does not build one")
        if not fixed:
            return LanguageCheck(
                lang.key, REFUSED,
                f"argument {position + 1} ({kind}) is not canonical: store "
                f"{canonical[:48]!r}, or duplicate detection cannot see it")

    if return_kind in execution_contract.STRUCTURAL_KINDS:
        fixed, canonical = migration_readiness.canonical_round_trip(
            context.expected, return_kind)
        if canonical is None or not fixed:
            return LanguageCheck(
                lang.key, REFUSED,
                f"the method returns a {return_kind}, which every v2 harness "
                f"prints canonically; the stored answer "
                f"{(context.expected or '')[:48]!r} "
                + ("is not one" if canonical is None
                   else f"canonicalises to {canonical[:48]!r}"))
    else:
        answer = _answer_form(node, context.expected)
        if answer:
            return LanguageCheck(lang.key, REFUSED, answer)

    return LanguageCheck(
        lang.key, VALIDATED,
        f"one line per declared parameter, kinds {kinds}"
        + (f", returning {return_kind}" if return_kind else "")
        + " — the vector render_v2 injects into this harness")


# ── v3 ────────────────────────────────────────────────────────────────────

def _v3(context, lang):
    """Unchanged from the command's original check, for Python."""
    if lang.key != CONTRACT_LANGUAGE:
        from groups.services import ExecutionContractError

        try:
            context.prepared(lang.key)
        except ExecutionContractError as exc:
            return LanguageCheck(lang.key, NOT_DEFINED, str(exc))
    invocation = execution_adapter.build_invocation(context.stdin,
                                                    context.starter)
    if not invocation.ok:
        return LanguageCheck(
            lang.key, REFUSED,
            f"does not bind under {context.version}: {invocation.outcome} — "
            f"{invocation.detail}")
    if invocation.warnings:
        return LanguageCheck(
            lang.key, REFUSED,
            f"binds only by guessing ({', '.join(invocation.warnings)})")
    return LanguageCheck(lang.key, VALIDATED,
                         f"binds as {invocation.envelope()[:60]}")


_REFLECTION = {
    execution_contract.CONTRACT_V1: _v1,
    execution_contract.CONTRACT_V2: _v2,
    execution_contract.CONTRACT_V3: _v3,
}
