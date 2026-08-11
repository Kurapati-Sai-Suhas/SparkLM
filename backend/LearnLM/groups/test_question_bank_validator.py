"""
The hidden-test and oracle validator (M2 P2.5, Phases 6 + 8).

Its job is to make "is this problem actually gradable, and has anything ever
checked its answers?" answerable without opening a shell, and to make a
shortfall LOUD. Three ways a tool like this lies, all pinned below:

  * reporting success about an empty bank — zero problems means zero
    failures, which is how "all green" gets announced about nothing, and is
    the state the development database is in right now;
  * reporting zero problems when the database was simply unreachable, turning
    an outage into a clean bill of health;
  * rounding "we have never verified this expected output" up to PASS, which
    would launder the exact defect P2.5 exists to fix.
"""

import json

import pytest
from django.core.management import call_command

from groups.conftest import approved_reference
from groups.models import CodingPortal, Question, ReferenceSolution, Topic


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Validator Portal")
    t, _ = Topic.objects.get_or_create(
        name="ValidatorTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return t


def make(topic, title, cases, oracle=True, language="python"):
    question = Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=cases, hidden_wrapper_code={},
    )
    if oracle:
        approved_reference(question, language=language)
    return question


def good_cases(n, prefix="in"):
    return [{"stdin": f"{prefix}{i}", "expected_output": f"out{i}"} for i in range(n)]


def run(capsys, *args):
    with pytest.raises(SystemExit) as exc:
        call_command("validate_question_bank", *args)
    return exc.value.code, capsys.readouterr().out


def report(capsys, *args):
    code, out = run(capsys, "--json", *args)
    return code, json.loads(out)


# ─────────────────────────────────────────────────────────────
# The three ways this tool could lie
# ─────────────────────────────────────────────────────────────

def test_an_empty_bank_is_blocked_not_passing(db, capsys):
    code, data = report(capsys)

    assert code == 2
    assert data["census"] == "BLOCKED"
    assert data["total_problems"] == 0


def test_an_unreachable_database_is_blocked_not_zero_problems(db, capsys):
    """
    An outage must not read as a clean bank. Without this, a scheduled job
    pointed at a bad alias reports "0 problems, 0 failures" forever.
    """
    code, data = report(capsys, "--database", "does_not_exist")

    assert code == 2
    assert data["census"] == "BLOCKED"
    assert data["total_problems"] is None
    assert "does_not_exist" in data["reason"]


def test_unverified_outputs_are_never_reported_as_verified(topic, capsys):
    """
    Oracle EXECUTION is Phase 7. Having a reference solution on file is not
    the same as having run it, so `verified` stays UNKNOWN even when an
    oracle exists — the report must not imply a check that never happened.
    """
    make(topic, "Has an oracle but nothing ran it", good_cases(16))

    code, data = report(capsys)

    assert data["problems"][0]["oracle"] == "YES"
    assert data["problems"][0]["verified"] == "UNKNOWN"
    assert data["verified_outputs"] == 0


# ─────────────────────────────────────────────────────────────
# Oracle presence
# ─────────────────────────────────────────────────────────────

def test_a_problem_with_no_active_oracle_fails(topic, capsys):
    make(topic, "No oracle", good_cases(16), oracle=False)

    code, data = report(capsys)

    assert code == 1
    assert data["problems"][0]["status"] == "FAIL"
    assert data["with_active_oracle"] == 0
    assert any("no active reference solution" in p
               for p in data["problems"][0]["problems"])


def test_a_deactivated_oracle_does_not_count(topic, capsys):
    question = make(topic, "Superseded only", good_cases(16), oracle=False)
    approved_reference(question, language="python", source_code="x", active=False)

    _, data = report(capsys)

    assert data["with_active_oracle"] == 0
    assert data["problems"][0]["oracle"] == "NO"


def test_an_unsupported_oracle_language_is_reported(topic, capsys):
    make(topic, "Bad language", good_cases(16), language="cobol")

    _, data = report(capsys)

    assert any("not a supported Judge0 language" in p
               for p in data["problems"][0]["problems"])


def test_a_fully_compliant_problem_passes(topic, capsys):
    """
    Positive control. Without this the validator could reject everything and
    every other test here would still pass.
    """
    make(topic, "Complete", good_cases(12))

    code, data = report(capsys)

    assert code == 0
    assert data["problems"][0]["status"] == "PASS"
    assert data["passing"] == 1


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


def test_exactly_at_the_floor_passes(topic, capsys):
    """12 is the floor, not the exclusive bound — off-by-one guard."""
    make(topic, "Exactly twelve", good_cases(12))

    assert report(capsys)[0] == 0


def test_one_below_the_floor_fails(topic, capsys):
    make(topic, "Eleven", good_cases(11))

    code, data = report(capsys)

    assert code == 1
    assert any("coverage floor" in p for p in data["problems"][0]["problems"])


def test_the_floor_is_configurable(topic, capsys):
    make(topic, "Eight", good_cases(8))

    assert report(capsys, "--min", "8")[0] == 0


# ─────────────────────────────────────────────────────────────
# The hidden-test contract
# ─────────────────────────────────────────────────────────────

def test_missing_expected_output_is_reported(topic, capsys):
    cases = good_cases(12)
    del cases[0]["expected_output"]
    make(topic, "Missing key", cases)

    code, data = report(capsys)

    assert code == 1
    assert any("missing 'expected_output'" in p for p in data["problems"][0]["problems"])


def test_a_suite_with_no_usable_case_is_blocked(topic, capsys):
    make(topic, "No answers at all", [{"stdin": f"in{i}"} for i in range(12)])

    _, data = report(capsys)

    assert data["problems"][0]["status"] == "BLOCKED"


def test_duplicate_inputs_are_reported_with_the_original(topic, capsys):
    """
    A duplicate input inflates the count toward the floor while testing
    nothing new. Naming the earlier case makes it fixable.
    """
    cases = good_cases(12)
    cases[5]["stdin"] = cases[0]["stdin"]
    make(topic, "Padded", cases)

    code, data = report(capsys)

    assert code == 1
    assert any("duplicate 'stdin' (same as case 1)" in p
               for p in data["problems"][0]["problems"])


def test_empty_stdin_is_reported(topic, capsys):
    cases = good_cases(12)
    cases[3]["stdin"] = "   "
    make(topic, "Blank input", cases)

    _, data = report(capsys)

    assert any("'stdin' is empty" in p for p in data["problems"][0]["problems"])


def test_non_string_values_are_reported(topic, capsys):
    """
    Comparison is string-based, so an int here compares unequal to the same
    value read back as text — a case that can never pass.
    """
    cases = good_cases(12)
    cases[2]["expected_output"] = 5
    make(topic, "Unquoted number", cases)

    _, data = report(capsys)

    assert any("must be a string" in p for p in data["problems"][0]["problems"])


def test_unknown_fields_are_reported(topic, capsys):
    cases = good_cases(12)
    cases[1]["answer"] = "leak"
    make(topic, "Stray field", cases)

    _, data = report(capsys)

    assert any("unknown field 'answer'" in p for p in data["problems"][0]["problems"])


def test_oversized_input_is_reported(topic, capsys):
    from groups.hidden_tests import MAX_STDIN_BYTES
    cases = good_cases(12)
    cases[0]["stdin"] = "x" * (MAX_STDIN_BYTES + 1)
    make(topic, "Huge input", cases)

    _, data = report(capsys)

    assert any("exceeds" in p for p in data["problems"][0]["problems"])


def test_malformed_non_object_cases_are_reported(topic, capsys):
    make(topic, "Junk rows", good_cases(11) + ["not-an-object"])

    _, data = report(capsys)

    assert any("not an object" in p for p in data["problems"][0]["problems"])


def test_a_non_list_value_is_blocked_rather_than_crashing(topic, capsys):
    question = make(topic, "Wrong type", [])
    Question.objects.filter(pk=question.pk).update(hidden_test_cases={"oops": True})

    code, data = report(capsys)

    assert code == 1
    assert data["problems"][0]["status"] == "BLOCKED"


# ─────────────────────────────────────────────────────────────
# Aggregate reporting and safety
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
    assert data["census"] == "VERIFIED"


def test_the_command_writes_nothing(topic, capsys):
    """
    Read-only is a safety property, not a convenience: this is the one P2.5
    command intended to be pointed at production.
    """
    question = make(topic, "Untouched", good_cases(4))
    before = Question.objects.get(pk=question.pk).hidden_test_cases

    run(capsys)

    assert Question.objects.get(pk=question.pk).hidden_test_cases == before
    assert Question.objects.count() == 1
    assert ReferenceSolution.objects.count() == 1


def test_the_report_never_contains_a_reference_solution_body(topic, capsys):
    """
    The validator reads reference solutions to check they exist. It must not
    print them — a CI log is a durable, widely-readable place for grading
    truth to end up.
    """
    question = make(topic, "Oracle body", good_cases(12), oracle=False)
    approved_reference(question, language="python",
                       source_code="SECRET_ORACLE_BODY_5521")

    _, out = run(capsys)
    _, json_out = run(capsys, "--json")

    assert "SECRET_ORACLE_BODY_5521" not in out
    assert "SECRET_ORACLE_BODY_5521" not in json_out
