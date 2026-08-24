"""
The shared execution adapter (M2 P2.7 follow-up).

ONE canonical transformation, used by grading and by the oracle:

    stored stdin  ->  declared signature  ->  typed arguments  ->  invocation

PURE. Standard library only — no ORM, no Django, no I/O, no clock. Both
`GradingService` and `OracleService` reach execution through
`GradingService._build_executable` and `prepare_stdin`, so there is exactly one
parser and it is this one. There is no separate oracle path to drift from.

── Why no wrapper template changes ─────────────────────────────────────────

The obvious reading of the 835 arity defect is that `GENERIC_PYTHON_WRAPPER`
must be rewritten. It does not.

The wrapper already contains a complete calling convention that nothing was
targeting deliberately:

    elif isinstance(parsed_input, list):
        res = target_method(*parsed_input)

A JSON array is splatted positionally. EVERY argument list can be written as
one — including the empty list, a single list argument (`[[1,2,3]]`), a single
string that looks like a number (`["110"]`), and a dict passed positionally
(`[{"a":1}]`). Verified by executing the real template against all sixteen
signature classes below, not by reading it.

So the contract-aware work happens HERE, in pure server-side Python, and the
templates stay byte-for-byte unchanged. That matters: v1 is the harness every
one of the ~2,900 production questions was graded under, and editing it would
silently re-grade all of them at once.

── The eight signature classes ─────────────────────────────────────────────

    A  one list parameter      [[1,2,3]]        ONE argument, not three
    B  multiple scalars        [6, 7]
    C  one scalar              [42]
    D  string parameters       ["110"]          characters, never a number
    E  variadic *args          refused — no arity can be derived
    F  mixed scalar and list   ["x", [1,2]]
    G  keyword-only            refused — names come from the input, not stdin
    H  zero arguments          []

Class A is the 835-question defect: `[1,8,6,...]` was splatted into nine
arguments for a one-parameter function. Class D is the 20-question defect.

── What it refuses ─────────────────────────────────────────────────────────

It never guesses. When stored stdin cannot be mapped onto the declared
signature the result is a refusal carrying a reason, not a best effort — a
plausible wrong invocation produces a confidently wrong answer key, which is
the failure this milestone exists to prevent.
"""

import ast
import json

# ── Outcomes ────────────────────────────────────────────────────────────────

OK = "OK"

#: Stored stdin contradicts the declared signature — wrong arity, or a value
#: whose type the signature rules out. The question needs a human.
CONTRACT_MISMATCH = "CONTRACT_MISMATCH"

#: The stored test case is not structurally usable at all: `stdin` or
#: `expected_output` is not text. Distinct from a mismatch — nothing was even
#: parseable enough to compare against the signature.
INVALID_INPUT = "INVALID_INPUT"

#: No declared contract to check against (unannotated, variadic, or more than
#: one public method). Not an accusation; an admission.
NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


# ── Declared types ──────────────────────────────────────────────────────────

TEXT = "text"
INTEGER = "integer"
FLOAT = "float"
BOOLEAN = "boolean"
SEQUENCE = "sequence"
MAPPING = "mapping"
UNDECLARED = "undeclared"

_MAPPING_HINTS = ("dict", "mapping", "map[")
_SEQUENCE_HINTS = ("list", "sequence", "tuple", "iterable", "array", "set[")
_TEXT_HINTS = ("str", "string", "text")
_BOOLEAN_HINTS = ("bool",)
_FLOAT_HINTS = ("float", "double", "decimal")
_INTEGER_HINTS = ("int", "integer", "long")


def classify_annotation(annotation):
    """
    The declared kind for one annotation, as lower-cased text.

    Order is load-bearing. Containers are checked before their element types,
    or `list[str]` reads as a string; `bool` is checked before `int` because
    Python's bool IS an int and the looser test would swallow it.
    """
    text = (annotation or "").lower()
    if not text:
        return UNDECLARED
    if any(hint in text for hint in _MAPPING_HINTS):
        return MAPPING
    if any(hint in text for hint in _SEQUENCE_HINTS):
        return SEQUENCE
    if any(hint in text for hint in _BOOLEAN_HINTS):
        return BOOLEAN
    if any(hint in text for hint in _TEXT_HINTS):
        return TEXT
    if any(hint in text for hint in _FLOAT_HINTS):
        return FLOAT
    if any(hint in text for hint in _INTEGER_HINTS):
        return INTEGER
    return UNDECLARED


def element_kind(annotation):
    """The declared kind of a container's elements: `list[str]` -> TEXT."""
    text = (annotation or "").lower()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return UNDECLARED
    return classify_annotation(text[start + 1:end].split(",")[0].strip())


# ── Reading the declared signature ──────────────────────────────────────────
#
# Single source of truth: `contract_impact` imports these rather than parsing
# starter code a second time. Two parsers that agree today are two parsers that
# disagree after the next edit.

def chosen_function(source):
    """
    (node, is_method) — the function the wrapper would actually call.

    Mirrors `dir(sol)[0]`: a class's first public method in ALPHABETICAL order,
    not source order. Reproduced rather than corrected, because the adapter has
    to bind arguments to the function that really runs.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None, False

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if classes:
        methods = sorted(
            (node for node in classes[0].body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and not node.name.startswith("_")),
            key=lambda node: node.name)
        if methods:
            return methods[0], True

    functions = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if functions:
        return functions[0], False
    return None, False


def public_method_names(source):
    """The public methods the wrapper could pick between, alphabetically."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if not classes:
        return []
    return sorted(
        node.name for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_"))


def declared_signature(source):
    """(name, [(parameter, annotation_text)]), or None if nothing is callable."""
    node, is_method = chosen_function(source)
    if node is None:
        return None
    arguments = list(node.args.args)
    if is_method and arguments and arguments[0].arg in ("self", "cls"):
        arguments = arguments[1:]
    return node.name, [(argument.arg, _annotation_text(argument.annotation))
                       for argument in arguments]


def accepts_variable_arity(source):
    """Whether the chosen function takes `*args` or `**kwargs` (class E)."""
    node, _is_method = chosen_function(source)
    return bool(node is not None and (node.args.vararg or node.args.kwarg))


def has_keyword_only_parameters(source):
    """Class G. Their names come from the input, so stdin cannot supply them."""
    node, _is_method = chosen_function(source)
    return bool(node is not None and node.args.kwonlyargs)


def _annotation_text(annotation):
    if annotation is None:
        return ""
    try:
        return ast.unparse(annotation).lower()
    except Exception:
        return ""


# ── The invocation ──────────────────────────────────────────────────────────

class Invocation:
    """
    The arguments to call with, or a refusal explaining why there are none.

    Deliberately not a bare list: "no arguments" and "we could not work out the
    arguments" are different facts, and a caller that cannot tell them apart
    will eventually execute the wrong one.
    """

    __slots__ = ("outcome", "arguments", "detail", "warnings")

    def __init__(self, outcome, arguments=(), detail="", warnings=()):
        self.outcome = outcome
        self.arguments = list(arguments)
        self.detail = detail
        self.warnings = tuple(warnings)

    @property
    def ok(self):
        return self.outcome == OK

    def envelope(self):
        """
        Canonical stdin: a JSON array of the positional arguments.

        This is the whole mechanism. The unchanged wrapper splats it, so the
        array's LENGTH is the arity and its ELEMENTS carry their own types —
        no shape guessing survives the trip.
        """
        if not self.ok:
            raise ValueError(f"no invocation to render: {self.outcome} "
                             f"({self.detail})")
        return json.dumps(self.arguments, ensure_ascii=False,
                          separators=(",", ":"))

    def __eq__(self, other):
        return (isinstance(other, Invocation)
                and self.outcome == other.outcome
                and self.arguments == other.arguments
                and self.detail == other.detail
                and self.warnings == other.warnings)

    def __repr__(self):
        if self.ok:
            return f"Invocation(OK, {self.arguments!r}, warnings={self.warnings!r})"
        return f"Invocation({self.outcome}, {self.detail!r})"


def _refuse(outcome, detail):
    return Invocation(outcome, detail=detail)


# ── Input adapter ───────────────────────────────────────────────────────────

def build_invocation(stdin, source):
    """
    The canonical invocation for one stored stdin against one starter's
    signature.

    `stdin` is the stored test-case input; `source` is the question's Python
    starter code, which IS the declared contract — it is what the learner is
    handed and what the wrapper will call.
    """
    if not isinstance(stdin, str):
        return _refuse(
            INVALID_INPUT,
            f"stdin is {type(stdin).__name__}, not text; nothing can be parsed "
            f"from it")

    if accepts_variable_arity(source):
        return _refuse(NEEDS_MANUAL_REVIEW,
                       "starter takes *args/**kwargs, so no arity is declared")
    if has_keyword_only_parameters(source):
        return _refuse(NEEDS_MANUAL_REVIEW,
                       "starter has keyword-only parameters, which positional "
                       "stdin cannot supply")

    signature = declared_signature(source)
    if signature is None:
        return _refuse(NEEDS_MANUAL_REVIEW,
                       "no callable found in the starter code")

    _name, parameters = signature
    expanded = stdin.replace("\\n", "\n")
    fields = _split_fields(expanded, len(parameters))
    if fields is None:
        return _refuse(
            CONTRACT_MISMATCH,
            f"stored stdin cannot be mapped onto {len(parameters)} declared "
            f"parameter(s)")

    arguments, warnings = [], []
    for (name, annotation), field in zip(parameters, fields):
        value, problem = _coerce(field, annotation, warnings)
        if problem:
            return _refuse(CONTRACT_MISMATCH, f"parameter {name!r}: {problem}")
        arguments.append(value)

    return Invocation(OK, arguments, warnings=tuple(warnings))


class _Raw(str):
    """A field still in its stored text form, not yet a decoded JSON value."""


def _split_fields(stdin, arity):
    """
    `arity` fields, or None when stdin cannot supply exactly that many.

    Order matters and each rule earns its place:

      arity 0  stdin must be blank; anything else contradicts the signature
      arity 1  the WHOLE input is the one field — never split, so a multi-line
               string argument survives intact
      else     exactly N non-blank lines, else a JSON array of exactly N
               elements, else N whitespace tokens

    The line rule precedes the JSON rule because `2 7 11 15\\n9` is the common
    stored form for two parameters and is not valid JSON at all.
    """
    if arity == 0:
        return [] if not stdin.strip() else None

    if arity == 1:
        return [_Raw(stdin)]

    lines = [line for line in stdin.split("\n") if line.strip() != ""]
    if len(lines) == arity:
        return [_Raw(line) for line in lines]

    try:
        decoded = json.loads(stdin)
    except Exception:
        decoded = None
    if isinstance(decoded, list) and len(decoded) == arity:
        return list(decoded)

    tokens = stdin.split()
    if len(tokens) == arity:
        return [_Raw(token) for token in tokens]

    return None


def _coerce(field, annotation, warnings):
    """(value, problem). `problem` is a string when the field contradicts the
    declared type — never a silent repair."""
    kind = classify_annotation(annotation)

    if not isinstance(field, _Raw):
        # Already a decoded JSON value from the array path. Validate it against
        # the declaration rather than re-reading it; the stored data made a
        # type claim and the signature either permits it or does not.
        return _validate_decoded(field, kind, annotation)

    text = str(field)

    if kind == TEXT:
        # The characters, untouched. No strip, no unquote, no case change —
        # a question about trailing spaces is graded on the bytes it stored.
        return text, None

    if kind == UNDECLARED:
        warnings.append("undeclared_parameter_type")
        return _guess(text), None

    stripped = text.strip()

    if kind == INTEGER:
        try:
            return int(stripped), None
        except ValueError:
            return None, f"{stripped!r} is not an integer"

    if kind == FLOAT:
        try:
            return float(stripped), None
        except ValueError:
            return None, f"{stripped!r} is not a number"

    if kind == BOOLEAN:
        # Both spellings accepted: inputs were authored by humans and LLMs, and
        # `True` here is a spelling difference, not a type claim. Output casing
        # is held strictly — see `canonical_output`.
        if stripped in ("true", "True"):
            return True, None
        if stripped in ("false", "False"):
            return False, None
        return None, f"{stripped!r} is not a boolean"

    if kind == SEQUENCE:
        return _coerce_sequence(stripped, annotation)

    if kind == MAPPING:
        try:
            decoded = json.loads(stripped)
        except Exception:
            return None, f"{stripped[:40]!r} is not a JSON object"
        if not isinstance(decoded, dict):
            return None, f"expected an object, stored value is a " \
                         f"{type(decoded).__name__}"
        return decoded, None

    return _guess(text), None


def _coerce_sequence(stripped, annotation):
    try:
        decoded = json.loads(stripped)
    except Exception:
        decoded = None

    if isinstance(decoded, list):
        return decoded, None

    # Anything else falls through to tokens, INCLUDING a JSON scalar. `5` and
    # `2 7 11 15` are both stored forms for the same one-list parameter, and
    # refusing the first while accepting the second would treat two cases of
    # one question under different rules. A token that cannot hold the declared
    # element type is still refused below.
    #
    # The other stored convention is whitespace-separated tokens, which is
    # exactly the form that defeated the v1 wrapper.
    tokens = stripped.split()
    if not tokens:
        return [], None
    wanted = element_kind(annotation)
    values = []
    for token in tokens:
        if wanted == TEXT:
            values.append(token)
        elif wanted == INTEGER:
            try:
                values.append(int(token))
            except ValueError:
                return None, f"element {token!r} is not an integer"
        elif wanted == FLOAT:
            try:
                values.append(float(token))
            except ValueError:
                return None, f"element {token!r} is not a number"
        else:
            values.append(_guess(token))
    return values, None


def _validate_decoded(value, kind, annotation):
    """A JSON-decoded field checked against the declaration, never coerced."""
    if kind == UNDECLARED:
        return value, None
    if kind == TEXT:
        if isinstance(value, str):
            return value, None
        return None, (f"declared text but the stored value is a "
                      f"{type(value).__name__}")
    if kind == BOOLEAN:
        return (value, None) if isinstance(value, bool) else (
            None, f"declared boolean but stored a {type(value).__name__}")
    if kind == INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"declared integer but stored a {type(value).__name__}"
        return value, None
    if kind == FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"declared number but stored a {type(value).__name__}"
        return float(value), None
    if kind == SEQUENCE:
        if not isinstance(value, list):
            return None, f"declared sequence but stored a {type(value).__name__}"
        wanted = element_kind(annotation)
        if wanted == TEXT and any(not isinstance(v, str) for v in value):
            return None, "declared a sequence of text but stored a non-text element"
        if wanted == INTEGER and any(
                isinstance(v, bool) or not isinstance(v, int) for v in value):
            return None, "declared a sequence of integers but stored otherwise"
        return value, None
    if kind == MAPPING:
        return (value, None) if isinstance(value, dict) else (
            None, f"declared mapping but stored a {type(value).__name__}")
    return value, None


def _guess(token):
    """
    Legacy shape guessing, for an UNDECLARED parameter only.

    Preserved rather than improved: with no declared type there is nothing
    better to go on, and questions reaching this path are reported as
    NEEDS_MANUAL_REVIEW rather than quietly executed as if they were fine.
    """
    stripped = token.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    if stripped == "null":
        return None
    return token


# ── Output adapter ──────────────────────────────────────────────────────────

def canonical_output(value):
    """
    The canonical printed form of a produced value.

    Booleans are LOWERCASE, matching `str(res).lower()` in the shipped wrapper.
    That is the future rule, and it is why expected outputs stored as
    `True`/`False` are non-canonical: the fix is to migrate that data, not to
    loosen the comparison, because loosening it would also make `True` match a
    genuinely wrong `true` from a different code path.

    Containers are compact JSON. Note the shipped wrapper prints
    `json.dumps(res).replace(" ", "")`, which also strips spaces INSIDE
    strings — `["a b"]` becomes `["ab"]`. Using `separators` removes the
    padding without corrupting the content.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def normalise_for_comparison(text):
    """
    Whitespace-only normalisation, applied to both sides before comparing.

    Line endings are unified and trailing whitespace is removed per line.
    Nothing else: case is preserved, internal spacing is preserved, and `1` is
    not equal to `1.0`. A comparator that is looser than the question is a
    comparator that marks wrong answers correct, which is worse than the bug it
    was meant to paper over.
    """
    if not isinstance(text, str):
        text = canonical_output(text)
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in unified.split("\n")).strip("\n")


def outputs_match(expected, actual):
    """Whether a produced output counts as the expected one."""
    return (normalise_for_comparison(expected)
            == normalise_for_comparison(actual))


def is_canonical_output(text):
    """
    Whether a stored expected output is already in canonical form.

    `True`/`False` are not: they can never be produced by the wrapper, so a
    question storing them fails every submission.
    """
    return isinstance(text, str) and text.strip() not in ("True", "False")


# ── Safe access to stored test cases ────────────────────────────────────────

def read_test_case(case):
    """
    (stdin, expected_output, problem) for one stored case, without assuming
    either field is text.

    `GradingService.grade` called `.strip()` on both. 48 production questions
    store a list in one of them, so the grader raised AttributeError and the
    learner received no verdict at all — not a wrong answer, an error page.
    Returning the problem lets the caller refuse deliberately instead.
    """
    if not isinstance(case, dict):
        return None, None, f"test case is a {type(case).__name__}, not an object"

    stdin = case.get("stdin")
    expected = case.get("expected_output")

    if stdin is None:
        stdin = ""
    if expected is None:
        expected = ""

    if not isinstance(stdin, str):
        return None, None, (f"stdin is a {type(stdin).__name__}, not text; "
                            f"the stored test case cannot be executed as written")
    if not isinstance(expected, str):
        return None, None, (f"expected_output is a {type(expected).__name__}, "
                            f"not text; there is nothing to compare against")
    return stdin, expected, None
