"""
Tier-A static execution-contract reconciliation (M2 P2.7a-1).

The audit proved that `reseed_questions` writes space-separated test data and
never sets `execution_contract_version`, so reseeded questions sit on v1 whose
Python harness parses JSON per line. A genuinely correct solution therefore
cannot pass them. This classifier finds those questions by READING their
stored data — no Judge0, no oracle, no learner code.

Three properties dominate this suite.

**It must not guess.** When a question's own cases disagree about their format,
the honest answer is CONTRACT_MISMATCH, not a majority vote. Picking a winner
would be the same silent decision that produced the current mess.

**It must not migrate.** V2_ONLY is a finding, not an instruction. A test
below asserts the command writes nothing at all.

**It must not leak.** The report is designed to be archived by a scheduled
job, and hidden inputs and expected outputs are grading truth.
"""

import json

import pytest
from django.core.management import call_command

from groups import contract_reconciliation as tier_a
from groups.conftest import approved_reference
from groups.models import CodingPortal, Question, ReferenceSolution, Topic

# The exact formats found in the repository, so these tests fail if the real
# data conventions drift away from what the classifier was built against.
RESEED_STDIN, RESEED_OUT = "2 7 11 15\n9", "0 1"        # ai_services prompt
V1_NATIVE_STDIN, V1_NATIVE_OUT = "[2,7,11,15]\n9", "[0,1]"  # v1 python harness
COMMA_STDIN, COMMA_OUT = "3,2,2,3\n3", "2,2"            # seed_problems.py
SCALAR_STDIN, SCALAR_OUT = "5", "6"                     # readable by both


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Recon Portal")
    t, _ = Topic.objects.get_or_create(
        name="ReconTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return t


def make(topic, title="Q", cases=None, version="v1", boilerplate=None,
         wrapper=None, reference=False):
    question = Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=cases if cases is not None else [],
        boilerplate_code=boilerplate if boilerplate is not None
        else {"python": "class Solution:\n    def solve(self): pass"},
        hidden_wrapper_code=wrapper or {},
        execution_contract_version=version,
    )
    if reference:
        approved_reference(question, language="python", source_code="print(1)")
    return question


def cases_of(stdin, out, n=2):
    return [{"stdin": f"{stdin}", "expected_output": out} for _ in range(n)]


def run(capsys, *args):
    with pytest.raises(SystemExit) as exc:
        call_command("reconcile_execution_contracts", *args)
    return exc.value.code, capsys.readouterr().out


def report(capsys, *args):
    code, out = run(capsys, "--json", *args)
    return code, json.loads(out)


# ─────────────────────────────────────────────────────────────
# Value-level classification
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("2 7 11 15\n9", tier_a.FORM_TOKENS),
    ("[2,7,11,15]\n9", tier_a.FORM_JSON_PER_LINE),
    ("5", tier_a.FORM_JSON_PER_LINE),        # a scalar is valid JSON too
    ("", tier_a.FORM_EMPTY),
    ("   ", tier_a.FORM_EMPTY),
])
def test_input_forms_are_recognised(text, expected):
    assert tier_a.classify_stdin(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("0 1", tier_a.FORM_TOKENS),
    ("[0,1]", tier_a.FORM_JSON),
    ("{\"a\":1}", tier_a.FORM_JSON),
    ("2,2", tier_a.FORM_COMMA),
    ("hello world", tier_a.FORM_TOKENS),
])
def test_output_forms_are_recognised(text, expected):
    assert tier_a.classify_output(text) == expected


# ─────────────────────────────────────────────────────────────
# Question-level contract classification
# ─────────────────────────────────────────────────────────────

def test_reseeded_data_is_classified_v2_only(topic, capsys):
    """
    The defect this phase exists to inventory: reseed's own output format is
    incompatible with v1's Python harness, and reseed leaves the question on
    v1. A correct solution cannot pass.
    """
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), version="v1")

    code, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.V2_ONLY
    assert q["configured_contract"] == "v1"
    assert q["currently_gradable"] is False
    assert q["recommendation"] == tier_a.ELIGIBLE_FOR_V2_REVIEW
    assert code == 1


def test_v1_native_data_is_classified_v1_only(topic, capsys):
    make(topic, cases=cases_of(V1_NATIVE_STDIN, V1_NATIVE_OUT), version="v1")

    _, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.V1_ONLY
    assert q["currently_gradable"] is True
    assert q["recommendation"] == tier_a.KEEP_V1


def test_scalar_data_is_ambiguous_and_safe_either_way(topic, capsys):
    """
    "5" is a valid JSON number AND a single token, so both harnesses grade it
    identically. Forcing a choice here would invent a distinction the data
    does not support.
    """
    make(topic, cases=cases_of(SCALAR_STDIN, SCALAR_OUT), version="v1")

    _, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.AMBIGUOUS_CONTRACT
    assert q["currently_gradable"] is True


def test_a_question_already_on_v2_with_v2_data_is_gradable(topic, capsys):
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), version="v2")

    code, data = report(capsys)

    assert data["questions"][0]["currently_gradable"] is True
    assert code == 0


def test_v1_data_on_a_v2_question_is_not_gradable(topic, capsys):
    """The mirror case — misconfiguration in the other direction."""
    make(topic, cases=cases_of(V1_NATIVE_STDIN, V1_NATIVE_OUT), version="v2")

    _, data = report(capsys)

    assert data["questions"][0]["currently_gradable"] is False


def test_mixed_formats_are_a_mismatch_not_a_majority_vote(topic, capsys):
    """
    Two cases in v2 form and one in v1 form. Guessing that the majority is
    "the real one" is precisely the silent decision this command must not
    make — the question is inconsistent and a human has to look.
    """
    make(topic, cases=[
        {"stdin": RESEED_STDIN, "expected_output": RESEED_OUT},
        {"stdin": RESEED_STDIN, "expected_output": RESEED_OUT},
        {"stdin": V1_NATIVE_STDIN, "expected_output": V1_NATIVE_OUT},
    ])

    _, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.CONTRACT_MISMATCH
    assert q["internally_consistent"] is False
    assert q["recommendation"] == tier_a.BLOCKED


def test_comma_separated_data_fits_neither_generic_harness(topic, capsys):
    make(topic, cases=cases_of(COMMA_STDIN, COMMA_OUT))

    _, data = report(capsys)

    assert data["questions"][0]["implied_contract"] == tier_a.NEITHER
    assert data["questions"][0]["recommendation"] == tier_a.BLOCKED


def test_a_custom_wrapper_question_is_not_judged_by_generic_rules(topic, capsys):
    """
    seed_problems.py's questions are comma-separated AND carry their own
    wrapper, which outranks the version in _build_executable. Classifying
    them against the generic harnesses would report a defect that is not one.
    """
    make(topic, cases=cases_of(COMMA_STDIN, COMMA_OUT),
         wrapper={"python": "CUSTOM {user_code}"})

    _, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.CUSTOM_WRAPPER
    assert q["has_custom_wrapper"] is True
    assert q["recommendation"] == tier_a.CUSTOM_WRAPPER_REVIEW


# ─────────────────────────────────────────────────────────────
# Degenerate and malformed data
# ─────────────────────────────────────────────────────────────

def test_a_question_with_no_tests_is_blocked(topic, capsys):
    make(topic, cases=[])

    code, data = report(capsys)

    assert data["questions"][0]["implied_contract"] == tier_a.MISSING_TESTS
    assert data["questions"][0]["recommendation"] == tier_a.BLOCKED
    assert code == 1


def test_a_single_case_still_classifies(topic, capsys):
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT, n=1))

    _, data = report(capsys)

    assert data["questions"][0]["implied_contract"] == tier_a.V2_ONLY
    assert data["questions"][0]["hidden_test_count"] == 1


@pytest.mark.parametrize("case", [
    "not-an-object",
    {"stdin": "1"},
    {"expected_output": "1"},
    {"stdin": 5, "expected_output": "1"},
    {"stdin": "1", "expected_output": None},
    {"stdin": "   ", "expected_output": "1"},
])
def test_malformed_cases_are_invalid_not_silently_skipped(topic, capsys, case):
    """
    Ignoring a malformed case would let a question look better than it is —
    the count would include rows that can never execute.
    """
    make(topic, cases=[case])

    _, data = report(capsys)

    assert data["questions"][0]["implied_contract"] == tier_a.INVALID_TESTS
    assert data["questions"][0]["recommendation"] == tier_a.BLOCKED


def test_a_malformed_case_beside_a_valid_one_reports_invalid_not_mismatch(
    topic, capsys
):
    """
    A question with one good case and one broken case has a BROKEN CASE — it
    does not have two formats disagreeing. Reporting CONTRACT_MISMATCH would
    send someone to reconcile formats when the actual fix is to repair one row.

    Found by mutation testing: every other malformed-case test uses a single
    case, where the two paths happen to produce the same verdict, so deleting
    the malformed-case branch entirely left the suite green.
    """
    make(topic, cases=[
        {"stdin": RESEED_STDIN, "expected_output": RESEED_OUT},
        {"stdin": "1", "expected_output": None},
    ])

    _, data = report(capsys)

    q = data["questions"][0]
    assert q["implied_contract"] == tier_a.INVALID_TESTS
    assert q["implied_contract"] != tier_a.CONTRACT_MISMATCH
    assert q["recommendation"] == tier_a.BLOCKED


def test_a_non_list_hidden_test_value_is_missing_tests(topic, capsys):
    question = make(topic, cases=[])
    Question.objects.filter(pk=question.pk).update(hidden_test_cases={"oops": 1})

    _, data = report(capsys)

    assert data["questions"][0]["implied_contract"] == tier_a.MISSING_TESTS


# ─────────────────────────────────────────────────────────────
# Boilerplate
# ─────────────────────────────────────────────────────────────

def test_a_question_with_no_boilerplate_is_blocked(topic, capsys):
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), boilerplate={})

    _, data = report(capsys)

    q = data["questions"][0]
    assert tier_a.MISSING_BOILERPLATE in q["blockers"]
    assert q["recommendation"] == tier_a.BLOCKED
    assert q["languages_present"] == []


def test_a_c_template_without_main_is_reported(topic, capsys):
    """
    The defect the audit proved: the generator emits `class Solution` for C++
    and a bare function for C, but both are SELF-CONTAINED and run raw, so a
    template with no main() cannot link.
    """
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), boilerplate={
        "python": "class Solution:\n    def solve(self): pass",
        "cpp": "class Solution {\npublic:\n    int solve(int n) { return 0; }\n};",
    })

    _, data = report(capsys)

    q = data["questions"][0]
    assert tier_a.INVALID_BOILERPLATE in q["blockers"]
    assert q["languages_structurally_valid"]["cpp"] is False
    assert any("main()" in p for p in q["boilerplate_problems"])


def test_a_self_contained_template_with_main_is_accepted(topic, capsys):
    """Positive control — the check must not reject every C++ template."""
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), boilerplate={
        "python": "class Solution:\n    def solve(self): pass",
        "cpp": "#include <bits/stdc++.h>\nint main() { return 0; }",
    })

    _, data = report(capsys)

    assert data["questions"][0]["languages_structurally_valid"]["cpp"] is True
    assert tier_a.INVALID_BOILERPLATE not in data["questions"][0]["blockers"]


def test_a_reflection_language_without_a_solution_class_is_reported(topic, capsys):
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT),
         boilerplate={"python": "def solve(n):\n    pass"})

    _, data = report(capsys)

    assert data["questions"][0]["languages_structurally_valid"]["python"] is False


def test_multiple_languages_are_reported_individually(topic, capsys):
    make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), boilerplate={
        "python": "class Solution:\n    def solve(self): pass",
        "java": "class Solution { public int solve(int n){ return 0; } }",
        "javascript": "class Solution { solve(n){ return 0; } }",
        "c": "int main(void){ return 0; }",
    })

    _, data = report(capsys)

    valid = data["questions"][0]["languages_structurally_valid"]
    assert valid == {"python": True, "java": True, "javascript": True, "c": True}
    assert data["questions"][0]["languages_present"] == ["c", "java", "javascript", "python"]


# ─────────────────────────────────────────────────────────────
# Safety: read-only, no migration, no leakage
# ─────────────────────────────────────────────────────────────

def test_the_command_changes_nothing(topic, capsys):
    """
    Read-only is the property that makes this safe to point at production.
    """
    question = make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), version="v1")
    before = Question.objects.get(pk=question.pk)
    snapshot = (before.execution_contract_version, before.hidden_test_cases,
                before.boilerplate_code, before.hidden_wrapper_code)

    run(capsys)

    after = Question.objects.get(pk=question.pk)
    assert (after.execution_contract_version, after.hidden_test_cases,
            after.boilerplate_code, after.hidden_wrapper_code) == snapshot
    assert Question.objects.count() == 1


def test_v2_only_never_becomes_an_automatic_migration(topic, capsys):
    """
    V2_ONLY is a FINDING. Migration needs boilerplate, an approved oracle,
    hidden tests and the publishability contract — all later phases.
    """
    question = make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT), version="v1")

    _, data = report(capsys)

    assert data["questions"][0]["recommendation"] == tier_a.ELIGIBLE_FOR_V2_REVIEW
    assert Question.objects.get(pk=question.pk).execution_contract_version == "v1"


def test_the_report_never_contains_hidden_inputs_or_expected_outputs(topic, capsys):
    """
    This output is archived by a scheduled job. stdin and expected_output are
    grading truth and must not travel in a report or a CI log.
    """
    make(topic, cases=[{"stdin": "SECRETINPUT4417", "expected_output": "SECRETANSWER9926"}])

    _, table = run(capsys)
    _, json_out = run(capsys, "--json")

    for blob in (table, json_out):
        assert "SECRETINPUT4417" not in blob
        assert "SECRETANSWER9926" not in blob


def test_the_report_never_contains_reference_source(topic, capsys):
    question = make(topic, cases=cases_of(RESEED_STDIN, RESEED_OUT))
    approved_reference(question, language="python",
                       source_code="SECRET_ORACLE_3391")

    _, table = run(capsys)
    _, json_out = run(capsys, "--json")

    assert "SECRET_ORACLE_3391" not in table
    assert "SECRET_ORACLE_3391" not in json_out


# ─────────────────────────────────────────────────────────────
# Census integrity
# ─────────────────────────────────────────────────────────────

def test_an_empty_bank_is_blocked_not_healthy(db, capsys):
    """
    Zero questions means zero failures, which a naive tool reports as success.
    That is how "all green" gets announced about nothing.
    """
    code, data = report(capsys)

    assert code == 2
    assert data["census"] == "BLOCKED"
    assert data["total_questions"] == 0


def test_an_unreachable_database_is_blocked_not_zero_questions(db, capsys):
    """An outage must never read as a clean inventory."""
    code, data = report(capsys, "--database", "does_not_exist")

    assert code == 2
    assert data["census"] == "BLOCKED"
    assert data["total_questions"] is None
    assert "does_not_exist" in data["reason"]


def test_the_summary_counts_every_classification(topic, capsys):
    make(topic, "reseeded", cases_of(RESEED_STDIN, RESEED_OUT), version="v1")
    make(topic, "native", cases_of(V1_NATIVE_STDIN, V1_NATIVE_OUT), version="v1")
    make(topic, "empty", [])
    make(topic, "wrapped", cases_of(COMMA_STDIN, COMMA_OUT),
         wrapper={"python": "X{user_code}"})

    code, data = report(capsys)

    assert data["total_questions"] == 4
    assert data["implied_contract"][tier_a.V2_ONLY] == 1
    assert data["implied_contract"][tier_a.V1_ONLY] == 1
    assert data["implied_contract"][tier_a.MISSING_TESTS] == 1
    assert data["implied_contract"][tier_a.CUSTOM_WRAPPER] == 1
    assert data["recommendation"][tier_a.ELIGIBLE_FOR_V2_REVIEW] == 1
    assert data["census"] == "VERIFIED"
    assert code == 1


def test_active_reference_presence_is_reported(topic, capsys):
    make(topic, "with", cases_of(SCALAR_STDIN, SCALAR_OUT), reference=True)
    make(topic, "without", cases_of(SCALAR_STDIN, SCALAR_OUT))

    _, data = report(capsys)

    assert data["with_active_reference"] == 1


def test_tier_b_fields_are_unknown_not_optimistic(topic, capsys):
    """
    Oracle agreement and determinism need an approved reference and a working
    Judge0. Reporting anything but UNKNOWN would imply a check that never ran.
    """
    make(topic, cases=cases_of(SCALAR_STDIN, SCALAR_OUT), reference=True)

    _, data = report(capsys)

    assert data["questions"][0]["oracle_agrees"] == "UNKNOWN"
    assert data["questions"][0]["oracle_deterministic"] == "UNKNOWN"
