"""
Early example check (M2 P2.7h-25).

Local/synthetic only. Every test drives a STUB runner — no Judge0 call is made
from this file, and no database row is written by anything under test.

The line these hold: the check runs code and compares one answer, and must
never be mistakable for oracle evidence. q2027's rejected artifact is the
regression fixture, because a validator justified by a real defect is worth
more than one justified by an invented one.
"""

import pytest

from groups import reseed_example_check as ec
from groups.models import CodingPortal, Question, Topic

GOOD_REFERENCE = """class Solution:
    def widgetCount(self, nums: list, target: int) -> int:
        return nums.count(target)
"""


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Example Portal")
    row, _ = Topic.objects.get_or_create(
        name="ExampleTopic", defaults={"structure_type": "flat",
                                       "portal": portal})
    return row


@pytest.fixture
def question(topic):
    return Question.objects.create(
        id=9910, title="Widget Count", topic=topic, base_difficulty=1300.0,
        content="<p>x</p>",
        boilerplate_code={"python": "class Solution:\n"
                                    "    def widgetCount(self, nums: list, "
                                    "target: int) -> int:\n        pass\n"},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")


def reference(source=GOOD_REFERENCE, **kwargs):
    kwargs.setdefault("origin", "llm")
    kwargs.setdefault("provider", "test")
    kwargs.setdefault("prompt_version", "v1")
    return ec.ReferenceCandidate(source, "python", **kwargs)


def stub(stdout="2", status_id=3, status="Accepted", **extra):
    def runner(source, language, stdin=""):
        return {"stdout": stdout, "status_id": status_id, "status": status,
                "stderr": extra.get("stderr", ""),
                "compile_output": extra.get("compile_output", ""),
                **{k: v for k, v in extra.items()
                   if k not in ("stderr", "compile_output")}}
    return runner


# ═════════════════════════════════════════════════════════════
# The verdicts
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_correct_example_passes(question):
    record = ec.check_example(question, reference(), [[1, 2, 2], 2], 2,
                              stub("2"))
    assert record["verdict"] == ec.EXAMPLE_PASS
    assert record["actual_output"] == "2"


@pytest.mark.django_db
def test_a_wrong_output_is_refused(question):
    record = ec.check_example(question, reference(), [[1, 2, 2], 2], 99,
                              stub("2"))
    assert record["verdict"] == ec.EXAMPLE_WRONG_OUTPUT
    assert "99" in record["detail"] and "2" in record["detail"]


@pytest.mark.django_db
def test_a_runtime_error_is_not_a_pass(question):
    record = ec.check_example(
        question, reference(), [[1, 2], 2], 2,
        stub("", status_id=11, status="Runtime Error (NZEC)",
             stderr="boom"))
    assert record["verdict"] == ec.EXAMPLE_RUNTIME_ERROR


@pytest.mark.django_db
def test_an_unavailable_runner_is_unresolved_not_a_pass(question):
    def broken(source, language, stdin=""):
        return {"error": "judge0 unreachable"}

    record = ec.check_example(question, reference(), [[1], 1], 1, broken)
    assert record["verdict"] == ec.EXAMPLE_UNRESOLVED


@pytest.mark.django_db
def test_a_raising_runner_is_unresolved_not_a_pass(question):
    def explodes(source, language, stdin=""):
        raise RuntimeError("network down")

    record = ec.check_example(question, reference(), [[1], 1], 1, explodes)
    assert record["verdict"] == ec.EXAMPLE_UNRESOLVED
    assert "network down" in record["detail"]


@pytest.mark.django_db
def test_an_unreadable_example_is_unresolved(question):
    record = ec.check_example(question, reference(), None, None, stub())
    assert record["verdict"] == ec.EXAMPLE_UNRESOLVED


@pytest.mark.django_db
def test_an_unencodable_input_is_refused(question):
    record = ec.check_example(question, reference(), [object()], 1, stub())
    assert record["verdict"] == ec.EXAMPLE_INVALID_INPUT


# ═════════════════════════════════════════════════════════════
# The q2027 regression
# ═════════════════════════════════════════════════════════════

GAME_REFERENCE = """class Solution:
    def removeColoredPiecesIfBothNeighborsAreTheSameColor(self, colors: str) -> bool:
        def movable(colour):
            return sum(1 for i in range(1, len(colors) - 1)
                       if colors[i - 1] == colors[i] == colors[i + 1] == colour)
        return movable("A") > movable("B")
"""


@pytest.fixture
def game_question(topic):
    return Question.objects.create(
        id=9911, title="Remove Colored Pieces", topic=topic,
        base_difficulty=1300.0, content="<p>x</p>",
        boilerplate_code={"python": GAME_REFERENCE.replace(
            "        def movable", "        pass\n        def movable")},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")


@pytest.mark.django_db
def test_the_q2027_bad_example_is_detected(game_question):
    """
    The mandatory regression. colors="AABAA" claimed true; the middle
    character is B, so Alice has no legal move and the answer is false.
    Every existing gate passed this artifact.
    """
    record = ec.check_example(
        game_question, reference(GAME_REFERENCE), ["AABAA"], True,
        stub("false"),
        starter_source="class Solution:\n    def "
                       "removeColoredPiecesIfBothNeighborsAreTheSameColor("
                       "self, colors: str) -> bool:\n        pass\n")
    assert record["verdict"] == ec.EXAMPLE_WRONG_OUTPUT


@pytest.mark.django_db
def test_a_correct_verdict_does_not_verify_the_explanation(game_question):
    """
    q2027's regenerated artifact computed the right answer and told the
    learner to remove a character that could not be removed. Treating the
    verdict as evidence for the prose is how that ships.
    """
    record = ec.check_example(
        game_question, reference(GAME_REFERENCE), ["AABAAAB"], True,
        stub("true"),
        starter_source="class Solution:\n    def "
                       "removeColoredPiecesIfBothNeighborsAreTheSameColor("
                       "self, colors: str) -> bool:\n        pass\n")

    assert record["verdict"] == ec.EXAMPLE_PASS
    assert ec.explanation_status(record) == ec.EXPLANATION_UNVERIFIED
    assert ec.explanation_status(record, reviewed_by="Suhas") == \
        ec.EXPLANATION_VERIFIED


# ═════════════════════════════════════════════════════════════
# The contract pre-flight
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_contract_that_cannot_bind_is_unresolved_not_an_error(topic):
    """
    q1974's real failure: contract v1 splats a single list argument, so the
    method is called with three arguments. Reporting that as a runtime error
    sends someone hunting a bug in the reference.
    """
    single = Question.objects.create(
        id=9912, title="GCD", topic=topic, base_difficulty=1000.0,
        content="<p>x</p>", boilerplate_code={"python": "class Solution:\n"
                                              "    def gcdOf(self, *args, "
                                              "**kwargs):\n        pass\n"},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")

    record = ec.check_example(
        single, reference(), [[3, 6, 4]], 3, stub("3"),
        starter_source="class Solution:\n    def gcdOf(self, nums: "
                       "list[int]) -> int:\n        pass\n")

    assert record["verdict"] == ec.EXAMPLE_UNRESOLVED
    assert "contract v1 cannot invoke" in record["detail"]
    assert "v3" in record["detail"]


@pytest.mark.django_db
def test_the_preflight_reads_the_artifact_starter_not_the_stored_one(topic):
    """
    Until `declare_signature` runs, the stored starter is still variadic.
    Checking against it asks whether the contract can bind a signature that
    does not exist yet — and answers yes, wrongly.
    """
    from groups import execution_adapter

    stored = "class Solution:\n    def gcdOf(self, *args, **kwargs):\n        pass\n"
    declared = "class Solution:\n    def gcdOf(self, nums: list[int]) -> int:\n        pass\n"

    assert execution_adapter.declared_signature(stored)[1] == []
    assert len(execution_adapter.declared_signature(declared)[1]) == 1

    question = Question.objects.create(
        id=9913, title="GCD2", topic=topic, base_difficulty=1000.0,
        content="<p>x</p>", boilerplate_code={"python": stored},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")

    assert ec.contract_binding_problem(question, stored, [[1, 2]]) is None
    assert ec.contract_binding_problem(question, declared, [[1, 2]])


# ═════════════════════════════════════════════════════════════
# It must not be mistakable for oracle evidence
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_every_record_denies_being_oracle_evidence(question):
    for arguments, claimed, runner in (
            ([[1], 1], 1, stub("1")),
            ([[1], 1], 9, stub("1")),
            ([[1], 1], 1, stub("", status_id=11))):
        record = ec.check_example(question, reference(), arguments, claimed,
                                  runner)
        assert record["evidence_class"] == "EARLY_EXAMPLE_CHECK"
        assert record["is_oracle_evidence"] is False
        assert record["supports_trust_transition"] is False


def test_no_verdict_collides_with_a_lifecycle_value():
    """A developer feeding one of these into promotion finds nothing matches."""
    from groups.models import OracleExecution, Question as Q

    verdicts = {ec.EXAMPLE_PASS, ec.EXAMPLE_WRONG_OUTPUT,
                ec.EXAMPLE_RUNTIME_ERROR, ec.EXAMPLE_INVALID_INPUT,
                ec.EXAMPLE_UNRESOLVED}
    lifecycle = ({value for value, _ in Q.STATUS_CHOICES}
                 | {value for value, _ in Q.TRUST_CHOICES}
                 | {value for value, _ in OracleExecution.STATUS_CHOICES})
    assert not (verdicts & lifecycle)


def test_the_module_writes_nothing():
    """
    Scanned as an AST, not as text. The module's docstring explains at length
    how it differs from `OracleExecution` provenance, and a raw substring
    search flagged its own explanation — the same mistake as an earlier check
    that matched `.update(` inside `digest.update(`.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ec))
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)

    for forbidden in ("record_execution", "OracleExecution", "save", "create",
                      "delete", "bulk_create", "QuestionApproval",
                      "ReseedLedger", "RemediationAction"):
        assert forbidden not in identifiers, forbidden


# ═════════════════════════════════════════════════════════════
# Reference provenance
# ═════════════════════════════════════════════════════════════

def test_an_llm_reference_must_record_its_provenance():
    with pytest.raises(ValueError, match="provider and prompt version"):
        ec.ReferenceCandidate("class Solution:\n    pass\n", origin="llm")


def test_a_reference_is_a_candidate_until_a_human_reads_it():
    candidate = reference()
    assert candidate.status == ec.REFERENCE_CANDIDATE

    reviewed = reference(reviewed_by="Suhas")
    assert reviewed.status == ec.REFERENCE_REVIEWED


def test_an_unknown_origin_is_refused():
    with pytest.raises(ValueError, match="unknown reference origin"):
        ec.ReferenceCandidate("class Solution:\n    pass\n", origin="magic")


def test_the_reference_digest_binds_the_source():
    first = reference().digest
    assert first != reference(GOOD_REFERENCE + "\n# changed\n").digest


# ═════════════════════════════════════════════════════════════
# Extraction and comparison
# ═════════════════════════════════════════════════════════════

def test_an_example_that_cannot_be_read_returns_none():
    assert ec.extract_example("<p>no example here at all</p>",
                              ["nums"]) is None


def test_extraction_reads_assignments_and_arrows():
    html = ("<h3>Example</h3><p>nums = [1, 2, 2], target = 2 → Result: 2</p>")
    assert ec.extract_example(html, ["nums", "target"]) == ([[1, 2, 2], 2], 2)


def test_extraction_reads_booleans():
    html = '<h3>Example</h3><p>colors = "AABAA" → the method returns true</p>'
    assert ec.extract_example(html, ["colors"]) == (["AABAA"], True)


@pytest.mark.parametrize("claimed,actual,same", [
    (2, "2", True), (2, "2\n", True), (2, "1", False),
    (True, "true", True), (True, "false", False), (False, "false", True),
    ([1, 2], "[1, 2]", True), ([1, 2], "[2, 1]", False),
])
def test_output_comparison(claimed, actual, same):
    assert ec._same(claimed, actual) is same
