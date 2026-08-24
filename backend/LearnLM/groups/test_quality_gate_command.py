"""
The quality-gate runner (M2 P2.7h-2).

`evaluate_suite` has existed since P2.7h-1 and both `question_review` and
`question_approve` require a `--quality-report` in `QualityOutcome` shape — but
nothing produced one, so the approval path could not be walked even with
perfect oracle evidence. These tests hold the runner to two things: it writes
nothing to the database, and its report is exactly what the approval path
consumes.

Local/synthetic database only.
"""

import ast
import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import hidden_test_quality as quality
from groups.management.commands import _question_trust as trust
from groups.management.commands import quality_gate as cmd
from groups.models import (
    CodingPortal, OracleExecution, Question, QuestionApproval,
    ReferenceSolution, Topic,
)

User = get_user_model()

STARTER = "class Solution:\n    def solve(self, s: str) -> str:\n        return s\n"
WRONG = "class Solution:\n    def solve(self, s: str) -> str:\n        return s[::-1]\n"


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="qg-op", password="pw",
                                    email="q@example.com", is_staff=True)


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="QG Portal")
    topic, _ = Topic.objects.get_or_create(
        name="QGTopic", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9500, title="Gate subject", content="Statement.", topic=topic,
        base_difficulty=1200.0, boilerplate_code={"python": STARTER},
        hidden_test_cases=[{"stdin": "ab", "expected_output": "ab",
                            "category": "typical"}],
        hidden_wrapper_code={}, execution_contract_version="v3")


def spec_file(tmp_path, payload, name="spec.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def valid_spec(question_id=9500):
    return {
        "question": question_id,
        "input_contract": {"is_sequence": True, "description": "one string"},
        "mutants": [{"identifier": "t1-reverses", "tier": 1,
                     "description": "returns the reversed string",
                     "source": WRONG}],
    }


def run(tmp_path, operator, payload=None, extra=(), question_id=9500):
    call_command("quality_gate", "--question", str(question_id),
                 "--operator", operator.username,
                 "--spec", spec_file(tmp_path, payload or valid_spec()),
                 *extra)


# ═════════════════════════════════════════════════════════════
# It writes nothing
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_it_writes_nothing_to_the_database(question, operator, tmp_path,
                                           monkeypatch):
    before = (question.hidden_test_cases, question.status,
              question.trust_state)
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})

    run(tmp_path, operator, extra=("--report-out",
                                   str(tmp_path / "report.json")))

    question.refresh_from_db()
    assert (question.hidden_test_cases, question.status,
            question.trust_state) == before
    assert not QuestionApproval.objects.exists()
    assert not OracleExecution.objects.exists()
    assert not ReferenceSolution.objects.exists()


def test_no_write_call_exists_in_the_command():
    """Structural: no save, no create, no update anywhere in the module."""
    tree = ast.parse(inspect.getsource(cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in (
                "save", "create", "update", "delete", "bulk_create",
                "get_or_create", "update_or_create"), ast.dump(node)


# ═════════════════════════════════════════════════════════════
# The report is what the approval path consumes
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_report_round_trips_into_the_approval_path(question, operator,
                                                       tmp_path, monkeypatch):
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    report = tmp_path / "report.json"

    run(tmp_path, operator, extra=("--report-out", str(report)))

    written = json.loads(report.read_text(encoding="utf-8"))
    # The four keys the approval path reads, plus a provenance block it
    # ignores — see `_write_report`.
    assert set(cmd.REPORT_KEYS) <= set(written)
    assert set(written) - set(cmd.REPORT_KEYS) == {"provenance"}
    outcome = trust.load_quality_outcome(str(report))
    assert outcome.mutant_identifiers == ("t1-reverses",)
    assert outcome.mutant_digest


@pytest.mark.django_db
def test_a_killed_mutant_is_reported_as_killed(question, operator, tmp_path,
                                               monkeypatch):
    """The suite catches the wrong answer, so the mutant dies."""
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    report = tmp_path / "report.json"
    run(tmp_path, operator, extra=("--report-out", str(report)))

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["tier1_kill_rate"] == 1.0


@pytest.mark.django_db
def test_a_surviving_mutant_blocks_the_gate(question, operator, tmp_path,
                                            monkeypatch):
    """The mutant returns what the suite expects, so nothing catches it."""
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ab", "status_id": 3})
    report = tmp_path / "report.json"
    run(tmp_path, operator, extra=("--report-out", str(report)))

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["tier1_kill_rate"] == 0.0
    assert written["blockers"], "a surviving tier-1 mutant must block"


# ═════════════════════════════════════════════════════════════
# The spec is an operator artifact and is validated
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_spec_for_another_question_is_refused(question, operator, tmp_path):
    with pytest.raises(CommandError, match="another"):
        run(tmp_path, operator, payload=valid_spec(question_id=1))


@pytest.mark.django_db
def test_a_spec_with_no_mutants_is_refused(question, operator, tmp_path):
    payload = valid_spec()
    payload["mutants"] = []
    with pytest.raises(CommandError, match="no mutants"):
        run(tmp_path, operator, payload=payload)


@pytest.mark.django_db
def test_malformed_json_is_refused(question, operator, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommandError, match="not valid JSON"):
        call_command("quality_gate", "--question", str(question.pk),
                     "--operator", operator.username, "--spec", str(path))


@pytest.mark.django_db
def test_a_missing_spec_is_refused(question, operator, tmp_path):
    with pytest.raises(CommandError, match="no such spec"):
        call_command("quality_gate", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--spec", str(tmp_path / "absent.json"))


@pytest.mark.django_db
def test_an_unknown_question_is_refused(operator, tmp_path):
    with pytest.raises(CommandError, match="no such question"):
        run(tmp_path, operator, question_id=999999)


@pytest.mark.django_db
def test_a_bad_mutant_tier_is_refused(question, operator, tmp_path):
    payload = valid_spec()
    payload["mutants"][0]["tier"] = 3
    with pytest.raises(CommandError, match="bad mutant"):
        run(tmp_path, operator, payload=payload)


# ═════════════════════════════════════════════════════════════
# Structural-only mode
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_structural_only_executes_nothing(question, operator, tmp_path,
                                          monkeypatch, capsys):
    """
    No mutant may be handed to the runner at all.

    Asserting only that Judge0 is untouched is too weak: a mutation that passed
    the mutant list through anyway still failed every one as EXECUTION_ERROR
    and the run still reported FAIL, so nothing noticed. The observable
    property is that no mutant RESULT exists.
    """
    def explode(*args, **kwargs):
        raise AssertionError("Judge0 was called during a structural-only run")

    monkeypatch.setattr("groups.coding_views._run_on_judge0", explode)
    run(tmp_path, operator, extra=("--structural-only",))

    output = capsys.readouterr().out
    assert "t1-reverses" not in output, "a mutant was evaluated"
    assert "EXECUTION_ERROR" not in output


@pytest.mark.django_db
def test_structural_only_writes_no_report(question, operator, tmp_path):
    report = tmp_path / "never.json"
    run(tmp_path, operator,
        extra=("--structural-only", "--report-out", str(report)))
    assert not report.exists(), "a structural run must not produce a report"


@pytest.mark.django_db
def test_structural_only_cannot_pass(question, operator, tmp_path, capsys):
    """
    No mutant ran, so nothing was measured. A PASS here would be the gate
    reporting success for a question it never tested.
    """
    run(tmp_path, operator, extra=("--structural-only",))
    output = capsys.readouterr().out
    assert "QUALITY_GATE = FAIL" in output


# ═════════════════════════════════════════════════════════════
# The verdict is not a trust state
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_command_never_claims_a_trust_state(question, operator, tmp_path,
                                                monkeypatch, capsys):
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    run(tmp_path, operator)
    output = capsys.readouterr().out

    # The fixture has one case against a floor of 12, so the verdict is FAIL —
    # what matters here is that the command reports a QUALITY_GATE verdict and
    # nothing stronger.
    assert "QUALITY_GATE = " in output
    assert "ORACLE_VERIFIED" in output      # named only to be disclaimed
    assert "not oracle" in output
    assert "trust_state" not in output
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


def test_the_command_cannot_set_a_trust_state():
    source = inspect.getsource(cmd)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in ("trust_state", "status",
                                               "hidden_test_cases"), target.attr


def test_the_question_read_is_routed_through_the_alias():
    tree = ast.parse(inspect.getsource(cmd))
    reads = [node for node in ast.walk(tree)
             if isinstance(node, ast.Attribute) and node.attr == "objects"]
    assert reads
    for node in reads:
        assert any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and c.func.attr == "using" and c.func.value is node
                   for c in ast.walk(tree)), ast.dump(node)


def test_it_uses_the_shared_execution_plan():
    """
    The gate must measure the SAME semantics the grader uses. An earlier phase
    found it bypassing both halves of the execution seam.
    """
    source = inspect.getsource(cmd)
    assert "quality_execution_plan" in source
    assert "plan=" in source


# ═════════════════════════════════════════════════════════════
# Tier rules and the report's provenance (M2 P2.7h-4)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_spec_with_no_tier1_mutant_blocks(question, operator, tmp_path,
                                            monkeypatch):
    """
    Tier 1 is all-or-nothing and cannot be skipped: a suite that has never been
    shown to catch a realistic mistake has not been measured.
    """
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    payload = valid_spec()
    payload["mutants"] = [dict(payload["mutants"][0], tier=2,
                               identifier="t2-only")]
    report = tmp_path / "report.json"
    run(tmp_path, operator, payload=payload, extra=("--report-out", str(report)))

    written = json.loads(report.read_text(encoding="utf-8"))
    assert any("Tier-1" in blocker for blocker in written["blockers"])
    assert written["provenance"]["verdict"] == "FAIL"


@pytest.mark.django_db
def test_a_surviving_tier1_mutant_blocks_even_at_100_percent_tier2(
        question, operator, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ab", "status_id": 3})
    report = tmp_path / "report.json"
    run(tmp_path, operator, extra=("--report-out", str(report)))

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["tier1_kill_rate"] == 0.0
    assert written["provenance"]["verdict"] == "FAIL"
    assert any("Tier-1" in blocker for blocker in written["blockers"])


@pytest.mark.django_db
def test_an_equivalent_mutant_needs_a_written_argument(question, operator,
                                                       tmp_path, monkeypatch):
    """
    Surviving every case is a suite GAP unless a structural reason is given.
    Passing the tests is never itself the argument.
    """
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ab", "status_id": 3})
    payload = valid_spec()
    payload["mutants"] = [
        {"identifier": "t1-real", "tier": 1, "description": "wrong",
         "source": WRONG},
        {"identifier": "t2-equivalent", "tier": 2, "description": "renamed var",
         "source": STARTER,
         "equivalence_argument": "identical behaviour by construction"},
    ]
    report = tmp_path / "report.json"
    run(tmp_path, operator, payload=payload, extra=("--report-out", str(report)))

    outcomes = {entry["identifier"]: entry["outcome"]
                for entry in json.loads(report.read_text(encoding="utf-8"))
                ["provenance"]["results"]}
    assert outcomes["t2-equivalent"] == "EQUIVALENT"
    assert outcomes["t1-real"] == "SURVIVED"


@pytest.mark.django_db
def test_the_report_binds_the_question_digest_and_reference(question, operator,
                                                            tmp_path,
                                                            monkeypatch):
    """
    Four numbers with no statement of what produced them are not evidence.
    """
    from groups import pre_image

    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=STARTER)
    reference.submit_for_review()
    reference.approve(by=operator)
    reference.activate()

    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    report = tmp_path / "report.json"
    run(tmp_path, operator, extra=("--report-out", str(report)))

    provenance = json.loads(report.read_text(encoding="utf-8"))["provenance"]
    assert provenance["question_id"] == question.pk
    assert provenance["question_state_digest"] == pre_image.live_digest(question)
    assert provenance["reference_id"] == reference.pk
    assert provenance["reference_source_hash"] == reference.source_hash
    assert provenance["execution_contract_version"] == "v3"
    assert provenance["case_count"] == len(question.hidden_test_cases)
    assert len(provenance["case_identities"]) == provenance["case_count"]


@pytest.mark.django_db
def test_the_provenance_block_does_not_disturb_the_approval_path(
        question, operator, tmp_path, monkeypatch):
    """`QualityOutcome` reads four keys and ignores the rest."""
    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ba", "status_id": 3})
    report = tmp_path / "report.json"
    run(tmp_path, operator, extra=("--report-out", str(report)))

    outcome = trust.load_quality_outcome(str(report))
    assert outcome.tier1_kill_rate == 1.0
    assert outcome.mutant_identifiers == ("t1-reverses",)
