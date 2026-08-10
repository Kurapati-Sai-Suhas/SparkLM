"""
The hidden-test contract (M2 P2.5, Phase 6).

`Question.hidden_test_cases` is a JSONField with no schema enforcement at the
database layer, which is how the bank ended up holding rows that are not
objects, cases with no `expected_output`, and duplicate inputs padding a count.
This module is the single place that says what a valid case is, so the
validator, the future generation pipeline and the reconciliation report all
agree rather than each re-deriving it.

Deliberately NOT a TestCase model. The audit asked whether one was necessary
and the answer is no: cases are always read as a whole set (`GradingService`
iterates `question.hidden_test_cases` in order), never queried individually,
never filtered, never joined. A model would add a migration of every existing
row — real risk — to a phase whose entire purpose is making grading
trustworthy, and would buy nothing the validator does not already give. The
defect was never the storage; it was that nothing checked the contents.

If per-case resource policy is ever needed, that WOULD justify columns; adding
keys to these objects stays backward compatible until then.
"""

# ── Field contract ────────────────────────────────────────────────────────
#
# Required. `stdin` must be non-empty: the generation contract in ai_services
# already states "stdin must NEVER be empty", and a blank input is almost
# always a case that was never really written.
REQUIRED_FIELDS = ("stdin", "expected_output")

# Optional, ignored by grading, useful to humans and to the generator.
# `category` records WHY a case exists (boundary, adversarial, stress...), so
# a suite of twelve can be seen to cover twelve different failure modes rather
# than the same one twelve times.
OPTIONAL_FIELDS = ("explanation", "category", "source")

ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)

# Every value is a JSON string — numbers included. ai_services already
# mandates this ("quote numbers too") because an int here silently becomes
# `"5"` on one path and `5` on another, and the comparison is string-based.
STRING_FIELDS = frozenset(REQUIRED_FIELDS + ("explanation", "category", "source"))

#: Judge0 takes stdin base64-encoded in a JSON body, and the whole suite is
#: executed one HTTP call per case on a 0.1 vCPU instance. These bounds are
#: about keeping a single problem's suite servable, not about Judge0's own
#: limits, which are far higher.
MAX_STDIN_BYTES = 64 * 1024
MAX_EXPECTED_OUTPUT_BYTES = 64 * 1024

#: Coverage floor from the P2.5 contract. A FLOOR, not a target: a hard
#: problem may need 20 or 30, and twelve near-identical cases satisfy this
#: number while testing one thing. `category` is what makes that visible.
MIN_HIDDEN_TESTS = 12


class CaseProblem:
    """One contract violation, addressed to whoever has to fix it."""

    def __init__(self, index, message):
        self.index = index      # None => the problem is with the suite itself
        self.message = message

    def __str__(self):
        return (f"case {self.index + 1}: {self.message}" if self.index is not None
                else self.message)

    def __repr__(self):
        return f"<CaseProblem {self}>"


def validate_case(case, index):
    """Contract violations for a single case, in reading order."""
    problems = []

    if not isinstance(case, dict):
        return [CaseProblem(index, f"is {type(case).__name__}, not an object")]

    for field in REQUIRED_FIELDS:
        if field not in case:
            problems.append(CaseProblem(index, f"missing {field!r}"))

    for field, value in case.items():
        if field not in ALLOWED_FIELDS:
            problems.append(CaseProblem(index, f"unknown field {field!r}"))
        elif field in STRING_FIELDS and value is not None and not isinstance(value, str):
            # Not merely untidy: comparison is string-based, so an int here
            # compares unequal to the identical value read back as text.
            problems.append(CaseProblem(
                index, f"{field!r} is {type(value).__name__}, must be a string"
            ))

    stdin = case.get("stdin")
    if isinstance(stdin, str):
        if not stdin.strip():
            problems.append(CaseProblem(index, "'stdin' is empty"))
        if len(stdin.encode("utf-8")) > MAX_STDIN_BYTES:
            problems.append(CaseProblem(
                index, f"'stdin' exceeds {MAX_STDIN_BYTES} bytes"))

    expected = case.get("expected_output")
    if isinstance(expected, str) and len(expected.encode("utf-8")) > MAX_EXPECTED_OUTPUT_BYTES:
        problems.append(CaseProblem(
            index, f"'expected_output' exceeds {MAX_EXPECTED_OUTPUT_BYTES} bytes"))

    return problems


def validate_suite(cases, floor=MIN_HIDDEN_TESTS):
    """
    Contract violations for a whole suite: per-case problems plus the ones
    only visible across cases (duplicates, coverage).
    """
    if not isinstance(cases, list):
        return [CaseProblem(None, f"hidden_test_cases is "
                                  f"{type(cases).__name__}, not a list")]
    if not cases:
        return [CaseProblem(None, "no hidden tests")]

    problems = []
    for index, case in enumerate(cases):
        problems.extend(validate_case(case, index))

    # A duplicate input inflates the count toward the floor while testing
    # nothing new — the specific failure a careless bulk generator produces.
    seen = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        stdin = case.get("stdin")
        if not isinstance(stdin, str):
            continue
        if stdin in seen:
            problems.append(CaseProblem(
                index, f"duplicate 'stdin' (same as case {seen[stdin] + 1})"))
        else:
            seen[stdin] = index

    if len(cases) < floor:
        problems.append(CaseProblem(
            None, f"{len(cases)} hidden test(s), coverage floor is {floor}"))

    return problems


def is_gradable(cases):
    """
    Whether the suite can grade a submission AT ALL, which is a lower bar than
    meeting the contract: a problem with four valid cases is under-covered but
    still gradable, whereas one with none, or with no usable expected outputs,
    is not.

    Kept separate because the two conditions call for different responses —
    an under-covered problem needs test authoring, an ungradable one must not
    be served to a learner at all.
    """
    if not isinstance(cases, list) or not cases:
        return False
    return any(
        isinstance(case, dict)
        and isinstance(case.get("stdin"), str) and case.get("stdin").strip()
        and isinstance(case.get("expected_output"), str)
        for case in cases
    )
