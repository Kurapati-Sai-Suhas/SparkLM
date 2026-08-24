"""
Declared-type-aware argument coercion (M2 P2.7 contract remediation).

PURE. No ORM, no Django, no I/O. One function, used by the executor wrapper so
`GradingService` and `OracleService` cannot diverge — they both build their
executable through `GradingService._build_executable`, so a single wrapper
template is the only contract either of them can run.

── The defect this exists to fix ───────────────────────────────────────────

Both shipped contracts coerce stdin by SHAPE and ignore the type the question
declares:

    stdin      v1 (json.loads)   v2 (int/float/bool)   declared `s: str`
    "110"      int 110           int 110               "110"
    "000"      "000"             int 0                 "000"
    "0"        int 0             int 0                 "0"
    "true"     True              True                  "true"
    "1.0"      float 1.0         float 1.0             "1.0"
    "007"      "007"             int 7                 "007"

Measured, not assumed. Note `000` and `007`: **v2 is WORSE than v1 there** — it
destroys leading zeros that v1 happened to preserve, because JSON rejects them
while `int()` accepts them. So "migrate everything to v2" is not a remediation;
for those questions it is a regression.

── The rule ────────────────────────────────────────────────────────────────

The learner's signature is the contract. `s: str` means the solution receives
the characters that were stored, unmodified. A parameter with no annotation
keeps the legacy shape-guessing, because there is nothing better to go on and
silently changing it would alter questions nobody has reviewed.

── Why this does not change any existing question ──────────────────────────

Nothing here is wired into v1 or v2. Both remain frozen: v1 is the contract
~1,782 production questions were graded under, and rewriting it would silently
change grading for all of them at once. This module is consumed only by a new
contract that **zero questions currently declare**. Correct semantics become
AVAILABLE; nothing adopts them until a question is deliberately migrated with
oracle verification.
"""

#: Annotation spellings that mean "give me the characters, unchanged".
_TEXT_HINTS = ("str", "string", "text")

#: Annotation spellings that mean "give me a sequence".
_SEQUENCE_HINTS = ("list", "sequence", "tuple", "iterable", "array")


#: Spellings of "this parameter has no annotation". `inspect.Parameter.empty`
#: is the class `inspect._empty`, so it arrives as the NAME `_empty` rather
#: than through `str()` — both are listed because relying on `_empty` matching
#: no type hint is luck, not a guarantee, and a future annotation class whose
#: name happened to contain "str" would be read as a declared string.
_ABSENT = ("_empty", "empty")


def _annotation_text(annotation):
    """
    Lower-cased annotation, or "" when absent.

    Accepts a type, a string annotation (`from __future__ import annotations`),
    or `inspect.Parameter.empty`.
    """
    if annotation is None:
        return ""
    name = getattr(annotation, "__name__", None)
    text = str(name).lower() if name else str(annotation).lower()
    return "" if text in _ABSENT else text


def declares_text(annotation):
    """Whether the parameter asks for a string."""
    text = _annotation_text(annotation)
    if not text:
        return False
    # `list[str]` asks for a sequence, not a string: check sequence first, so a
    # container of strings is not mistaken for a string.
    if any(hint in text for hint in _SEQUENCE_HINTS):
        return False
    return any(hint in text for hint in _TEXT_HINTS)


def declares_sequence(annotation):
    text = _annotation_text(annotation)
    return bool(text) and any(hint in text for hint in _SEQUENCE_HINTS)


def coerce_token(token):
    """
    Legacy shape-guessing for an UNANNOTATED parameter.

    Order matters: `int` before `float` so "3" stays an int, and the boolean
    words are checked only as exact lowercase matches so a sentence containing
    "true" is not turned into `True`.
    """
    try:
        return int(token)
    except (TypeError, ValueError):
        pass
    try:
        return float(token)
    except (TypeError, ValueError):
        pass
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "null":
        return None
    return token


def coerce_argument(text, annotation):
    """
    One stdin line, as the declared type.

    `text` is passed through UNTOUCHED for a text parameter — no strip, no
    unquote, no case change. Whitespace inside a string input is data; a
    question about trailing spaces is graded on exactly the bytes it stored.
    """
    if declares_text(annotation):
        return text
    if declares_sequence(annotation):
        return [coerce_token(part) for part in text.split()]
    tokens = text.split()
    if len(tokens) == 1:
        return coerce_token(tokens[0])
    return [coerce_token(part) for part in tokens]


def render_output(value):
    """
    A produced value as the string the grader will compare.

    Booleans render LOWERCASE, matching both shipped contracts. That choice is
    load-bearing and is why 67 production questions storing "True"/"False" can
    never pass: `normalize_output` does not fold case. Changing the renderer to
    match those 67 would break the 153 that correctly store "true"/"false", so
    the renderer stays and the data is what needs repair — separately, and with
    a human deciding.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(render_output(item) for item in value)
    if value is None:
        return "null"
    return str(value)
