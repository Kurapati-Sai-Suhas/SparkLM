"""
Input-representation repair for the M18 contract repair pilot.

PURE apart from reading the question it is handed: nothing here writes, and
nothing executes. Every verdict is derived from the v1 harnesses' own binding
rules — `suite_admission.v1_binding` for Python and JavaScript, and a mirror of
`GENERIC_JAVA_WRAPPER`'s coercion for Java.

── What this repairs, and only this ────────────────────────────────────────

A stored stdin that spells the RIGHT value in a notation the v1 harness does
not read. Two shapes are in scope, and they are the whole scope:

    `3,8,2,9`      for `prices: list[int]`   Python and JavaScript receive the
                                            TEXT; Java's split happens to work
    `'noon'`       for `s: str`              every harness receives the quotes
                                            as part of the text

The repair rewrites such a stdin into the representation every served v1
harness already binds as the intended value. The expected output, the case
order, the contract, the trust state and the question are untouched — the
command that applies a plan re-proves all of that against what it writes.

── A closed decoder set ────────────────────────────────────────────────────

A repair is only as trustworthy as the reading of the old text. So the old
stdin must decode by exactly one of three rules, and where two rules read it
they must agree:

    json_value                 already a JSON value of the declared type
    python_string_literal      ONE quoted Python string, nothing else
    comma_separated_integers   two or more integers joined by bare commas

Anything else refuses. There is deliberately no "best effort" reading: a
decoder that guesses is how a repair changes the question being asked.

── Deliberately narrow ─────────────────────────────────────────────────────

One graded parameter, declared exactly `int`, `str` or `list[int]`, on a v1
question with no wrapper of its own, served only in reflection languages.
Every other shape refuses. This is a pilot instrument, not a general rewriter.
"""

import ast
import hashlib
import json
import re
import types
from dataclasses import dataclass

from common import languages
from groups import execution_adapter, execution_contract, language_readiness
from groups import suite_admission

#: The only contract this module reads and writes.
CONTRACT = execution_contract.CONTRACT_V1

#: Languages whose v1 harness binds stdin to the graded method's arguments.
REFLECTION_LANGUAGES = ("python", "javascript", "java")

#: Declarations a repair can decode, re-encode and compare exactly.
SUPPORTED_DECLARATIONS = ("int", "str", "list[int]")

#: The decoders, by name. A plan records which one read each case.
JSON_VALUE = "json_value"
PYTHON_STRING_LITERAL = "python_string_literal"
COMMA_SEPARATED_INTEGERS = "comma_separated_integers"
DECODERS = (JSON_VALUE, PYTHON_STRING_LITERAL, COMMA_SEPARATED_INTEGERS)

#: The only review verdict under which a question may be repaired.
REVIEW_CONSISTENT = "CONSISTENT"

#: Java parameter types the generic harness coerces, by the declaration each
#: one must match. A type outside this table reaches the method as a String.
JAVA_TYPES = {"int": "int", "Integer": "int", "int[]": "list[int]",
              "String": "str"}

_COMMA_INTEGERS = re.compile(r"-?\d+(?:,-?\d+)+")
#: ONE quoted string with no prefix: rejects `'a' 'b'` (implicit
#: concatenation), triple quotes, and `r'..'`/`b'..'`/`f'..'`.
_ONE_STRING_LITERAL = re.compile(r"""(['"])(?:\\.|(?!\1)[^\\\n])*\1""")
_JAVA_INT = re.compile(r"[+-]?\d+")
_JAVA_METHOD = re.compile(
    r"\bpublic[ \t]+(?:static[ \t]+)?(?:final[ \t]+)?([\w<>\[\], ]+?)[ \t]+"
    r"(\w+)[ \t]*\(([^)]*)\)")
_JS_PROTOTYPE_METHOD = re.compile(r"Solution\.prototype\.(\w+)\s*=\s*function\b")
_JS_CLASS_METHOD = re.compile(r"^[ \t]+(?:async[ \t]+)?(\w+)[ \t]*\([^)]*\)[ \t]*\{",
                              re.MULTILINE)
_JS_NOT_METHODS = frozenset({"constructor", "if", "for", "while", "switch",
                             "catch", "function", "return"})
_INT32 = (-2 ** 31, 2 ** 31 - 1)


class RepairRefused(Exception):
    """The question, or one of its cases, is outside what this module repairs."""


# ── values ────────────────────────────────────────────────────────────────

def fits(value, declared):
    """Whether `value` is exactly the declared type — bool is not an int."""
    if declared == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "str":
        return isinstance(value, str)
    if declared == "list[int]":
        return isinstance(value, list) and all(fits(item, "int") for item in value)
    return False


def same_value(left, right):
    """Type-strict equality: `True` is not `1`, and `1.0` is not `1`."""
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            same_value(a, b) for a, b in zip(left, right))
    return left == right


def same_values(left, right):
    return (left is not None and right is not None and len(left) == len(right)
            and all(same_value(a, b) for a, b in zip(left, right)))


def _same_json(left, right):
    """Equal as stored JSON: `1`, `1.0` and `true` are three different values."""
    def encoded(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return encoded(left) == encoded(right)


# ── the closed decoder set ────────────────────────────────────────────────

def _json_reading(text):
    try:
        return json.loads(text, parse_constant=_no_constant)
    except ValueError:
        return _UNREAD


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


_UNREAD = object()


def decode_field(text, declared):
    """
    (value, decoder) for one stored field, read by the closed decoder set.

    Raises `RepairRefused` when no rule reads it as the declared type, or when
    two rules read it and disagree.
    """
    if declared not in SUPPORTED_DECLARATIONS:
        raise RepairRefused(f"declaration {declared!r} is not one this repair "
                            f"supports ({', '.join(SUPPORTED_DECLARATIONS)})")
    stripped = (text or "").strip()
    readings = []

    value = _json_reading(stripped)
    if value is not _UNREAD and fits(value, declared):
        readings.append((value, JSON_VALUE))

    if declared == "str" and _ONE_STRING_LITERAL.fullmatch(stripped):
        try:
            literal = ast.literal_eval(stripped)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            literal = None
        if isinstance(literal, str):
            readings.append((literal, PYTHON_STRING_LITERAL))

    if declared == "list[int]" and _COMMA_INTEGERS.fullmatch(stripped):
        readings.append(([int(part) for part in stripped.split(",")],
                         COMMA_SEPARATED_INTEGERS))

    if not readings:
        raise RepairRefused(
            f"{stripped[:40]!r} is not read as {declared} by any decoder in the "
            f"closed set ({', '.join(DECODERS)})")
    first, decoder = readings[0]
    for other, other_decoder in readings[1:]:
        if not same_value(first, other):
            raise RepairRefused(
                f"{stripped[:40]!r} reads as {first!r} by {decoder} and as "
                f"{other!r} by {other_decoder}; an ambiguous input is not "
                f"repaired")
    return first, decoder


# ── the canonical v1 encoding ─────────────────────────────────────────────

def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _java_trim(text):
    """Java's `String.trim()`: strips every char <= U+0020 from both ends."""
    return text.strip("".join(chr(code) for code in range(0x21)))


def bare_text_is_exact(value):
    """
    Whether the raw text `value` reaches every v1 harness as exactly `value`.

    The harnesses strip the input, try JSON, then fall back to the raw text —
    and Java trims each line. So bare text is faithful only if it has no
    surrounding whitespace, no line break, does not decode as JSON and is not
    itself a quoted or bracketed literal that a validator would read as one.
    """
    if not value or "\n" in value or "\r" in value:
        return False
    if value != value.strip() or value != _java_trim(value):
        return False
    # Text that opens like a structure is read as one by some reader, valid or
    # not; it is never left bare.
    if value[0] in "[{(":
        return False
    # JavaScript's trim() also strips characters Python's strip() keeps, such
    # as a byte-order mark; the binding mirror reads with Python's. Only a
    # visible character at either end is unambiguous to every harness.
    if not (value[0].isprintable() and value[-1].isprintable()):
        return False
    # Python's decoder, not JSON.parse: it also accepts NaN and Infinity, and
    # the Python harness would hand the method a float for either.
    try:
        json.loads(value)
    except ValueError:
        pass
    else:
        return False
    return (execution_adapter.quoted_literal(value) is None
            and not execution_adapter.container_literal(value))


def canonical_stdin(value, declared):
    """
    The v1 representation of ONE argument `value` of type `declared`.

      list[...]  the value wrapped in an array: v1 splats a top-level array, so
                 `[[3,8,2]]` is one argument `[3,8,2]`
      str        the bare text when that is exact (see `bare_text_is_exact`),
                 else the JSON string
      int        the JSON number
    """
    if declared.startswith("list["):
        text = _dumps([value])
    elif declared == "str" and bare_text_is_exact(value):
        text = value
    else:
        text = _dumps(value)
    if "\\n" in text:
        raise RepairRefused(
            "the canonical text contains a literal \\n, which the v1 seam "
            "expands into a line break before any harness reads it")
    return text


# ── what each v1 harness hands the method ─────────────────────────────────

def _prepared(question, language, stdin):
    """The bytes the harness is fed — the grader's own seam, never a copy."""
    from groups.services import GradingService

    return GradingService.prepare_stdin(question, language, stdin)


def reflection_values(prepared, language, parameter):
    """
    The arguments the v1 Python or JavaScript harness passes, or None.

    Read through `suite_admission.v1_binding`, which `test_suite_admission`
    proves against the real templates. A top-level object is keyword arguments
    in Python and ONE positional argument in JavaScript.
    """
    mode, arguments = suite_admission.v1_binding(
        prepared, strict=language != "python")
    if mode == suite_admission.KEYWORD:
        if language != "python":
            return [arguments]
        if set(arguments) != {parameter}:
            return None
        return [arguments[parameter]]
    return list(arguments)


def _java_lines(prepared):
    """
    `input.trim().split("\\n")` after Scanner re-joined the lines with "\\n".

    Scanner also ends a line at a carriage return or a Unicode separator; this
    mirror does not model those, so it declines rather than guesses.
    """
    if any(mark in prepared for mark in ("\r", "\u2028", "\u2029", "\u0085")):
        return None
    lines = _java_trim(prepared).split("\n")
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def _java_int(text):
    if not _JAVA_INT.fullmatch(text):
        return None
    value = int(text)
    return value if _INT32[0] <= value <= _INT32[1] else None


def java_values(prepared, java_types):
    """
    The arguments `GENERIC_JAVA_WRAPPER` passes, or None when it would throw.

    One line per parameter, each trimmed: `int` by `Integer.parseInt`, `int[]`
    by stripping every bracket and splitting on `[, ]+`, `String` as the line
    itself — quotes and all.
    """
    lines = _java_lines(prepared)
    if lines is None:
        return None
    values = []
    for index, java_type in enumerate(java_types):
        if index >= len(lines):
            return None                       # Java passes null
        line = _java_trim(lines[index])
        declared = JAVA_TYPES.get(java_type)
        if declared == "int":
            value = _java_int(line)
            if value is None:
                return None
        elif declared == "list[int]":
            clean = _java_trim(line.replace("[", "").replace("]", ""))
            if not clean:
                value = []
            else:
                parts = re.split(r"[, ]+", clean)
                while parts and parts[-1] == "":
                    parts.pop()
                value = [_java_int(_java_trim(part)) for part in parts]
                if any(item is None for item in value):
                    return None
        elif declared == "str":
            value = line
        else:
            return None
        values.append(value)
    return values


def bound_values(question, stdin, language, signature):
    """What the `language` v1 harness hands the graded method for `stdin`."""
    prepared = _prepared(question, language, stdin)
    if language == "java":
        return java_values(prepared, signature.java_types)
    return reflection_values(prepared, language, signature.parameter)


def binds(question, stdin, language, signature, values):
    return same_values(bound_values(question, stdin, language, signature),
                       values)


# ── the declared signature, in every served language ──────────────────────

@dataclass(frozen=True)
class Signature:
    method: str
    parameter: str
    declaration: str
    java_types: tuple


def _python_signature(question):
    source = language_readiness.boilerplate_for(question, "python") or ""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise RepairRefused(f"the Python starter does not parse ({exc.msg})")
    node, is_method = execution_adapter.chosen_function(source)
    if node is None or not is_method:
        raise RepairRefused("the Python starter declares no Solution method")
    parameters = [argument for argument in node.args.args
                  if argument.arg not in ("self", "cls")]
    if (len(parameters) != 1 or node.args.vararg or node.args.kwarg
            or node.args.kwonlyargs or node.args.posonlyargs):
        raise RepairRefused(
            f"{node.name} takes {len(parameters)} positional parameter(s); "
            f"this repair handles exactly one")
    (parameter,) = parameters
    if parameter.annotation is None:
        raise RepairRefused(f"{parameter.arg} has no annotation to repair against")
    declaration = language_readiness.canonical_annotation(
        parameter.annotation).replace(" ", "")
    if declaration not in SUPPORTED_DECLARATIONS:
        raise RepairRefused(
            f"{parameter.arg} is declared {declaration!r}, which this repair "
            f"does not support")
    if node.returns is None or ast.unparse(node.returns) == "None":
        raise RepairRefused(f"{node.name} returns nothing; a method graded on "
                            f"its side effects is not an input-repair case")
    return node.name, parameter.arg, declaration


def _java_signature(question):
    source = language_readiness.boilerplate_for(question, "java") or ""
    methods = _JAVA_METHOD.findall(source)
    if len(methods) != 1:
        raise RepairRefused(
            f"the Java starter declares {len(methods)} public method(s); the "
            f"harness calls the first one reflection returns, which is "
            f"unspecified unless there is exactly one")
    _return_type, name, parameter_text = methods[0]
    java_types = []
    for parameter in filter(None, (p.strip() for p in parameter_text.split(","))):
        tokens = [t for t in parameter.split() if t != "final"]
        if len(tokens) != 2:
            raise RepairRefused(f"the Java parameter {parameter!r} is not "
                                f"`Type name`")
        java_types.append(tokens[0])
    return name, tuple(java_types)


def _javascript_method(question):
    source = language_readiness.boilerplate_for(question, "javascript") or ""
    match = _JS_PROTOTYPE_METHOD.search(source)
    if match:
        return match.group(1)
    if re.search(r"\bclass\s+Solution\b", source):
        for name in _JS_CLASS_METHOD.findall(source):
            if name not in _JS_NOT_METHODS:
                return name
    raise RepairRefused("the JavaScript starter names no Solution method")


def served_languages(question):
    """
    The reflection languages a learner can submit in.

    Refuses when a self-contained language (C, C++) is served: its programs
    parse stdin themselves, so changing the representation would change what
    THEIR code must read — a contract change for them, not a repair.
    """
    served = []
    for lang in languages.REGISTRY:
        if not language_readiness.assess(question, lang.key).ready:
            continue
        if lang.self_contained:
            raise RepairRefused(
                f"{lang.label} is served for this question and reads stdin "
                f"itself; changing the representation would change its "
                f"contract")
        served.append(lang.key)
    if "python" not in served:
        raise RepairRefused("Python is not served; it declares the contract")
    return tuple(sorted(served, key=REFLECTION_LANGUAGES.index))


def signature_of(question, served):
    """The graded signature, agreed by every served language's starter."""
    method, parameter, declaration = _python_signature(question)
    java_types = ()
    if "java" in served:
        java_method, java_types = _java_signature(question)
        if java_method != method:
            raise RepairRefused(
                f"the Java starter grades {java_method}, the Python starter "
                f"{method}; the starters disagree on the method")
        mapped = tuple(JAVA_TYPES.get(t) for t in java_types)
        if mapped != (declaration,):
            raise RepairRefused(
                f"the Java starter declares {java_types} for a Python "
                f"{declaration}; the starters disagree on the parameter")
    if "javascript" in served:
        js_method = _javascript_method(question)
        if js_method != method:
            raise RepairRefused(
                f"the JavaScript starter grades {js_method}, the Python "
                f"starter {method}; the starters disagree on the method")
    return Signature(method, parameter, declaration, java_types)


# ── the plan ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CasePlan:
    case: int                 # 1-based, as remediate_inputs numbers cases
    before: str
    after: str
    value: object             # the ONE argument the case means
    decoder: str
    bound_before: tuple       # ((language, binds the value before), ...)

    @property
    def changes(self):
        return self.before != self.after


@dataclass(frozen=True)
class RepairPlan:
    question_id: int
    signature: Signature
    languages: tuple
    cases: tuple

    def changes(self):
        """The changes file this plan implies, in remediate_inputs' format."""
        return [{"case": c.case, "before": c.before, "after": c.after}
                for c in self.cases if c.changes]


def plan(question):
    """
    The repair a question needs, or `RepairRefused` saying why it is not one.

    Every gate below is a reason the question is NOT a v1 input-repair case;
    none of them is advisory.
    """
    version = execution_contract.contract_version(question)
    if version != CONTRACT:
        raise RepairRefused(
            f"the question is on contract {version}; this repair reads and "
            f"writes {CONTRACT} only, and changing the contract is a different "
            f"action class")

    from groups.services import wrapper_for

    for lang in languages.REGISTRY:
        if wrapper_for(question, lang.key) is not None:
            raise RepairRefused(
                f"{lang.label} is graded by the question's own wrapper, which "
                f"defines its own input format")

    served = served_languages(question)
    signature = signature_of(question, served)

    suite = question.hidden_test_cases
    if not isinstance(suite, list) or not suite:
        raise RepairRefused("the question has no hidden test cases")
    for index, case in enumerate(suite, start=1):
        if not (isinstance(case, dict)
                and isinstance(case.get("stdin"), str)
                and isinstance(case.get("expected_output"), str)):
            raise RepairRefused(f"case {index} is not text stdin and text "
                                f"expected_output")

    _require_input_defect(question, suite)

    cases = []
    for index, case in enumerate(suite, start=1):
        before = case["stdin"]
        prepared = _prepared(question, "python", before)
        value, decoder = decode_field(prepared, signature.declaration)
        bound_before = tuple(
            (lang, binds(question, before, lang, signature, [value]))
            for lang in served)
        if all(ok for _lang, ok in bound_before):
            after = before
        else:
            after = canonical_stdin(value, signature.declaration)
        cases.append(CasePlan(index, before, after, value, decoder,
                              bound_before))

    repair = RepairPlan(question.pk, signature, served, tuple(cases))
    for case_plan in repair.cases:
        check_languages(question, repair, case_plan, case_plan.after)
    _require_admitted_after(question, repair)
    if not repair.changes():
        raise RepairRefused("every case already binds as intended in every "
                            "served language; there is nothing to repair")
    return repair


def _require_input_defect(question, suite):
    """
    The candidate-class gate: the question must actually be in the class.

    At least one case must be refused by Python's v1 admission — the contract
    language — or there is no input defect for this module to repair.
    """
    refused = [index for index, case in enumerate(suite, start=1)
               if _check(question, case, "python").refused]
    if not refused:
        raise RepairRefused(
            "no case is refused by Python's v1 admission; the question is not "
            "an input-repair candidate")


def _check(question, case, language):
    return next(c for c in suite_admission.check_case(question, case)
                if c.language == language)


def _require_admitted_after(question, repair):
    """
    After the repair every case must be ADMITTED in every served language.

    This is what stops an answer-form defect riding along: a question whose
    expected outputs are also wrong in form stays refused after its inputs are
    repaired, and belongs to a different action class.
    """
    view = types.SimpleNamespace(
        pk=question.pk,
        execution_contract_version=question.execution_contract_version,
        boilerplate_code=question.boilerplate_code,
        hidden_wrapper_code=question.hidden_wrapper_code,
        hidden_test_cases=[dict(case, stdin=c.after) for case, c
                           in zip(question.hidden_test_cases, repair.cases)])
    for case_plan, case in zip(repair.cases, view.hidden_test_cases):
        for language in repair.languages:
            verdict = _check(view, case, language)
            if verdict.refused:
                raise RepairRefused(
                    f"case {case_plan.case} is still refused in {language} "
                    f"after its input is repaired ({verdict.detail[:120]}); "
                    f"the question has a defect this repair does not own")


def check_languages(question, repair, case_plan, after):
    """
    `after` must bind case_plan.value in every served language.

    A language that received the intended value BEFORE and would not after is
    a regression, reported as one; a language that did not before and still
    does not is a repair that does not reach it. Both refuse.
    """
    bound_after = {lang: binds(question, after, lang, repair.signature,
                               [case_plan.value])
                   for lang in repair.languages}
    for lang, ok_before in case_plan.bound_before:
        if ok_before and not bound_after[lang]:
            raise RepairRefused(
                f"case {case_plan.case}: language regression in {lang}: it "
                f"receives the intended value today and would not after")
    for lang in repair.languages:
        if not bound_after[lang]:
            raise RepairRefused(
                f"case {case_plan.case}: {lang} would not receive "
                f"{case_plan.value!r} after the repair")


# ── checks a command runs against what it is about to write ───────────────

def check_changes(question, repair, changes):
    """
    The approved changes must be EXACTLY what the plan derives.

    In order, so the refusal names the real problem: the approval must cover
    the same cases; each `before` must be what is stored; each `after` must
    mean the same value as THAT case's stored input (a value moved between
    cases is a reorder); it must not regress or miss a served language; and it
    must be the canonical text, not merely an equivalent one.
    """
    planned = {c.case: c for c in repair.cases if c.changes}
    named = [entry["case"] for entry in changes]
    if sorted(named) != sorted(planned):
        raise RepairRefused(
            f"the approval names cases {sorted(named)} but the plan changes "
            f"cases {sorted(planned)}")
    for entry in changes:
        case_plan = planned[entry["case"]]
        if entry["before"] != case_plan.before:
            raise RepairRefused(
                f"case {case_plan.case}: the approval was written against "
                f"{entry['before']!r}, but the case stores "
                f"{case_plan.before!r}")
        python_now = bound_values(question, entry["after"], "python",
                                  repair.signature)
        if not same_values(python_now, [case_plan.value]):
            raise RepairRefused(
                f"case {case_plan.case}: value mismatch: the approved input "
                f"means {python_now!r} but this case's stored input means "
                f"{[case_plan.value]!r}; the cases are reordered or edited")
        check_languages(question, repair, case_plan, entry["after"])
        if entry["after"] != case_plan.after:
            raise RepairRefused(
                f"case {case_plan.case}: the approved input "
                f"{entry['after']!r} is not the canonical v1 representation "
                f"{case_plan.after!r}")


def check_suite_transition(question, repair, before_suite, after_suite):
    """
    `after_suite` must be `before_suite` with ONLY the planned stdin changed.

    Run against the proposal before a write and against what landed after it.
    Same count, same order, every expected output byte-identical, every other
    key identical, and each stdin exactly the plan's — read back through the
    harness to the same value, so a reordered suite cannot pass by holding the
    right set of texts in the wrong places.
    """
    if not (isinstance(before_suite, list) and isinstance(after_suite, list)):
        raise RepairRefused("a suite is not a list")
    if not len(before_suite) == len(after_suite) == len(repair.cases):
        raise RepairRefused(
            f"case count changed: {len(before_suite)} before, "
            f"{len(after_suite)} after, {len(repair.cases)} planned")
    for case_plan, was, now in zip(repair.cases, before_suite, after_suite):
        index = case_plan.case
        if not isinstance(was, dict) or not isinstance(now, dict):
            raise RepairRefused(f"case {index} is not an object")
        if was.get("expected_output") != now.get("expected_output") or \
                type(was.get("expected_output")) is not type(now.get("expected_output")):
            raise RepairRefused(
                f"case {index}: expected output changed from "
                f"{was.get('expected_output')!r} to "
                f"{now.get('expected_output')!r}; an input repair never "
                f"touches an answer")
        if set(was) != set(now) or any(not _same_json(was[key], now[key])
                                       for key in was if key != "stdin"):
            raise RepairRefused(f"case {index}: a key other than stdin changed")
        if was.get("stdin") != case_plan.before:
            raise RepairRefused(
                f"case {index}: stored stdin {was.get('stdin')!r} is not the "
                f"planned before {case_plan.before!r}")
        if now.get("stdin") != case_plan.after:
            raise RepairRefused(
                f"case {index}: stdin {now.get('stdin')!r} is not the planned "
                f"after {case_plan.after!r}; cases moved or were edited")
        if not binds(question, now["stdin"], "python", repair.signature,
                     [case_plan.value]):
            raise RepairRefused(
                f"case {index}: the written input no longer means "
                f"{case_plan.value!r}")


# ── the title/signature review ────────────────────────────────────────────

def statement_digest(question):
    return hashlib.sha256((question.content or "").encode("utf-8")).hexdigest()


def review_fingerprint(question, repair):
    """What a reviewer attests to: the live title, method and statement."""
    return {"title": (question.title or "").strip(),
            "method": repair.signature.method,
            "statement_sha256": statement_digest(question)}


def check_review(question, repair, review):
    """
    A human attested that the title, statement and signature describe ONE
    problem — and attested to THIS title, method and statement.

    A question whose title names a different problem than its signature
    (q423 "Reconstruct Original Digits" grading `sortWordsByNumbers`) is not
    repaired: fixing how its input is spelled would make a wrong question
    gradable. The verdict is human; this check only refuses to proceed
    without it, or with one written against a question that has since moved.
    """
    if not isinstance(review, dict):
        raise RepairRefused("no title/signature review was recorded")
    verdict = review.get("verdict")
    if verdict != REVIEW_CONSISTENT:
        raise RepairRefused(
            f"the title/signature review recorded {verdict!r}; only a "
            f"question reviewed {REVIEW_CONSISTENT} is repaired")
    live = review_fingerprint(question, repair)
    for key, value in live.items():
        if review.get(key) != value:
            raise RepairRefused(
                f"the review attests {key} {review.get(key)!r}, but the "
                f"question now has {value!r}; review it again")
