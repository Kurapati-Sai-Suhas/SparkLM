"""
q7 hidden-suite expansion (M2 P2.34).

q7's suite was 4 cases against a floor of 12, with none of its five required
coverage categories labelled, so the quality gate failed structurally before
a single mutant could be considered. This expansion took it to 12.

── What these tests can and cannot see ─────────────────────────────────────

The suite itself lives in the production database and the expansion plan is
gitignored — both are grading truth, and neither is available to a test run
from a fresh clone. So nothing here asserts on q7's stored cases.

What IS committed, and what these tests therefore pin, is the SPECIFICATION
side: the quality spec, the input contract it declares, and the coverage
obligations that contract creates. Those are what made the gate fail, and a
regression in them would make it fail again — or, worse, pass for the wrong
reason.

The suite-shaped fixtures below are synthetic. They mirror the categories the
real expansion used, without carrying any stored input or answer.
"""

import ast
import json
import pathlib

import pytest

from groups import hidden_test_quality as quality
from groups import hidden_tests

SPEC_PATH = (pathlib.Path(__file__).resolve().parent.parent
             / "quality" / "q7_quality_spec.json")

#: The categories q7's contract makes mandatory. Named here so a change to the
#: contract that silently drops one is visible as a test failure rather than a
#: quieter gate.
REQUIRED_FOR_Q7 = {"minimum_boundary", "maximum_boundary", "negative_values",
                   "zero", "overflow_sensitive"}

#: The category labels the real expansion applied, in order. Synthetic cases
#: are built from these; no stored input or expected output appears.
EXPANDED_CATEGORIES = (
    "typical", "negative_values", "trailing_zero", "overflow_sensitive",
    "zero", "minimum_boundary", "maximum_boundary", "negative_overflow",
    "largest_non_overflowing", "smallest_non_overflowing", "single_digit",
    "interior_zeros",
)


@pytest.fixture
def spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def contract(spec):
    return quality.InputContract(**spec["input_contract"])


def synthetic_suite(categories):
    """
    A suite with the given labels and distinct placeholder inputs.

    Values are `case-N`, not integers: these fixtures must not resemble q7's
    stored cases even by accident, and the structural checks under test care
    about labels, counts and duplicates rather than arithmetic.
    """
    return [{"stdin": f"case-{index}", "expected_output": f"out-{index}",
             "category": category}
            for index, category in enumerate(categories, start=1)]


# ═════════════════════════════════════════════════════════════
# A — the floor
# ═════════════════════════════════════════════════════════════

def test_the_floor_is_twelve_and_is_not_a_local_constant():
    """
    The expansion targeted the repository's floor, not a number chosen to fit
    what was convenient. If the floor moves, this milestone's premise moves.
    """
    assert hidden_tests.MIN_HIDDEN_TESTS == 12


def test_a_twelve_case_suite_clears_the_floor():
    problems = hidden_tests.validate_suite(
        synthetic_suite(EXPANDED_CATEGORIES))

    assert not [p for p in problems if "floor" in p.message]


def test_the_pre_expansion_size_still_fails_the_floor():
    """
    The premise of the milestone, kept executable: four cases is a blocker,
    so a regression that shrank the suite would be caught rather than
    silently producing a smaller passing suite.
    """
    problems = hidden_tests.validate_suite(
        synthetic_suite(EXPANDED_CATEGORIES[:4]))

    assert [p for p in problems if "floor" in p.message]


def test_the_expansion_added_exactly_eight_cases():
    assert len(EXPANDED_CATEGORIES) == 12
    assert len(EXPANDED_CATEGORIES) - 4 == 8


# ═════════════════════════════════════════════════════════════
# B–F — the five required categories
# ═════════════════════════════════════════════════════════════

def test_the_contract_requires_exactly_the_five_expected_categories(contract):
    required = {rule.name for rule in quality.GENERIC_CATEGORIES
                if rule.applies(contract) and rule.required}

    assert required == REQUIRED_FOR_Q7


@pytest.mark.parametrize("category", sorted(REQUIRED_FOR_Q7))
def test_each_required_category_is_covered(contract, category):
    """One test per obligation, so a failure names the category that is missing."""
    _, missing = quality.assess_categories(
        contract, synthetic_suite(EXPANDED_CATEGORIES))

    assert category not in missing


def test_no_required_category_is_missing(contract):
    _, missing = quality.assess_categories(
        contract, synthetic_suite(EXPANDED_CATEGORIES))

    assert missing == []


def test_the_original_four_labels_alone_would_still_fail(contract):
    """
    Labelling the existing cases was necessary but nowhere near sufficient:
    three of the five obligations are met only by cases the expansion added.
    """
    _, missing = quality.assess_categories(
        contract, synthetic_suite(EXPANDED_CATEGORIES[:4]))

    assert set(missing) == {"minimum_boundary", "maximum_boundary", "zero"}


def test_the_contract_flags_are_what_create_the_obligations(contract):
    """
    The five categories are required BECAUSE of these flags. Weakening a flag
    would silently drop a coverage obligation, which is the quiet way a gate
    stops meaning anything.
    """
    assert contract.numeric is True
    assert contract.allows_negative is True
    assert contract.allows_zero is True
    assert contract.overflow_sensitive is True
    assert contract.has_size_bounds is True


def test_categories_that_do_not_apply_are_not_demanded(contract):
    """A scalar problem must not be asked for sequence coverage."""
    records = {r["category"]: r for r in quality.assess_categories(
        contract, synthetic_suite(EXPANDED_CATEGORIES))[0]}

    for inapplicable in ("empty_input", "singleton", "duplicate_values",
                         "already_sorted", "reverse_sorted"):
        assert records[inapplicable]["applicable"] is False


# ═════════════════════════════════════════════════════════════
# G/H — contract conformance and duplicates
# ═════════════════════════════════════════════════════════════

def test_every_case_shape_conforms_to_the_hidden_test_contract():
    problems = hidden_tests.validate_suite(
        synthetic_suite(EXPANDED_CATEGORIES))

    assert [p for p in problems if p.index is not None] == []


def test_a_duplicate_input_is_rejected():
    """
    The specific failure a careless expansion produces: growing the count
    toward the floor while testing nothing new.
    """
    suite = synthetic_suite(EXPANDED_CATEGORIES)
    suite[-1]["stdin"] = suite[0]["stdin"]

    problems = hidden_tests.validate_suite(suite)

    assert [p for p in problems if "duplicate" in p.message]


def test_every_expansion_category_is_distinct():
    """
    Twelve cases carrying the same label would clear the floor and cover one
    category. The labels are the unit of coverage, so they must differ.
    """
    assert len(set(EXPANDED_CATEGORIES)) == len(EXPANDED_CATEGORIES)


def test_an_unlabelled_case_cannot_count_toward_coverage(contract):
    """
    Why the expansion had to label the four pre-existing cases: an unlabelled
    case is invisible to the coverage assessment even when it embodies the
    category perfectly.
    """
    unlabelled = [{"stdin": f"case-{i}", "expected_output": f"out-{i}"}
                  for i in range(12)]

    _, missing = quality.assess_categories(contract, unlabelled)

    assert set(missing) == REQUIRED_FOR_Q7


# ═════════════════════════════════════════════════════════════
# K — the committed quality spec stays valid
# ═════════════════════════════════════════════════════════════

def test_the_quality_spec_still_parses_and_validates(spec):
    assert spec["question"] == 7
    quality.InputContract(**spec["input_contract"])
    mutants = [quality.Mutant(**m) for m in spec["mutants"]]

    assert len(mutants) == 8
    assert sum(1 for m in mutants if m.tier == 1) == 5
    assert sum(1 for m in mutants if m.tier == 2) == 3


def test_every_mutant_source_is_syntactically_valid(spec):
    for entry in spec["mutants"]:
        ast.parse(entry["source"])


def test_every_mutant_exposes_exactly_one_public_method(spec):
    """
    A mutant the harness refuses to run is not a survivor and not a kill — it
    is an execution error, which must never be counted as either.
    """
    for entry in spec["mutants"]:
        namespace = {}
        exec(compile(entry["source"], entry["identifier"], "exec"), namespace)
        solution = namespace["Solution"]()
        public = [name for name in dir(solution)
                  if not name.startswith("_")
                  and callable(getattr(solution, name))]
        assert public == ["reverse"], entry["identifier"]


def test_every_mutant_terminates_on_a_negative_input(spec):
    """
    The hazard found while drafting: the natural C/Java digit loop,
    `while n != 0` with `n // 10`, never terminates in Python because
    -1 // 10 is -1. On Judge0 that registers as a timeout rather than a kill
    and quietly distorts the rate, so no mutant may contain that shape.
    """
    for entry in spec["mutants"]:
        namespace = {}
        exec(compile(entry["source"], entry["identifier"], "exec"), namespace)
        # Reaching the next line at all is the termination evidence. The
        # assertion then pins that the call produced a value, so a mutant
        # that silently returns nothing on this input is caught too.
        result = namespace["Solution"]().reverse(-123)
        assert result is not None, entry["identifier"]


def test_the_thresholds_the_spec_targets_are_unchanged():
    assert quality.TIER1_REQUIRED_KILL_RATE == 1.0
    assert quality.TIER2_REQUIRED_KILL_RATE == 0.80


def test_the_bias_disclosure_survives_in_provenance(spec):
    """
    Operator direction: the disclosure that the stored inputs were read before
    the mutants were written must not be edited away, because it is the reason
    a passing gate here is weaker evidence than one written blind.
    """
    provenance = spec["provenance"]

    assert "bias_disclosure" in provenance
    assert "CANNOT claim" in provenance["bias_disclosure"]
    assert provenance["authored_by"].endswith("PENDING OPERATOR REVIEW")


# ═════════════════════════════════════════════════════════════
# J — nothing learner-facing carries grading truth
# ═════════════════════════════════════════════════════════════

def test_the_committed_spec_carries_no_stored_expected_output(spec):
    """
    Mutants are wrong answers and are public by design; the suite's answers
    are not. The spec's rationale uses the statement's own worked examples and
    2147483647, a public constant.
    """
    blob = json.dumps(spec)

    # The one stored input that is not published in the statement.
    assert "1534236469" not in blob


def test_the_expansion_plan_is_not_committed():
    """
    The plan holds every new input AND its expected output. It must live only
    where .gitignore already excludes it.
    """
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[3]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "remediation/*_suite_expansion.json" in gitignore

    # The pattern existing is not the same as the file being untracked, so
    # ask git directly. A plan that was somehow added would show here.
    tracked = subprocess.run(
        ["git", "ls-files", "backend/LearnLM/remediation/"],
        cwd=root, capture_output=True, text=True).stdout

    assert "_suite_expansion.json" not in tracked
