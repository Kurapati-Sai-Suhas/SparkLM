"""
Language-agnostic output classification (M2 P2.5, Phase 7).

The approved comparison model is ONE canonical oracle language per problem,
with learners still submitting in any of the five supported languages. That
only works if a problem's output means the same thing in all of them, because
one `expected_output` string is compared against every learner's stdout using
whitespace normalization alone.

Some outputs simply do not survive that. The same correct logic prints:

    booleans   True (python) / true (java, javascript) / 1 (c, cpp)
    floats     0.30000000000000004 (python) / 0.300000 (printf "%f")
    lists      [1, 2] (python repr) / requires manual formatting elsewhere
    nulls      None / null / nullptr / (empty)

A problem whose answers look like these cannot be graded fairly across
languages today, and no amount of test generation fixes it — the contract is
wrong, not the tests.

This module classifies problems from the EVIDENCE ACTUALLY PRESENT: the
expected outputs stored in `hidden_test_cases`, plus any oracle output the
caller has obtained. It deliberately does not keyword-match the problem
statement — "return true if..." in prose says nothing about what the program
prints, and rejecting on that would flag correct problems while missing the
ones whose statement is silent.

It classifies. It never edits a problem.
"""

import re

LANGUAGE_AGNOSTIC = "LANGUAGE_AGNOSTIC"
REQUIRES_REVIEW = "REQUIRES_REVIEW"
UNSUPPORTED_FOR_CURRENT_CONTRACT = "UNSUPPORTED_FOR_CURRENT_CONTRACT"
UNKNOWN = "UNKNOWN"

# ── Shapes that survive every language unchanged ──────────────────────────
#
# An integer prints identically in all five. So does a bare token, and so does
# whitespace-separated integer output, which is the house style the generation
# prompt already asks for ("0 1" for Two Sum rather than "[0, 1]").
_INTEGER = re.compile(r"^[+-]?\d+$")
_INTEGER_SEQUENCE = re.compile(r"^[+-]?\d+(?:[ \t]+[+-]?\d+)*$")
# Canonical uppercase tokens are a deliberate product convention: a problem
# that specifies YES/NO is language-agnostic precisely because it does not
# rely on any language's boolean printing.
_CANONICAL_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]*$")
# Plain words/identifiers with no delimiters that imply a language's repr.
_PLAIN_TEXT = re.compile(r"^[A-Za-z0-9 _.,:;!?'\-]+$")

# ── Shapes that do NOT ────────────────────────────────────────────────────
_LOWER_BOOLEAN = re.compile(r"^(true|false)$", re.IGNORECASE)
_FLOAT = re.compile(r"^[+-]?\d*\.\d+(?:[eE][+-]?\d+)?$")
_NULLISH = re.compile(r"^(none|null|nil|nullptr|undefined)$", re.IGNORECASE)
_BRACKETED = re.compile(r"^[\[\({].*[\]\)}]$", re.S)


def classify_line(line):
    """
    (is_agnostic, reason) for one line of expected output. `reason` is None
    when the line is fine.
    """
    text = line.strip()
    if not text:
        return True, None

    if _INTEGER.match(text) or _INTEGER_SEQUENCE.match(text):
        return True, None
    if _CANONICAL_TOKEN.match(text):
        return True, None

    if _FLOAT.match(text):
        return False, (
            f"{text!r} is a floating-point value; formatting differs by "
            f"language (python repr vs printf %f) and no tolerance comparator "
            f"is permitted under the current contract"
        )
    if _LOWER_BOOLEAN.match(text):
        return False, (
            f"{text!r} is a boolean literal; python prints True/False, "
            f"java and javascript true/false, c and cpp 1/0"
        )
    if _NULLISH.match(text):
        return False, (
            f"{text!r} is a null literal; every language spells it differently"
        )
    if _BRACKETED.match(text):
        return False, (
            f"{text!r} uses a bracketed container representation; this is a "
            f"language's repr, not a specified output format"
        )
    if _PLAIN_TEXT.match(text):
        return True, None

    return False, (
        f"{text!r} contains characters whose textual representation is not "
        f"obviously identical across languages"
    )


def classify_outputs(outputs):
    """
    (status, reasons) for a collection of expected-output strings.

    UNKNOWN when there is nothing to judge — an absent verdict, never an
    optimistic one.
    """
    concrete = [o for o in outputs if isinstance(o, str)]
    if not concrete:
        return UNKNOWN, ["no expected outputs to classify"]

    reasons = []
    for output in concrete:
        for line in output.splitlines() or [output]:
            ok, reason = classify_line(line)
            if not ok and reason not in reasons:
                reasons.append(reason)

    if not reasons:
        return LANGUAGE_AGNOSTIC, []

    # Floats and nulls have no canonical textual form a learner could be
    # expected to reproduce in five languages; the problem needs restating,
    # not re-testing. Everything else is a judgement call for a human.
    unsupported = any(
        "floating-point" in reason or "null literal" in reason
        for reason in reasons
    )
    return (UNSUPPORTED_FOR_CURRENT_CONTRACT if unsupported else REQUIRES_REVIEW), reasons


def classify_question(question, oracle_outputs=None):
    """
    (status, reasons) for a question, from its stored expected outputs plus
    any oracle output the caller has already obtained.

    Oracle output is included because it is the stronger evidence: it is what
    the canonical solution ACTUALLY prints, whereas a stored expected output
    is only what someone claimed it prints.
    """
    cases = question.hidden_test_cases
    stored = []
    if isinstance(cases, list):
        stored = [
            case.get("expected_output") for case in cases
            if isinstance(case, dict)
        ]

    return classify_outputs(list(stored) + list(oracle_outputs or []))
