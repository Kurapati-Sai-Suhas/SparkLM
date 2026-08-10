"""
The hidden-test validator (M2 P2.5, Phase 8).

Its job is to make "is this problem actually gradable?" answerable without
opening a shell, and to make a shortfall LOUD. Two failure modes matter more
than any individual check:

  * reporting PASS about an empty bank, which is how "all green" gets said
    about nothing;
  * rounding "we have never verified this expected output" up to PASS,
    which would launder the exact problem P2.5 exists to fix.

Both are pinned below.
"""

import json

import pytest
from django.core.management import call_command

from groups.models import CodingPortal, Question, Topic


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Validator Portal")
    t, _ = Topic.objects.get_or_create(
        name="ValidatorTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return t


def make(topic, title, cases):
    return Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=cases, hidden_wrapper_code={},
    )


def good_cases(n, prefix="in"):
    return [{"stdin": f"{prefix}{i}", "expected_output": f"out{i}"} for i in range(n)]


def run(capsys, *args):
    """Run the validator, returning (exit_code, stdout)."""
    with pytest.raises(SystemExit) as exc:
        call_command("validate_question_bank", *args)
    return exc.value.code, capsys.readouterr().out


def report(capsys, *args):
    code, out = run(capsys, "--json", *args)
    return code, json.loads(out)


# ─────────────────────────────────────────────────────────────
# The two ways this tool could lie
# ─────────────────────────────────────────────────────────────

def test_an_empty_bank_is_a_failure_not_a_pass(db, capsys):
    """
    Zero problems means zero failures, which a naive implementation reports as
    success. The development database is empty right now, so this is the exact
    shape of the false green that would have been reported.
    """
    code, out = run(capsys)

    assert code == 2
    assert "EMPTY" in out


def test_unverified_expected_outputs_are_never_reported_as_verified(topic, capsys):
    """
    No reference-solution storage exists yet, so no expected output in this
    system has been produced by executing a trusted implementation. The report
    must say UNKNOWN, not YES — a problem with 12 LLM-invented cases is
    covered, not correct.
    """
    make(topic, "Plenty of unverified cases", good_cases(16))

    code, data = report(capsys)

    assert data["problems"][0]["hidden"] == 16
    assert data["problems"][0]["verified"] == "UNKNOWN"
    assert data["problems"][0]["oracle"] == "NO"
    assert data["with_verified_oracle"] == 0


# ─────────────────────────────────────────────────────────────
# Coverage floor
# ─────────────────────────────────────────────────────────────

def test_zero_hidden_tests_is_blocked_not_merely_failing(topic, capsys):
    """A problem with no tests cannot be graded at all — a stronger verdict."""
    make(topic, "Ungradable", [])

    code, data = report(capsys)

    assert code == 1
    assert data["problems"][0]["status"] == "BLOCKED"
    assert data["zero_hidden_tests"] == 1


def test_a_problem_below_the_floor_fails(topic, capsys):
    make(topic, "Thin coverage", good_cases(8))

    code, data = report(capsys)

    assert code == 1
    assert data["problems"][0]["status"] == "FAIL"
    assert data["below_floor"] == 1
    assert "8 hidden test(s), floor is 12" in data["problems"][0]["problems"]


def test_exactly_at_the_floor_passes(topic, capsys):
    """12 is the floor, not the exclusive bound — off-by-one guard."""
    make(topic, "Exactly twelve", good_cases(12))

    code, data = report(capsys)

    assert code == 0
    assert data["problems"][0]["status"] == "PASS"
    assert data["passing"] == 1


def test_one_below_the_floor_fails(topic, capsys):
    make(topic, "Eleven", good_cases(11))

    assert report(capsys)[0] == 1


def test_the_floor_is_configurable(topic, capsys):
    make(topic, "Eight", good_cases(8))

    assert report(capsys, "--min", "8")[0] == 0


# ─────────────────────────────────────────────────────────────
# Data-quality checks
# ─────────────────────────────────────────────────────────────

def test_missing_expected_output_is_reported(topic, capsys):
    cases = good_cases(12)
    del cases[0]["expected_output"]
    make(topic, "Missing key", cases)

    code, data = report(capsys)

    assert code == 1
    assert any("missing expected_output" in p for p in data["problems"][0]["problems"])


def test_a_problem_where_every_case_lacks_an_answer_is_blocked(topic, capsys):
    make(topic, "No answers at all", [{"stdin": f"in{i}"} for i in range(12)])

    _, data = report(capsys)

    assert data["problems"][0]["status"] == "BLOCKED"


def test_duplicate_inputs_are_reported(topic, capsys):
    """
    A duplicate input inflates the count toward the floor while testing
    nothing new — coverage theatre, and precisely what a careless bulk
    generator produces.
    """
    cases = good_cases(12)
    cases[5]["stdin"] = cases[0]["stdin"]
    make(topic, "Padded", cases)

    code, data = report(capsys)

    assert code == 1
    assert any("duplicate stdin" in p for p in data["problems"][0]["problems"])


def test_empty_stdin_is_reported(topic, capsys):
    """The generation contract states stdin must never be empty."""
    cases = good_cases(12)
    cases[3]["stdin"] = "   "
    make(topic, "Blank input", cases)

    _, data = report(capsys)

    assert any("empty stdin" in p for p in data["problems"][0]["problems"])


def test_malformed_non_object_cases_are_reported(topic, capsys):
    """`hidden_test_cases` is a schemaless JSONField — anything can be in it."""
    make(topic, "Junk rows", good_cases(11) + ["not-an-object"])

    _, data = report(capsys)

    assert any("not objects" in p for p in data["problems"][0]["problems"])


def test_a_non_list_value_is_blocked_rather_than_crashing(topic, capsys):
    q = make(topic, "Wrong type", [])
    Question.objects.filter(pk=q.pk).update(hidden_test_cases={"oops": True})

    code, data = report(capsys)

    assert code == 1
    assert data["problems"][0]["status"] == "BLOCKED"


# ─────────────────────────────────────────────────────────────
# Aggregate reporting
# ─────────────────────────────────────────────────────────────

def test_the_summary_counts_each_category(topic, capsys):
    make(topic, "Good", good_cases(12))
    make(topic, "Thin", good_cases(3))
    make(topic, "Empty", [])

    code, data = report(capsys)

    assert code == 1
    assert data["total_problems"] == 3
    assert data["passing"] == 1
    assert data["failing"] == 1
    assert data["blocked"] == 1
    assert data["below_floor"] == 2


def test_the_command_writes_nothing(topic, capsys):
    """
    Read-only is a safety property, not a convenience: this is the one P2.5
    command intended to be pointed at production.
    """
    q = make(topic, "Untouched", good_cases(4))
    before = Question.objects.get(pk=q.pk).hidden_test_cases

    run(capsys)

    assert Question.objects.get(pk=q.pk).hidden_test_cases == before
    assert Question.objects.count() == 1
