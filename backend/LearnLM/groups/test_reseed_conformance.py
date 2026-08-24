"""
Conformance of a generated statement to an authoritative specification
(M2 P2.7h-20).

The check under test is CONTAINMENT, not equivalence, and these tests are
written to hold that line: they prove it catches a dropped or substituted
requirement, and they prove it does NOT claim more than that.

The two failing artifacts from Phase 5 are used as fixtures, because a
validator justified by a real defect is worth more than one justified by an
invented one.
"""

import pytest

from groups import reseed_conformance as conf

# The two Phase 5 defects, reduced to their essentials.
ADJACENT_SPEC = ("Given an n by n matrix of integers, you may repeatedly "
                 "choose two adjacent cells and multiply both of their values "
                 "by -1. Return the maximum sum of all elements achievable.")
ADJACENT_GOOD = ("<p>Choose two adjacent cells and multiply both by -1, any "
                 "number of times. Return the maximum sum of all elements.</p>")
ADJACENT_WRONG = ("<p>Choose any cell and multiply its value by -1, any "
                  "number of times. Return the maximum sum of all elements "
                  "of the matrix.</p>")

GCD_SPEC = ("Given a list nums of positive integers, return the greatest "
            "common divisor of the smallest number in nums and the largest "
            "number in nums.")
GCD_GOOD = ("<p>Return the greatest common divisor of the smallest number in "
            "<code>nums</code> and the largest number in <code>nums</code>.</p>")
GCD_WRONG = ("<p>Compute the greatest common divisor of all elements in "
             "<code>nums</code> and return it.</p>")


# ═════════════════════════════════════════════════════════════
# The two mandatory cases
# ═════════════════════════════════════════════════════════════

def test_a_widened_operation_is_refused():
    """`adjacent cells` -> `any cell` is the q1970 defect exactly."""
    refusals = conf.conformance_refusals(ADJACENT_SPEC, ADJACENT_WRONG)

    assert refusals
    assert any("adjacent" in refusal for refusal in refusals), refusals


def test_a_changed_objective_is_refused():
    """`smallest and largest` -> `all elements` is the q1974 defect exactly."""
    refusals = conf.conformance_refusals(GCD_SPEC, GCD_WRONG)

    assert refusals
    assert any("smallest" in refusal and "largest" in refusal
               for refusal in refusals), refusals


@pytest.mark.parametrize("specification,statement", [
    (ADJACENT_SPEC, ADJACENT_GOOD),
    (GCD_SPEC, GCD_GOOD),
])
def test_a_faithful_statement_conforms(specification, statement):
    assert conf.conformance_refusals(specification, statement) == []


def test_the_substitution_is_named_not_just_the_omission():
    """
    Losing `adjacent` while gaining `any` is a changed operation, and the
    refusal should say so rather than only listing what went missing.
    """
    refusals = conf.conformance_refusals(ADJACENT_SPEC, ADJACENT_WRONG)
    assert any("substitutes" in refusal for refusal in refusals), refusals


# ═════════════════════════════════════════════════════════════
# The boundary of the claim
# ═════════════════════════════════════════════════════════════

def test_no_specification_means_no_confidence():
    """The absence of a source is itself a refusal, not a pass."""
    for empty in ("", "   ", None):
        refusals = conf.conformance_refusals(empty, GCD_GOOD)
        assert refusals
        assert "no authoritative specification" in refusals[0]


def test_addition_alone_does_not_block():
    """
    Measured on the five Phase 5 artifacts, blocking on addition flagged all
    five — three of them wrongly. A statement legitimately says more than a
    specification: constraints, examples, a restated output. A check that
    always fires is a check that gets turned off.
    """
    elaborated = (GCD_GOOD + "<h3>Example</h3><p>Input: nums = [2, 5, 6, 9, 10]"
                  "; Output: 2. Constraints: the list length is at least one."
                  "</p>")
    assert conf.conformance_refusals(GCD_SPEC, elaborated) == []
    assert conf.advisory_additions(GCD_SPEC, elaborated)


def test_invented_numbers_are_advisory_not_fatal():
    worked = GCD_GOOD + "<p>Example: nums = [7, 14] gives 7.</p>"
    assert conf.conformance_refusals(GCD_SPEC, worked) == []
    assert "7" in conf.invented_numbers(GCD_SPEC, worked)


def test_known_synonyms_satisfy_a_requirement_automatically():
    """
    `minimum`/`smallest` and `maximum`/`largest` are the same instruction.
    Refusing one because the specification chose the other teaches operators
    to fight the validator instead of using it.
    """
    paraphrased = ("<p>Return the greatest common divisor of the minimum and "
                   "maximum values of <code>nums</code>.</p>")
    assert conf.conformance_refusals(GCD_SPEC, paraphrased) == []


def test_an_unlisted_paraphrase_can_be_accepted_deliberately():
    """
    `allow_omitted` is an operator's recorded exception, not a silent one.
    The synonym table is deliberately short — a generous one would let a real
    substitution through under cover of "near enough" — so anything outside
    it needs a human to say so explicitly.
    """
    unlisted = ("<p>Return the greatest common divisor of the least and "
                "greatest values of <code>nums</code>.</p>")
    assert conf.conformance_refusals(GCD_SPEC, unlisted)
    assert conf.conformance_refusals(
        GCD_SPEC, unlisted,
        allow_omitted={"smallest", "largest"}) == []


def test_the_report_is_evidence_rather_than_a_verdict():
    report = conf.conformance_report(ADJACENT_SPEC, ADJACENT_WRONG)

    assert "adjacent" in report["omitted"]
    assert "any" in report["added"]
    assert "sum" in report["shared"]
    assert report["conforms"] is False


def test_the_vocabulary_is_load_bearing_words_only():
    """
    Not every word. Whole-text similarity would fire on every rewording; these
    are the terms that, changed, change the problem.
    """
    terms = conf.terms_in("Return the sum of two adjacent distinct elements.")
    assert {"return", "sum", "two", "adjacent", "distinct"} <= terms
    assert "elements" not in terms      # a noun that carries no requirement


def test_html_and_entities_do_not_hide_a_term():
    hidden = "<p><strong>adj</strong>acent</p>"
    assert "adjacent" not in conf.terms_in(hidden)
    visible = "<p>two <em>adjacent</em> cells</p>"
    assert {"two", "adjacent", "cell"} <= conf.terms_in(visible)


def test_unicode_comparison_operators_are_normalised():
    assert "1" in conf.numbers_in("<p>1 ≤ n ≤ 100</p>")
    assert "100" in conf.numbers_in("<p>1 ≤ n ≤ 100</p>")
