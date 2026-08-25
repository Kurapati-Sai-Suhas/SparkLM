"""
The hidden-test quality gate (M2 P2.7h-1).

`MIN_HIDDEN_TESTS = 12` is a floor, not a quality score: twelve near-identical
cases satisfy it while testing one thing. This suite proves the gate answers
the question the floor cannot — *would this suite catch a wrong solution?* —
and that it cannot be talked into saying yes.

SYNTHETIC ONLY. The runner below executes real Python functions in-process; no
Judge0, no credentials, no `Question` row, no `ReferenceSolution`, no database.
The gate takes a runner exactly as `GradingService` and `OracleService` do, so
nothing here needs an execution service to exist.
"""

import pytest

from groups import hidden_test_quality as q
from groups.hidden_test_quality import (
    CategorySubstitution, InputContract, Mutant, evaluate_suite,
)

# ═════════════════════════════════════════════════════════════
# A synthetic problem: "sum of a list of integers"
# ═════════════════════════════════════════════════════════════
#
#   stdin  = "<n>\n<n space-separated integers>"
#   stdout = their sum
#
# The leading count is not decoration. `hidden_tests.validate_case` rejects a
# blank `stdin` outright — "a blank input is almost always a case that was
# never really written" — so a problem that accepts an empty collection cannot
# express that case as literally empty input. It has to denote emptiness
# in-band, which is what real problems do anyway. See the P2.7h-1 report: the
# `empty_input` category and the non-blank-stdin rule are in genuine tension
# for any problem whose empty case IS a blank line.

CONTRACT = InputContract(
    accepts_empty_input=True, is_sequence=True, numeric=True,
    allows_duplicates=True, allows_negative=True, allows_zero=True,
    order_sensitive=False, has_size_bounds=True, overflow_sensitive=False,
    description="sum of a whitespace-separated integer list")

CORRECT = "sum"


def _parse(stdin):
    tokens = (stdin or "").split()
    if not tokens:
        return []
    count = int(tokens[0])
    return [int(tok) for tok in tokens[1:1 + count]]


#: Candidate implementations, keyed by the source string a Mutant carries.
#: The "source" is a label rather than real code because the gate never parses
#: source — it only hands it to the runner, exactly as Judge0 would be handed
#: a file.
IMPLEMENTATIONS = {
    "sum":              lambda xs: sum(xs),
    "skip_first":       lambda xs: sum(xs[1:]),            # off-by-one
    "skip_last":        lambda xs: sum(xs[:-1]),           # boundary
    "dedup":            lambda xs: sum(set(xs)),           # duplicate handling
    "init_one":         lambda xs: sum(xs) + 1,            # wrong init
    "abs_sum":          lambda xs: sum(abs(x) for x in xs),  # sign misconception
    "positive_only":    lambda xs: sum(x for x in xs if x > 0),
    "empty_returns_one": lambda xs: 1 if not xs else sum(xs),
    "sorted_sum":       lambda xs: sum(sorted(xs)),        # equivalent
    "reversed_sum":     lambda xs: sum(reversed(xs)),      # equivalent
    "crashes":          None,
}


def runner(source, language, stdin):
    """
    Synthetic in-process runner with the repository's established signature:
    (source, language, stdin) -> verdict dict.

    `unrunnable` models a language this environment has no execution service
    for — distinct from `crashes`, which DOES run and fails, and is therefore
    a kill rather than an error.
    """
    if source == "unrunnable":
        return {"error": f"no execution service configured for {language}"}
    impl = IMPLEMENTATIONS.get(source)
    if impl is None:
        return {"status_id": 11, "stdout": "", "stderr": "boom"}
    try:
        return {"status_id": 3, "stdout": str(impl(_parse(stdin))), "stderr": ""}
    except Exception as exc:                                  # noqa: BLE001
        return {"status_id": 11, "stdout": "", "stderr": str(exc)}


def unavailable_runner(source, language, stdin):
    """A runner for a language this environment cannot execute."""
    return {"error": "no execution service configured for this language"}


def case(stdin, category=None):
    expected = str(sum(_parse(stdin)))
    row = {"stdin": stdin, "expected_output": expected}
    if category:
        row["category"] = category
    return row


def good_suite():
    """
    Twelve distinct cases covering every REQUIRED applicable category.

    Required here: empty_input, singleton, minimum_boundary, maximum_boundary,
    duplicate_values, negative_values, zero, overflow_sensitive (not
    applicable). already/reverse_sorted are optional (order-insensitive).
    """
    return [
        case("0", "empty_input"),
        case("1 7", "singleton"),
        case("1 1", "minimum_boundary"),
        case("10 1 2 3 4 5 6 7 8 9 10", "maximum_boundary"),
        case("3 4 4 4", "duplicate_values"),
        case("2 -5 -6", "negative_values"),
        case("3 0 0 5", "zero"),
        case("3 1 2 3"),
        case("3 10 20 30"),
        case("2 -1 1"),
        case("2 100 200"),
        case("3 3 1 2"),
    ]


def tier1(*identifiers, **overrides):
    """Curated realistic wrong solutions."""
    catalogue = {
        "t1-off-by-one": ("skips the first element", "skip_first"),
        "t1-boundary": ("skips the last element", "skip_last"),
        "t1-duplicates": ("de-duplicates before summing", "dedup"),
        "t1-init": ("initialises the accumulator to 1", "init_one"),
        "t1-sign": ("sums absolute values", "abs_sum"),
        "t1-empty": ("returns 1 for empty input", "empty_returns_one"),
    }
    out = []
    for identifier in identifiers:
        description, source = catalogue[identifier]
        out.append(Mutant(identifier=identifier, tier=1, source=source,
                          description=description, **overrides))
    return out


def tier2(n_killed=8, n_survived=2, n_equivalent=0, n_not_applicable=0,
          n_error=0):
    """
    A Tier-2 set with an exact, chosen outcome mix.

    Killed mutants use implementations that differ from `sum`; survivors use
    implementations that agree on every case in the suite.
    """
    killers = ["skip_first", "skip_last", "dedup", "init_one", "abs_sum",
               "positive_only", "empty_returns_one", "skip_first", "dedup",
               "init_one", "abs_sum", "skip_last"]
    out = []
    for i in range(n_killed):
        out.append(Mutant(f"t2-k{i}", 2, "operator mutation",
                          killers[i % len(killers)]))
    for i in range(n_survived):
        out.append(Mutant(f"t2-s{i}", 2, "unkillable by this suite",
                          "sorted_sum"))
    for i in range(n_equivalent):
        out.append(Mutant(
            f"t2-e{i}", 2, "reordering mutation", "reversed_sum",
            equivalence_argument="addition is commutative and associative, so "
                                 "summation order cannot change the result"))
    for i in range(n_not_applicable):
        out.append(Mutant(
            f"t2-n{i}", 2, "loop-bound mutation", "sum",
            applicable=False,
            not_applicable_reason="the reference contains no explicit loop "
                                  "bound to mutate"))
    for i in range(n_error):
        out.append(Mutant(f"t2-x{i}", 2, "mutant in an unrunnable language",
                          "unrunnable", language="java"))
    return out


#: The gate now REQUIRES an ExecutionPlan (M2 P2.7 follow-up), so these
#: synthetic tests supply a pass-through double: `source` here is a label
#: rather than real code, and `stdin` is already the literal text the fake
#: runner parses, so wrapping or re-preparing either would be meaningless.
#:
#: It lives in the TEST file deliberately. Production has exactly one plan —
#: `GradingService.quality_execution_plan` — and no default, because a default
#: is how the bypass this parameter closes would come back. The real seam is
#: exercised in `test_quality_gate_seam.py`.
PASS_THROUGH_PLAN = q.ExecutionPlan(
    build_executable=lambda source, language: source,
    prepare_stdin=lambda stdin, language: stdin,
)


def assess(cases=None, mutants=None, contract=CONTRACT, substitutions=(),
           run=runner, plan=PASS_THROUGH_PLAN):
    return evaluate_suite(
        cases if cases is not None else good_suite(),
        mutants if mutants is not None else
        (tier1("t1-off-by-one", "t1-duplicates", "t1-init") + tier2()),
        run, contract, plan=plan, substitutions=substitutions)


# ═════════════════════════════════════════════════════════════
# The happy path — the positive control everything else leans on
# ═════════════════════════════════════════════════════════════

def test_a_strong_suite_passes():
    report = assess()

    assert report.verdict == q.PASS, report.blockers
    assert report.blockers == []
    assert report.total_cases == 12
    assert report.tier1_kill_rate == 1.0
    assert report.tier2_effective_kill_rate == pytest.approx(0.8)


# ═════════════════════════════════════════════════════════════
# The floor
# ═════════════════════════════════════════════════════════════

def test_fewer_than_twelve_cases_fails():
    report = assess(cases=good_suite()[:11])

    assert report.verdict == q.FAIL
    assert any("floor is 12" in b for b in report.blockers)


def test_exactly_twelve_can_pass():
    """The floor is a floor, not a target — twelve good cases suffice."""
    report = assess(cases=good_suite())

    assert report.total_cases == 12
    assert report.verdict == q.PASS, report.blockers


# ═════════════════════════════════════════════════════════════
# Duplicates — normalized semantics
# ═════════════════════════════════════════════════════════════

def test_an_exact_duplicate_fails():
    cases = good_suite()
    cases[-1] = dict(cases[0])

    report = assess(cases=cases)

    assert report.verdict == q.FAIL
    assert report.duplicate_count == 1
    assert any("duplicate input" in b for b in report.blockers)


def test_a_whitespace_only_duplicate_still_fails():
    """
    The reason this gate adopts the NORMALIZED definition. Two cases differing
    only by a trailing newline are the same test; the raw comparison in
    `hidden_tests.validate_suite` would let this through.
    """
    cases = good_suite()
    cases[-1] = {"stdin": cases[0]["stdin"] + "\n",
                 "expected_output": cases[0]["expected_output"]}

    report = assess(cases=cases)

    assert report.duplicate_count == 1
    assert report.verdict == q.FAIL


def test_distinct_inputs_are_not_duplicates():
    """Positive control — the detector is not flagging everything."""
    assert q.normalized_duplicate_indexes(good_suite()) == []


# ═════════════════════════════════════════════════════════════
# Tier 1 — 100%, no partial credit
# ═════════════════════════════════════════════════════════════

def test_tier1_all_killed_passes():
    report = assess(mutants=tier1("t1-off-by-one", "t1-sign", "t1-empty")
                    + tier2())

    assert report.tier1_kill_rate == 1.0
    assert report.verdict == q.PASS, report.blockers


def test_a_single_tier1_survivor_fails():
    survivor = Mutant("t1-survivor", 1, "sorts before summing", "sorted_sum")

    report = assess(mutants=tier1("t1-off-by-one", "t1-init") + [survivor]
                    + tier2())

    assert report.verdict == q.FAIL
    assert report.tier1_survived == 1
    assert any("every curated wrong solution must be caught" in b
               for b in report.blockers)


def test_a_tier1_equivalence_argument_is_not_a_defence():
    """
    Tier 1 is a wrong algorithm somebody deliberately wrote. Calling it
    equivalent does not make the suite catch it.
    """
    excused = Mutant("t1-excused", 1, "sorts before summing", "sorted_sum",
                     equivalence_argument="addition is commutative")

    report = assess(mutants=tier1("t1-off-by-one") + [excused] + tier2())

    assert report.verdict == q.FAIL
    assert report.tier1_survived == 1


def test_no_tier1_mutants_cannot_pass():
    report = assess(mutants=tier2())

    assert report.verdict == q.FAIL
    assert any("no Tier-1 curated wrong solutions" in b for b in report.blockers)


def test_all_tier1_not_applicable_cannot_pass():
    """Excluding every curated mutant measures nothing; it is not a pass."""
    skipped = tier1("t1-off-by-one", "t1-init", applicable=False,
                    not_applicable_reason="this problem has no accumulator")

    report = assess(mutants=skipped + tier2())

    assert report.verdict == q.FAIL
    assert report.tier1_not_applicable == 2
    assert any("nothing was actually measured" in b for b in report.blockers)


# ═════════════════════════════════════════════════════════════
# Tier 2 — the 80% rule and the denominator
# ═════════════════════════════════════════════════════════════

def test_tier2_at_eighty_percent_passes():
    report = assess(mutants=tier1("t1-off-by-one") + tier2(8, 2))

    assert report.tier2_effective_kill_rate == pytest.approx(0.80)
    assert report.verdict == q.PASS, report.blockers


def test_tier2_below_eighty_percent_fails():
    """15/19 ≈ 79%."""
    report = assess(mutants=tier1("t1-off-by-one") + tier2(15, 4))

    assert report.tier2_effective_kill_rate == pytest.approx(15 / 19)
    assert report.tier2_effective_kill_rate < 0.80
    assert report.verdict == q.FAIL
    assert any("below 80%" in b for b in report.blockers)


def test_no_tier2_mutants_cannot_pass():
    report = assess(mutants=tier1("t1-off-by-one", "t1-init"))

    assert report.verdict == q.FAIL
    assert any("unmeasured, not satisfied" in b for b in report.blockers)


def test_all_tier2_not_applicable_cannot_pass():
    report = assess(mutants=tier1("t1-off-by-one") + tier2(0, 0,
                                                           n_not_applicable=6))

    assert report.verdict == q.FAIL
    assert report.tier2_not_applicable == 6
    assert any("unmeasured, not satisfied" in b for b in report.blockers)


def test_not_applicable_cannot_inflate_the_kill_rate():
    """
    The denominator attack. Padding a failing set with NOT_APPLICABLE mutants
    must not rescue it — they leave both numerator and denominator alone.
    """
    failing = tier1("t1-off-by-one") + tier2(3, 3)
    padded = failing + tier2(0, 0, n_not_applicable=20)

    before = assess(mutants=failing)
    after = assess(mutants=padded)

    assert before.tier2_effective_kill_rate == after.tier2_effective_kill_rate
    assert before.verdict == after.verdict == q.FAIL


def test_a_survivor_cannot_be_hidden_by_shrinking_the_denominator():
    baseline = assess(mutants=tier1("t1-off-by-one") + tier2(3, 3))
    assert baseline.verdict == q.FAIL

    # Add more measured kills and the rate legitimately improves — the point
    # is that the SURVIVOR is still counted, not erased.
    improved = assess(mutants=tier1("t1-off-by-one") + tier2(12, 3))
    assert improved.tier2_survived == 3
    assert improved.tier2_effective_kill_rate == pytest.approx(12 / 15)


# ═════════════════════════════════════════════════════════════
# Equivalent mutants
# ═════════════════════════════════════════════════════════════

def test_an_argued_equivalent_is_excluded_from_the_denominator():
    report = assess(mutants=tier1("t1-off-by-one") + tier2(8, 2, n_equivalent=5))

    assert report.tier2_equivalent == 5
    assert report.tier2_effective_kill_rate == pytest.approx(8 / 10)
    assert report.verdict == q.PASS, report.blockers


def test_an_unargued_survivor_is_never_classified_equivalent():
    """
    Passing every test is evidence of a possible GAP, not of equivalence.
    Without a structural argument the outcome must be SURVIVED.
    """
    silent = Mutant("t2-silent", 2, "reordering mutation", "reversed_sum")

    report = assess(mutants=tier1("t1-off-by-one") + tier2(1, 0) + [silent])

    result = next(r for r in report.results if r.identifier == "t2-silent")
    assert result.outcome == q.SURVIVED
    assert "no equivalence argument" in result.detail
    assert report.tier2_equivalent == 0


def test_the_equivalence_reason_is_recorded():
    report = assess(mutants=tier1("t1-off-by-one") + tier2(8, 2, n_equivalent=1))

    result = next(r for r in report.results if r.identifier == "t2-e0")
    assert result.outcome == q.EQUIVALENT
    assert "commutative" in result.detail


def test_not_applicable_requires_a_reason():
    with pytest.raises(ValueError, match="needs a reason"):
        Mutant("t2-bad", 2, "x", "sum", applicable=False)


# ═════════════════════════════════════════════════════════════
# Execution errors
# ═════════════════════════════════════════════════════════════

def test_an_unrunnable_mutant_is_a_blocker_not_a_pass():
    """
    A language this environment cannot execute must never be reported as
    verified. Unmeasured is not satisfied.
    """
    report = assess(mutants=tier1("t1-off-by-one") + tier2(8, 2, n_error=1))

    assert report.verdict == q.FAIL
    assert report.tier2_errors == 1
    assert any("could not be executed" in b for b in report.blockers)


def test_a_runner_that_reports_error_is_an_execution_error():
    report = assess(mutants=tier1("t1-off-by-one") + tier2(2, 0),
                    run=unavailable_runner)

    assert report.verdict == q.FAIL
    assert all(r.outcome == q.EXECUTION_ERROR for r in report.results)


def test_a_runner_that_raises_is_contained():
    def exploding(*args):
        raise RuntimeError("judge exploded")

    report = assess(mutants=tier1("t1-off-by-one") + tier2(2, 0), run=exploding)

    assert report.verdict == q.FAIL
    assert all(r.outcome == q.EXECUTION_ERROR for r in report.results)
    assert "judge exploded" in report.results[0].detail


def test_a_crashing_mutant_counts_as_killed():
    """
    A mutant that crashes IS caught — the learner would see a failure. That is
    a kill, not an execution error of the harness.
    """
    crasher = Mutant("t2-crash", 2, "crashes on every input", "crashes")

    report = assess(mutants=tier1("t1-off-by-one") + tier2(1, 0) + [crasher])

    result = next(r for r in report.results if r.identifier == "t2-crash")
    assert result.outcome == q.KILLED


# ═════════════════════════════════════════════════════════════
# Categories
# ═════════════════════════════════════════════════════════════

def test_a_missing_required_category_fails():
    cases = [c for c in good_suite() if c.get("category") != "negative_values"]
    cases.append(case("4 9 9 9 9"))

    report = assess(cases=cases)

    assert report.verdict == q.FAIL
    assert "negative_values" in report.missing_required_categories


def test_an_inapplicable_category_is_not_demanded():
    """
    A string problem must not be asked for negative values. Categories are
    derived from the contract, not from a fixed checklist.
    """
    string_contract = InputContract(
        accepts_empty_input=True, is_sequence=True, numeric=False,
        allows_duplicates=True, has_size_bounds=True,
        description="string problem")

    report = assess(contract=string_contract)

    records = {r["category"]: r for r in report.category_records}
    assert records["negative_values"]["applicable"] is False
    assert records["zero"]["applicable"] is False
    assert "negative_values" not in report.missing_required_categories


def test_an_optional_category_does_not_block():
    """already_sorted/reverse_sorted are optional here and absent."""
    report = assess()

    records = {r["category"]: r for r in report.category_records}
    assert records["already_sorted"]["applicable"] is False  # order-insensitive
    assert report.verdict == q.PASS, report.blockers


def test_a_problem_specific_substitution_satisfies_a_category():
    cases = [c for c in good_suite() if c.get("category") != "maximum_boundary"]
    cases.append(case("6 1 2 3 4 5 6", "all_elements_identical_sign"))
    substitution = CategorySubstitution(
        replaces="maximum_boundary", name="all_elements_identical_sign",
        reason="this problem has no meaningful upper size bound; the "
               "equivalent stress is a uniformly-signed input")

    report = assess(cases=cases, substitutions=[substitution])

    records = {r["category"]: r for r in report.category_records}
    assert records["maximum_boundary"]["substituted_by"] == \
        "all_elements_identical_sign"
    assert records["maximum_boundary"]["present"] is True
    assert report.verdict == q.PASS, report.blockers


def test_a_substitution_that_is_also_absent_still_fails():
    """Declaring a substitution is not the same as providing the case."""
    cases = [c for c in good_suite() if c.get("category") != "maximum_boundary"]
    cases.append(case("6 1 2 3 4 5 6"))
    substitution = CategorySubstitution(
        replaces="maximum_boundary", name="all_elements_identical_sign",
        reason="no meaningful upper bound")

    report = assess(cases=cases, substitutions=[substitution])

    assert report.verdict == q.FAIL
    assert any("also absent" in m for m in report.missing_required_categories)


def test_the_substitution_is_recorded_in_the_report():
    substitution = CategorySubstitution(
        replaces="maximum_boundary", name="x", reason="documented reason")
    cases = good_suite() + [case("4 5 5 5 5", "x")]

    report = assess(cases=cases, substitutions=[substitution])

    assert report.substitutions[0]["reason"] == "documented reason"
    assert report.substitutions[0]["replaces"] == "maximum_boundary"


# ═════════════════════════════════════════════════════════════
# Determinism and reporting
# ═════════════════════════════════════════════════════════════

def test_the_report_is_deterministic():
    first = assess().as_dict()
    for _ in range(5):
        assert assess().as_dict() == first


def test_mutant_order_does_not_change_the_report():
    mutants = tier1("t1-off-by-one", "t1-init") + tier2()

    forwards = assess(mutants=mutants).as_dict()
    backwards = assess(mutants=list(reversed(mutants))).as_dict()

    assert forwards == backwards


def test_blockers_are_actionable_and_sorted():
    report = assess(cases=good_suite()[:5],
                    mutants=tier1("t1-off-by-one") + tier2(3, 5))

    assert report.blockers == sorted(report.blockers)
    assert len(report.blockers) >= 2
    joined = " ".join(report.blockers)
    assert "floor is 12" in joined
    assert "Tier-2 effective kill rate" in joined


def test_an_insufficient_suite_is_never_reported_as_good():
    report = assess(cases=good_suite()[:3], mutants=[])

    assert report.verdict == q.FAIL
    assert report.blockers


def test_the_report_serialises():
    data = assess().as_dict()

    assert data["verdict"] == q.PASS
    assert isinstance(data["results"], list)
    assert data["results"][0]["identifier"]


# ═════════════════════════════════════════════════════════════
# Safety — no trust contamination, no persistence
# ═════════════════════════════════════════════════════════════

def test_the_gate_touches_no_django_models():
    """
    Structural guarantee. If this module cannot import a model it cannot write
    one, and the P2.7 trust boundary is safe by construction rather than by
    review.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parent / "hidden_test_quality.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # Strip docstrings: prose is not behaviour. The module's own docstring
    # says it cannot reach a Question row, and a raw text search would read
    # that sentence as evidence of the thing it denies — the same trap the
    # P2.7d migration guard hit.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    executable = ast.unparse(tree)

    for forbidden in ("groups.models", "Question", "ReferenceSolution",
                      "trust_state", "adaptive_eligible", "objects.",
                      ".save(", "Response("):
        assert forbidden not in executable, (
            f"the quality gate references {forbidden!r}; it must be pure "
            f"assessment with no way to reach production state")


def test_the_gate_never_writes_expected_output():
    """It reads expected_output to judge a mutant. It must not produce one."""
    cases = good_suite()
    snapshot = [dict(c) for c in cases]

    assess(cases=cases)

    assert cases == snapshot


def test_the_gate_does_not_mutate_its_inputs():
    cases = good_suite()
    mutants = tier1("t1-off-by-one") + tier2()
    case_snapshot = [dict(c) for c in cases]
    mutant_ids = [m.identifier for m in mutants]

    assess(cases=cases, mutants=mutants)

    assert cases == case_snapshot
    assert [m.identifier for m in mutants] == mutant_ids


def test_realistic_wrong_algorithms_are_actually_detected():
    """
    The point of the whole gate: a suite that misses a real misconception must
    say so. Here the suite has no case where de-duplication changes the
    answer, so the duplicate-handling mutant survives — and the gate fails.
    """
    weak_cases = [case(f"2 {i} {i + 1}") for i in range(1, 13)]
    for i, category in enumerate(
            ["empty_input", "singleton", "minimum_boundary",
             "maximum_boundary", "duplicate_values", "negative_values",
             "zero"]):
        weak_cases[i]["category"] = category

    report = evaluate_suite(weak_cases, tier1("t1-duplicates") + tier2(),
                            runner, CONTRACT, plan=PASS_THROUGH_PLAN)

    assert report.verdict == q.FAIL
    assert report.tier1_survived == 1


# ═════════════════════════════════════════════════════════════
# Gaps found by mutation testing
# ═════════════════════════════════════════════════════════════

def test_a_malformed_case_fails():
    """
    Mutation testing found that NO test exercised the contract-violation path
    at all — every fixture was well-formed, so deleting the malformed-case
    blocker changed nothing observable.
    """
    cases = good_suite()
    cases[3] = {"stdin": "2 1 2"}          # no expected_output

    report = assess(cases=cases)

    assert report.verdict == q.FAIL
    assert report.malformed_count >= 1
    assert any("violate the hidden-test contract" in b for b in report.blockers)


def test_a_non_string_field_is_malformed():
    cases = good_suite()
    cases[4] = {"stdin": "2 1 2", "expected_output": 3}   # int, not str

    report = assess(cases=cases)

    assert report.verdict == q.FAIL
    assert report.malformed_count >= 1


def test_a_well_formed_suite_reports_no_malformed_cases():
    """Positive control for the two above."""
    report = assess()

    assert report.malformed_count == 0
    assert report.contract_problems == []


def test_an_empty_tier2_set_says_none_were_supplied():
    """
    The diagnostic must distinguish "you supplied none" from "all of yours
    were excluded" — an operator fixes those two situations differently.
    Mutation testing showed the two branches were interchangeable.
    """
    report = assess(mutants=tier1("t1-off-by-one", "t1-init"))

    assert any("no Tier-2 mutants were supplied" in b for b in report.blockers)


def test_all_tier2_excluded_says_they_were_excluded():
    report = assess(mutants=tier1("t1-off-by-one")
                    + tier2(0, 0, n_not_applicable=4))

    assert any("every Tier-2 mutant was excluded" in b for b in report.blockers)


def test_an_empty_tier1_set_says_none_were_supplied():
    report = assess(mutants=tier2())

    assert any("no Tier-1 curated wrong solutions" in b for b in report.blockers)


def test_all_tier1_excluded_says_nothing_was_measured():
    skipped = tier1("t1-off-by-one", applicable=False,
                    not_applicable_reason="no accumulator in this problem")

    report = assess(mutants=skipped + tier2())

    assert any("nothing was actually measured" in b for b in report.blockers)


# ═════════════════════════════════════════════════════════════
# M2 P2.7h-31 — the execution plan must stay mandatory
#
# `plan` is keyword-only with NO default so the gate cannot be called
# without one. A default would silently restore the defect the parameter
# exists to close: mutants executed unwrapped and stdin never prepared, so
# kill rates measured against semantics no learner experiences. A Phase 19
# mutation sweep put the default back and nothing failed.
# ═════════════════════════════════════════════════════════════

def test_evaluate_suite_refuses_to_run_without_an_execution_plan():
    import inspect

    signature = inspect.signature(evaluate_suite)
    plan = signature.parameters["plan"]
    assert plan.kind is inspect.Parameter.KEYWORD_ONLY
    assert plan.default is inspect.Parameter.empty, (
        "a default execution plan lets the gate measure the wrong semantics "
        "silently; it must be supplied at every call site")

    with pytest.raises(TypeError, match="plan"):
        evaluate_suite([], [], lambda *a: {}, InputContract())
