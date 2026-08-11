"""
Oracle execution, output contract, and reconciliation (M2 P2.5, Phase 7).

The absolute rule these tests exist to enforce:

    LLM output           != grading truth
    human-written output != grading truth
    ReferenceSolution execution == grading truth

Everything here follows from that. The oracle must run the reference through
the LEARNER's execution path or its answers are unreachable by any submission;
it must REFUSE on failure or nondeterminism rather than guess; and
reconciliation must report disagreement without touching a row, because
overwriting a mismatch is only correct if the oracle is right, and no oracle
in this system has been reviewed yet.

Judge0 is stubbed throughout — there is no API key in this environment, and
the runner is an injected seam precisely so both the grader and the oracle can
be driven by one stub.
"""

import json

import pytest
from django.core.management import call_command

from groups import output_contract
from groups.hidden_tests import MIN_HIDDEN_TESTS
from groups.models import CodingPortal, Question, ReferenceSolution, Topic
from groups.oracle import (
    OracleFailed, OracleNondeterministic, OracleService, OracleUnavailable,
    canonical_reference, canonical_reference_problem,
)
from groups.output_contract import (
    LANGUAGE_AGNOSTIC, REQUIRES_REVIEW, UNKNOWN,
    UNSUPPORTED_FOR_CURRENT_CONTRACT, classify_outputs,
)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Oracle Portal")
    t, _ = Topic.objects.get_or_create(
        name="OracleTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return t


def make(topic, title="Oracle Problem", cases=None, oracle=True, language="python"):
    question = Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=cases if cases is not None else [], hidden_wrapper_code={},
    )
    if oracle:
        ReferenceSolution.objects.create(
            question=question, language=language, source_code="print(input())"
        )
    return question


def accepting(stdout):
    """A runner that always succeeds with `stdout` (or a per-stdin mapping)."""
    def runner(source, language, stdin):
        value = stdout(stdin) if callable(stdout) else stdout
        return {"status": "Accepted", "status_id": 3, "stdout": value,
                "stderr": "", "compile_output": "", "time": "0.01", "memory": 1000}
    return runner


# ─────────────────────────────────────────────────────────────
# Oracle execution
# ─────────────────────────────────────────────────────────────

def test_the_oracle_runs_the_reference_through_the_learner_execution_path(topic):
    """
    The property that makes oracle output reachable by a learner at all: the
    reference is wrapped by `GradingService._build_executable`, the same
    function that wraps a submission. Wrapping it differently would mint
    expected outputs no submission could reproduce.
    """
    question = make(topic)
    reference = canonical_reference(question)
    seen = {}

    def runner(source, language, stdin):
        seen["source"] = source
        seen["language"] = language
        return {"status": "Accepted", "status_id": 3, "stdout": "ok",
                "stderr": "", "compile_output": "", "time": "0", "memory": 1}

    OracleService(runner).run(question, reference, "1", verify_determinism=False)

    from groups.services import GradingService
    expected, _ = GradingService._build_executable(
        question, reference.language, reference.source_code
    )
    assert seen["source"] == expected
    assert seen["language"] == "python"


def test_the_oracle_normalizes_with_the_graders_own_normalizer(topic):
    """
    A looser normalizer during generation than during grading would mint
    expected outputs the grader then rejects.
    """
    question = make(topic)
    reference = canonical_reference(question)

    result = OracleService(accepting("  42  \r\n")).run(
        question, reference, "1", verify_determinism=False
    )

    assert result == "42"


def test_a_reference_that_fails_to_run_raises_rather_than_returning(topic):
    """
    A reference that cannot execute cannot define the answer. Returning
    whatever it printed would bake its failure into grading truth.
    """
    question = make(topic)
    reference = canonical_reference(question)

    def failing(source, language, stdin):
        return {"status": "Compilation Error", "status_id": 6, "stdout": "",
                "stderr": "", "compile_output": "boom", "time": None, "memory": None}

    with pytest.raises(OracleFailed, match="did not run cleanly"):
        OracleService(failing).run(question, reference, "1")


def test_an_unreachable_execution_service_is_distinguishable_from_a_bad_reference(topic):
    """
    Transient infrastructure failure and a broken reference call for opposite
    responses — retry versus block the problem — so they must not collapse
    into one exception.
    """
    question = make(topic)
    reference = canonical_reference(question)

    def unavailable(source, language, stdin):
        return {"error": "Judge0 timed out. Try again."}

    with pytest.raises(OracleUnavailable):
        OracleService(unavailable).run(question, reference, "1")


def test_nondeterministic_output_raises_instead_of_picking_a_run(topic):
    """
    If the answer is not a function of the input alone, no single stored
    expected_output can be correct. Picking either run makes grading a coin
    flip for every learner who attempts the problem.
    """
    question = make(topic)
    reference = canonical_reference(question)
    outputs = iter(["1", "2"])

    def flaky(source, language, stdin):
        return {"status": "Accepted", "status_id": 3, "stdout": next(outputs),
                "stderr": "", "compile_output": "", "time": "0", "memory": 1}

    with pytest.raises(OracleNondeterministic):
        OracleService(flaky).run(question, reference, "1")


def test_determinism_verification_runs_the_reference_twice(topic):
    question = make(topic)
    reference = canonical_reference(question)
    calls = []

    def counting(source, language, stdin):
        calls.append(stdin)
        return {"status": "Accepted", "status_id": 3, "stdout": "same",
                "stderr": "", "compile_output": "", "time": "0", "memory": 1}

    OracleService(counting).run(question, reference, "5")

    assert calls == ["5", "5"]


def test_run_many_preserves_input_order(topic):
    question = make(topic)
    reference = canonical_reference(question)

    pairs = OracleService(accepting(lambda s: f"out-{s}")).run_many(
        question, reference, ["a", "b", "c"], verify_determinism=False
    )

    assert pairs == [("a", "out-a"), ("b", "out-b"), ("c", "out-c")]


# ─────────────────────────────────────────────────────────────
# Canonical oracle selection
# ─────────────────────────────────────────────────────────────

def test_exactly_one_active_reference_is_canonical(topic):
    question = make(topic)

    assert canonical_reference(question).language == "python"
    assert canonical_reference_problem(question) is None


def test_no_active_reference_has_no_canonical_oracle(topic):
    question = make(topic, oracle=False)

    assert canonical_reference(question) is None
    assert "no active reference solution" in canonical_reference_problem(question)


def test_two_active_references_are_refused_rather_than_chosen_between(topic):
    """
    The schema allows one active row PER LANGUAGE; the product contract allows
    one canonical oracle per PROBLEM. Silently picking one would let row order
    determine every expected output the problem ever receives.
    """
    question = make(topic, language="python")
    ReferenceSolution.objects.create(
        question=question, language="cpp", source_code="int main(){}"
    )

    assert canonical_reference(question) is None
    assert "exactly one canonical oracle" in canonical_reference_problem(question)


def test_a_superseded_reference_does_not_make_the_oracle_ambiguous(topic):
    question = make(topic, language="python")
    ReferenceSolution.objects.create(
        question=question, language="cpp", source_code="x", is_active=False
    )

    assert canonical_reference(question).language == "python"


# ─────────────────────────────────────────────────────────────
# Output contract
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "42", "-7", "0", "0 1", "1 2 3 4 5", "YES", "NO", "IMPOSSIBLE",
    "hello world", "abc",
])
def test_language_agnostic_outputs_are_accepted(value):
    status, reasons = classify_outputs([value])

    assert status == LANGUAGE_AGNOSTIC, f"{value!r} -> {reasons}"


@pytest.mark.parametrize("value,fragment", [
    ("3.14", "floating-point"),
    ("0.30000000000000004", "floating-point"),
    ("None", "null literal"),
    ("null", "null literal"),
])
def test_outputs_with_no_canonical_textual_form_are_unsupported(value, fragment):
    """
    Floats and nulls have no representation a learner could reproduce in five
    languages, so the PROBLEM needs restating — more tests cannot fix it.
    """
    status, reasons = classify_outputs([value])

    assert status == UNSUPPORTED_FOR_CURRENT_CONTRACT
    assert any(fragment in r for r in reasons)


@pytest.mark.parametrize("value,fragment", [
    ("true", "boolean literal"),
    ("false", "boolean literal"),
    ("[1, 2]", "bracketed container"),
    ("(1, 2)", "bracketed container"),
])
def test_formatting_dependent_outputs_require_review(value, fragment):
    """
    A judgement call, not an automatic rejection: a problem that explicitly
    specifies `true`/`false` as its output format is fine, one that inherited
    it from Python's print is not. A human decides.
    """
    status, reasons = classify_outputs([value])

    assert status == REQUIRES_REVIEW
    assert any(fragment in r for r in reasons)


def test_nothing_to_classify_is_unknown_not_agnostic():
    """An absent verdict, never an optimistic one."""
    assert classify_outputs([])[0] == UNKNOWN
    assert classify_outputs([None, 5])[0] == UNKNOWN


def test_one_bad_line_condemns_a_multi_line_output():
    status, _ = classify_outputs(["1\n2\n3.5"])

    assert status == UNSUPPORTED_FOR_CURRENT_CONTRACT


def test_classification_reads_the_questions_stored_outputs(topic):
    question = make(topic, cases=[
        {"stdin": "1", "expected_output": "1"},
        {"stdin": "2", "expected_output": "true"},
    ])

    status, reasons = output_contract.classify_question(question)

    assert status == REQUIRES_REVIEW
    assert any("boolean" in r for r in reasons)


def test_oracle_output_is_weighed_alongside_stored_outputs(topic):
    """
    Oracle output is the stronger evidence: it is what the canonical solution
    ACTUALLY prints, where a stored value is only what someone claimed.
    """
    question = make(topic, cases=[{"stdin": "1", "expected_output": "1"}])

    status, _ = output_contract.classify_question(question, oracle_outputs=["2.5"])

    assert status == UNSUPPORTED_FOR_CURRENT_CONTRACT


def test_the_problem_statement_is_not_keyword_matched(topic):
    """
    Explicitly NOT rejected on prose. "return true if..." says nothing about
    what the program prints; matching it would flag correct problems while
    missing ones whose statement is silent.
    """
    question = make(topic, cases=[{"stdin": "1", "expected_output": "YES"}])
    question.content = "Return true if the array contains a duplicate, else false."
    question.save(update_fields=["content"])

    assert output_contract.classify_question(question)[0] == LANGUAGE_AGNOSTIC


# ─────────────────────────────────────────────────────────────
# Reconciliation — read-only
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def stub_runner(monkeypatch):
    """Injects a stub in place of the real Judge0 runner."""
    def install(runner):
        from groups.management.commands import reconcile_hidden_tests as mod
        monkeypatch.setattr(mod.Command, "get_runner", lambda self: runner)
    return install


def reconcile(capsys, *args):
    with pytest.raises(SystemExit) as exc:
        call_command("reconcile_hidden_tests", "--json", *args)
    return exc.value.code, json.loads(capsys.readouterr().out)


def statuses(report, question_id=None):
    return [
        case["status"]
        for entry in report["problems"]
        if question_id is None or entry["id"] == question_id
        for case in entry["cases"]
    ]


def test_agreeing_outputs_are_reported_as_match(topic, stub_runner, capsys):
    make(topic, cases=[{"stdin": "a", "expected_output": "1"},
                       {"stdin": "b", "expected_output": "1"}])
    stub_runner(accepting("1"))

    code, report = reconcile(capsys)

    assert code == 0
    assert statuses(report) == ["MATCH", "MATCH"]
    assert report["mismatched"] == 0


def test_a_disagreeing_output_is_reported_as_mismatch(topic, stub_runner, capsys):
    make(topic, cases=[{"stdin": "a", "expected_output": "WRONG"}])
    stub_runner(accepting("1"))

    code, report = reconcile(capsys)

    assert code == 1
    assert statuses(report) == ["MISMATCH"]


def test_reconciliation_never_modifies_stored_test_data(topic, stub_runner, capsys):
    """
    The core safety property. Overwriting a mismatch is only correct if the
    oracle is right, and no oracle here has been reviewed yet.
    """
    question = make(topic, cases=[{"stdin": "a", "expected_output": "WRONG"}])
    before = Question.objects.get(pk=question.pk).hidden_test_cases
    stub_runner(accepting("1"))

    reconcile(capsys)

    assert Question.objects.get(pk=question.pk).hidden_test_cases == before


def test_the_report_never_echoes_the_stored_answer_key(topic, stub_runner, capsys):
    """
    This output is designed to be archived by a scheduled job, and the stored
    value is the answer key. The case number is enough to find it.
    """
    make(topic, cases=[{"stdin": "a", "expected_output": "SECRETKEY7781"}])
    stub_runner(accepting("1"))

    _, report = reconcile(capsys)

    assert "SECRETKEY7781" not in json.dumps(report)


def test_a_problem_without_a_canonical_oracle_is_no_oracle(topic, stub_runner, capsys):
    make(topic, cases=[{"stdin": "a", "expected_output": "1"}], oracle=False)
    stub_runner(accepting("1"))

    code, report = reconcile(capsys)

    assert code == 1
    assert statuses(report) == ["NO_ORACLE"]


def test_a_broken_reference_is_oracle_error_not_mismatch(topic, stub_runner, capsys):
    """
    A reference that crashes tells us nothing about the stored output.
    Reporting MISMATCH would blame the test data for the oracle's failure.
    """
    make(topic, cases=[{"stdin": "a", "expected_output": "1"}])
    stub_runner(lambda s, l, i: {"status": "Runtime Error", "status_id": 11,
                                 "stdout": "", "stderr": "boom",
                                 "compile_output": "", "time": None, "memory": None})

    code, report = reconcile(capsys)

    assert statuses(report) == ["ORACLE_ERROR"]


def test_nondeterminism_is_oracle_error(topic, stub_runner, capsys):
    make(topic, cases=[{"stdin": "a", "expected_output": "1"}])
    outputs = iter(["1", "9"])
    stub_runner(lambda s, l, i: {"status": "Accepted", "status_id": 3,
                                 "stdout": next(outputs), "stderr": "",
                                 "compile_output": "", "time": "0", "memory": 1})

    _, report = reconcile(capsys)

    assert statuses(report) == ["ORACLE_ERROR"]
    assert "nondeterministic" in report["problems"][0]["cases"][0]["detail"]


def test_a_malformed_case_is_not_sent_to_the_oracle(topic, stub_runner, capsys):
    """Executing a case with no input would waste a blocking Judge0 call."""
    make(topic, cases=[{"expected_output": "1"}])
    calls = []
    stub_runner(lambda s, l, i: calls.append(i) or {
        "status": "Accepted", "status_id": 3, "stdout": "1", "stderr": "",
        "compile_output": "", "time": "0", "memory": 1})

    _, report = reconcile(capsys)

    assert statuses(report) == ["MALFORMED"]
    assert calls == []


def test_a_repeated_input_is_reported_as_duplicate(topic, stub_runner, capsys):
    make(topic, cases=[{"stdin": "a", "expected_output": "1"},
                       {"stdin": "a", "expected_output": "1"}])
    stub_runner(accepting("1"))

    _, report = reconcile(capsys)

    assert statuses(report) == ["MATCH", "DUPLICATE"]


def test_a_non_agnostic_oracle_output_is_a_contract_error(topic, stub_runner, capsys):
    """
    The oracle ran fine and agrees, but what it prints cannot be graded
    fairly across five languages — a different failure from MISMATCH.
    """
    make(topic, cases=[{"stdin": "a", "expected_output": "3.5"}])
    stub_runner(accepting("3.5"))

    _, report = reconcile(capsys)

    assert statuses(report) == ["OUTPUT_CONTRACT_ERROR"]


def test_an_empty_bank_is_blocked(db, stub_runner, capsys):
    stub_runner(accepting("1"))

    code, report = reconcile(capsys)

    assert code == 2
    assert report["census"] == "BLOCKED"


def test_an_unreachable_database_is_blocked(db, stub_runner, capsys):
    stub_runner(accepting("1"))

    code, report = reconcile(capsys, "--database", "nope")

    assert code == 2
    assert report["total_problems"] is None


def test_a_single_question_can_be_reconciled(topic, stub_runner, capsys):
    """Judge0 is a blocking call; reconciling one problem must be possible."""
    first = make(topic, "First", cases=[{"stdin": "a", "expected_output": "1"}])
    make(topic, "Second", cases=[{"stdin": "b", "expected_output": "1"}])
    stub_runner(accepting("1"))

    _, report = reconcile(capsys, "--question", str(first.pk))

    assert report["total_problems"] == 1
    assert report["problems"][0]["id"] == first.pk


def test_the_coverage_floor_is_not_enforced_by_reconciliation(topic, stub_runner, capsys):
    """
    Separation of concerns: reconciliation answers "are these outputs right",
    the validator answers "are there enough". A problem with two correct cases
    reconciles cleanly and still fails validation.
    """
    make(topic, cases=[{"stdin": "a", "expected_output": "1"},
                       {"stdin": "b", "expected_output": "1"}])
    stub_runner(accepting("1"))

    code, _ = reconcile(capsys)

    assert code == 0
    assert MIN_HIDDEN_TESTS == 12
