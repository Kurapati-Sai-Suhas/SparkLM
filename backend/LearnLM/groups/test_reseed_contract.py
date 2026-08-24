"""
The reseed contract-setting step (M2 P2.7h-27 / Phase 12).

Local/synthetic only. No Judge0 call, no production read, no production write.

The command chooses `execution_contract_version` for a question whose
signature has just been declared and whose suite does not exist yet. That
window is the whole safety argument: v3 changes what a stored expected output
MEANS, so the contract must be fixed before any case is authored and can never
be moved after.

q1974 is the regression fixture throughout — a one-parameter method taking a
list, which contract v1 cannot invoke because its harness splats a JSON array
positionally.
"""

import ast
import hashlib
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from groups import execution_adapter as ea
from groups import pre_image, reseed_authoring
from groups import reseed_contract_census as census
from groups.management.commands import _preimage_ops as ops
from groups.management.commands.reseed_contract import CONTRACT_FIELD
from groups.models import (CodingPortal, OracleExecution, Question,
                           QuestionApproval, ReferenceSolution,
                           RemediationAction, RemediationBatch, ReseedLedger,
                           Topic)


def reference_for(question):
    """A minimal reference OWNED by the question — the ownership trigger
    refuses provenance that crosses questions."""
    return ReferenceSolution.objects.create(
        question=question, language="python",
        source_code="class Solution:\n    def f(self): pass\n")

User = get_user_model()

MARKER = Question.PLACEHOLDER_MARKER
STUB = f"<p>{MARKER} a thing.</p>"

VARIADIC = ("class Solution:\n"
            "    def solve(self, *args, **kwargs):\n"
            "        pass\n")
#: q1974's real declared signature — one parameter, and it is a list.
CONTAINER = ("class Solution:\n"
             "    def findGreatestCommonDivisorOfArray(self, "
             "nums: list[int]) -> int:\n        pass\n")
SCALAR = ("class Solution:\n"
          "    def removeColoredPieces(self, colors: str) -> bool:\n"
          "        pass\n")
TWO = ("class Solution:\n"
       "    def getLucky(self, s: str, k: int) -> int:\n"
       "        pass\n")
KWONLY = ("class Solution:\n"
          "    def solve(self, *, k: int) -> int:\n"
          "        pass\n")
UNANNOTATED = ("class Solution:\n"
               "    def solve(self, a):\n"
               "        pass\n")


# ═════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def operator(db):
    return User.objects.create_user(username="cop", password="pw",
                                    email="cop@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Contract Portal")
    row, _ = Topic.objects.get_or_create(
        name="ContractTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


def make_question(topic, question_id, starter=CONTAINER, **overrides):
    """
    A question mid-reseed: statement written, signature declared, no suite.

    `content` keeps the marker because the PRE-IMAGE is what eligibility is
    anchored to, and the fixture captures the pre-image from this row.
    """
    fields = {
        "id": question_id, "title": f"Q{question_id}", "content": STUB,
        "topic": topic, "base_difficulty": 1200.0,
        "boilerplate_code": {"python": starter},
        "hidden_test_cases": [], "hidden_wrapper_code": {},
        "execution_contract_version": "v1",
        "status": Question.STATUS_DRAFT,
        "trust_state": Question.TRUST_UNVERIFIED,
    }
    fields.update(overrides)
    return Question.objects.create(**fields)


def freeze(operator, questions, batch="contract-batch"):
    call_command("preimage_capture", "--batch", batch,
                 "--questions", *[str(q.pk) for q in questions],
                 "--purpose", "contract step test",
                 "--operator", operator.username,
                 "--local", "--apply", "--confirm")
    call_command("preimage_capture", "--batch", batch, "--freeze",
                 "--operator", operator.username,
                 "--local", "--apply", "--confirm")
    return RemediationBatch.objects.get(batch_key=batch)


def run(question, operator, *, batch="contract-batch", digest=None,
        apply=True, extra=()):
    args = ["reseed_contract", "--batch", batch,
            "--question", str(question.pk),
            "--expect-digest", digest or pre_image.live_digest(question),
            "--reason", "phase 12 test",
            "--operator", operator.username, "--local"]
    if apply:
        args += ["--apply", "--confirm"]
    call_command(*args, *extra)


@pytest.fixture
def ready(db, topic, operator):
    """The happy path: one frozen, captured, signature-declared question."""
    question = make_question(topic, 1974)
    freeze(operator, [question])
    return question


# ═════════════════════════════════════════════════════════════
# 12C — the contract decision
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("starter,expected,verdict", [
    (CONTAINER, "v3", census.V3_REQUIRED),
    (SCALAR, "v1", census.V1_SUFFICIENT),
    (TWO, "v1", census.V1_SUFFICIENT),
])
def test_the_decision_comes_from_the_census_rule(topic, starter, expected,
                                                 verdict):
    question = make_question(topic, 7001, starter=starter)
    assert reseed_authoring.contract_target(question) == (expected, verdict)


@pytest.mark.django_db
def test_the_decision_is_not_a_second_copy_of_the_rule():
    """
    `contract_target` must DELEGATE to the census. A reimplementation here
    would be a second thing to keep true, and Phase 11 is where the rule was
    mutation-tested.
    """
    import inspect

    source = inspect.getsource(reseed_authoring.contract_target)
    assert "v3_requirement" in source
    tree = ast.parse(source.strip())
    # no container/kind reasoning of its own
    assert "CONTAINER_KINDS" not in source
    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr == "SEQUENCE"]


@pytest.mark.django_db
def test_a_custom_wrapper_keeps_v1_and_is_not_migrated(topic):
    """
    `_build_executable` consults the per-question wrapper BEFORE the version,
    so the generic harness never runs and the splat cannot happen. Migrating
    such a question would change a field nothing reads.
    """
    question = make_question(
        topic, 7002, starter=CONTAINER,
        hidden_wrapper_code={"python": "{user_code}\nprint('own harness')"})

    assert reseed_authoring.contract_target(question) == \
        ("v1", census.V1_SUFFICIENT)


@pytest.mark.django_db
def test_an_unclassifiable_signature_yields_no_contract(topic):
    question = make_question(topic, 7003, starter=UNANNOTATED)
    target, verdict = reseed_authoring.contract_target(question)
    assert target is None and verdict == census.UNKNOWN


@pytest.mark.django_db
def test_an_unclassifiable_signature_is_refused_as_manual_review(topic,
                                                                 operator):
    question = make_question(topic, 7004, starter=UNANNOTATED)
    freeze(operator, [question])
    with pytest.raises(CommandError, match="NEEDS_MANUAL_REVIEW"):
        run(question, operator)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


# ═════════════════════════════════════════════════════════════
# 12B — preconditions and refusals
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("overrides,fragment", [
    ({"status": Question.STATUS_PUBLISHED}, "status is PUBLISHED"),
    # DRAFT + ORACLE_VERIFIED is forbidden by a database check constraint
    # (`question_draft_cannot_be_oracle_verified`), so the only reachable
    # oracle-verified state is a published one. Both blockers are reported.
    ({"status": Question.STATUS_PUBLISHED,
      "trust_state": Question.TRUST_ORACLE_VERIFIED}, "trust_state is"),
    ({"hidden_test_cases": [{"stdin": "1", "expected_output": "1"}]},
     "hidden test case"),
])
def test_a_question_with_grading_truth_is_refused(topic, operator, overrides,
                                                  fragment):
    question = make_question(topic, 7010, **overrides)
    freeze(operator, [question])
    with pytest.raises(CommandError, match=fragment):
        run(question, operator)
    assert RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_CONTRACT_DECLARATION).count() == 0


@pytest.mark.django_db
def test_a_question_with_an_approval_is_refused(topic, operator):
    question = make_question(topic, 7011)
    freeze(operator, [question])
    QuestionApproval.objects.create(
        question=question, reference=reference_for(question),
        reference_source_hash="a" * 64, artifact_digest="b" * 64,
        approved_by=operator, approved_at=timezone.now())
    with pytest.raises(CommandError, match="QuestionApproval"):
        run(question, operator)


@pytest.mark.django_db
def test_a_question_with_oracle_evidence_is_refused(topic, operator):
    question = make_question(topic, 7012)
    freeze(operator, [question])
    OracleExecution.objects.create(
        question=question, reference=reference_for(question),
        reference_source_hash="a" * 64, language="python",
        case_digest="c" * 64, input_digest="d" * 64,
        # `oracle_execution_output_digest_matches` enforces that the digest
        # really is sha256 of the output — provenance cannot be faked.
        produced_output="3",
        output_digest=hashlib.sha256(b"3").hexdigest(),
        execution_contract_version="v1", status="SUCCESS",
        executed_at=timezone.now())
    with pytest.raises(CommandError, match="OracleExecution"):
        run(question, operator)


@pytest.mark.django_db
@pytest.mark.parametrize("starter,fragment", [
    (VARIADIC, "still variadic"),
    (KWONLY, "keyword-only"),
])
def test_an_undeclared_signature_is_refused(topic, operator, starter,
                                            fragment):
    """
    The ordering guarantee. Until `declare_signature` runs there is no arity,
    and a contract chosen from no arity is a guess.
    """
    question = make_question(topic, 7013, starter=starter)
    freeze(operator, [question])
    with pytest.raises(CommandError, match=fragment):
        run(question, operator)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_missing_pre_image_is_refused(topic, operator):
    """
    Write-ahead, and it raises BEFORE any gate the command owns: a question
    with no captured pre-image has no reversible ground to stand on, so there
    would be nothing to roll back to.
    """
    question = make_question(topic, 7014)
    other = make_question(topic, 7015)
    freeze(operator, [other])          # batch exists; 7014 is not in it
    with pytest.raises(pre_image.CaptureIncomplete, match="no pre-image"):
        run(question, operator)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_a_wrong_batch_is_refused(ready, operator):
    with pytest.raises(CommandError, match="no such batch"):
        run(ready, operator, batch="not-a-batch")


@pytest.mark.django_db
def test_a_stale_digest_is_refused(ready, operator):
    with pytest.raises(CommandError, match="planned against"):
        run(ready, operator, digest="0" * 64)
    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"


@pytest.mark.django_db
def test_the_contract_is_chosen_once_per_question(ready, operator):
    """
    Idempotency as an explicit refusal, not a silent second audit row. An
    append-only trail that accumulates duplicates cannot answer "when was
    this decided".
    """
    run(ready, operator)
    with pytest.raises(CommandError, match="already has a CONTRACT_DECLARATION"):
        run(ready, operator, digest=pre_image.live_digest(
            Question.objects.get(pk=ready.pk)))
    assert RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_CONTRACT_DECLARATION).count() == 1


@pytest.mark.django_db
def test_a_dry_run_writes_nothing(ready, operator):
    run(ready, operator, apply=False)
    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_contract_blockers_and_signature_blockers_are_disjoint(topic):
    """
    The two stages cover opposite states of the same question by
    construction: `signature_blockers` refuses a starter that already declares
    parameters, this refuses one that does not. No question can be eligible
    for both at once, which is what makes the order unskippable.
    """
    stub = make_question(topic, 7020, starter=VARIADIC)
    declared = make_question(topic, 7021, starter=CONTAINER)

    assert reseed_authoring.signature_blockers(stub) == []
    assert reseed_authoring.contract_blockers(stub) != []

    assert reseed_authoring.signature_blockers(declared) != []
    assert reseed_authoring.contract_blockers(declared) == []


# ═════════════════════════════════════════════════════════════
# 12D — write scope
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_contract_field_changes(ready, operator):
    """Before/after substitution over every captured field."""
    before = pre_image.question_state(ready)

    run(ready, operator)

    ready.refresh_from_db()
    after = pre_image.question_state(ready)
    changed = [name for name in before if before[name] != after[name]]
    assert changed == [CONTRACT_FIELD]
    assert after[CONTRACT_FIELD] == "v3"


@pytest.mark.django_db
def test_the_command_writes_exactly_one_update_field():
    """
    The scope is enforced by `update_fields`, so the literal is asserted
    rather than trusted. A second name here would let the same lock write a
    column the role's grant does not cover.
    """
    import inspect

    from groups.management.commands import reseed_contract

    tree = ast.parse(inspect.getsource(reseed_contract))
    saves = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "save"]
    assert len(saves) == 1
    keywords = {k.arg: k for k in saves[0].keywords}
    assert "update_fields" in keywords
    assert [e.id for e in keywords["update_fields"].value.elts] == \
        ["CONTRACT_FIELD"]
    assert reseed_contract.CONTRACT_FIELD == "execution_contract_version"


@pytest.mark.django_db
def test_a_v1_decision_changes_no_field_but_is_still_audited(topic, operator):
    """
    The subtle half of the design. A V1_SUFFICIENT question already declares
    v1, so the decision writes nothing — and if nothing were recorded, "we
    chose v1" and "we never looked" would be the same row, leaving suite
    authoring unable to tell whether it may start.
    """
    question = make_question(topic, 7030, starter=SCALAR)
    freeze(operator, [question])
    before = pre_image.question_state(question)

    run(question, operator)

    question.refresh_from_db()
    assert pre_image.question_state(question) == before      # nothing moved
    action = RemediationAction.objects.get(
        action_class=RemediationAction.CLASS_CONTRACT_DECLARATION)
    assert action.question_id == question.pk
    assert "v1 -> v1" in action.detail
    assert census.V1_SUFFICIENT in action.detail


@pytest.mark.django_db
def test_a_zero_parameter_starter_is_refused(topic, operator):
    """
    No parameter means no stdin is delivered anywhere, so no contract
    distinction applies. Accepting it would file a decision that means
    nothing, and the ledger would then read CONTRACT_SET for a question
    nobody decided anything about.
    """
    question = make_question(
        topic, 7050,
        starter="class Solution:\n    def solve(self) -> int:\n        pass\n")
    freeze(operator, [question])
    with pytest.raises(CommandError, match="declares no parameters"):
        run(question, operator)
    assert RemediationAction.objects.count() == 0


# ═════════════════════════════════════════════════════════════
# 12B/12J — the checks INSIDE the lock are load-bearing
#
# Every one of these conditions is already checked while planning, so in a
# single-threaded test the second check never fires and could be deleted
# unnoticed. That is exactly the bug class the lock exists for: the plan is
# read outside the transaction, and another writer may act in between. Each
# test below makes the collaborator disagree with itself between the two
# calls, which is the only way to prove the SECOND one is real.
# ═════════════════════════════════════════════════════════════

def diverge_on(monkeypatch, module, name, later, first_calls=1):
    """Return the true answer `first_calls` times, then `later`."""
    real = getattr(module, name)
    seen = {"n": 0}

    def patched(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] > first_calls:
            return later
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, patched)


@pytest.mark.django_db
def test_a_digest_that_moves_between_the_plan_and_the_lock_is_refused(
        ready, operator, monkeypatch):
    digest = pre_image.live_digest(ready)
    diverge_on(monkeypatch, pre_image, "live_digest", "f" * 64)

    with pytest.raises(CommandError, match="between the plan and the lock"):
        run(ready, operator, digest=digest)

    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_eligibility_that_lapses_between_the_plan_and_the_lock_is_refused(
        ready, operator, monkeypatch):
    diverge_on(monkeypatch, reseed_authoring, "contract_blockers",
               ["a suite was authored while this write was being planned"])

    with pytest.raises(CommandError, match="stopped being eligible"):
        run(ready, operator)

    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_decision_that_changes_under_the_lock_is_refused(ready, operator,
                                                           monkeypatch):
    """
    The signature is what the verdict is computed from, and it lives in a
    column ANOTHER role can write. If `declare_signature` moved it between
    the plan and the lock, the planned contract is a decision about a shape
    that no longer exists.
    """
    diverge_on(monkeypatch, reseed_authoring, "contract_target",
               ("v1", census.V1_SUFFICIENT))

    with pytest.raises(CommandError, match="decision changed under the lock"):
        run(ready, operator)

    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


def tamper_on_refresh(monkeypatch, **attributes):
    """Simulate another writer landing between the save and the read-back."""
    real = Question.refresh_from_db

    def patched(self, *args, **kwargs):
        real(self, *args, **kwargs)
        for name, value in attributes.items():
            setattr(self, name, value)

    monkeypatch.setattr(Question, "refresh_from_db", patched)


@pytest.mark.django_db
def test_a_field_changing_during_the_write_reverts_it(ready, operator,
                                                       monkeypatch):
    """
    The substitution proof, and why it is a runtime check rather than a
    comment: if anything outside the contract column moved while the row was
    locked, the transaction must not commit.
    """
    tamper_on_refresh(monkeypatch, content="<p>somebody else wrote this</p>")

    with pytest.raises(CommandError, match="changed during a contract"):
        run(ready, operator)

    monkeypatch.undo()
    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"   # rolled back
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_a_contract_that_did_not_land_reverts_the_write(ready, operator,
                                                         monkeypatch):
    """The write must be verified, not assumed to have happened."""
    tamper_on_refresh(monkeypatch, execution_contract_version="v2")

    with pytest.raises(CommandError, match="but v3 was chosen"):
        run(ready, operator)

    monkeypatch.undo()
    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"
    assert RemediationAction.objects.count() == 0


# ═════════════════════════════════════════════════════════════
# 12E — audit
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_one_successful_write_produces_exactly_one_action(ready, operator):
    run(ready, operator)

    actions = RemediationAction.objects.all()
    assert actions.count() == 1
    action = actions.get()
    assert action.action_class == \
        RemediationAction.CLASS_CONTRACT_DECLARATION
    assert action.question_id == ready.pk
    assert action.applied_by_id == operator.pk
    assert action.pre_image is not None
    assert action.post_digest == pre_image.live_digest(
        Question.objects.get(pk=ready.pk))
    assert "phase 12 test" in action.detail
    assert "v1 -> v3" in action.detail
    assert census.V3_REQUIRED in action.detail


def test_the_audit_class_is_not_contract_repair():
    """
    Both write the same column under the same role, so the class is the ONLY
    thing separating them in the trail. `remediate_contract` justifies its
    write by execution evidence; this one has none by construction, and the
    first question a reviewer asks is which is which.
    """
    assert RemediationAction.CLASS_CONTRACT_DECLARATION != \
        RemediationAction.CLASS_CONTRACT_REPAIR
    assert RemediationAction.CLASS_CONTRACT_DECLARATION in \
        dict(RemediationAction.CLASS_CHOICES)


def test_the_command_does_not_mint_its_own_audit_row():
    """
    Audit rows go through `pre_image.record_action`, which re-reads the
    question and computes the post-digest itself. A command building a
    RemediationAction directly could record a digest of its own choosing.
    """
    import inspect

    from groups.management.commands import reseed_contract

    source = inspect.getsource(reseed_contract)
    assert "record_action" in source
    tree = ast.parse(source)
    created = {n.func.value.attr for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "create"
               and isinstance(n.func.value, ast.Attribute)}
    assert "objects" not in created


# ═════════════════════════════════════════════════════════════
# 12F — ledger
# ═════════════════════════════════════════════════════════════

def test_the_ledger_has_a_contract_stage_that_is_not_complete():
    assert ReseedLedger.STAGE_CONTRACT == "CONTRACT_SET"
    assert ReseedLedger.STAGE_CONTRACT != ReseedLedger.STAGE_COMPLETE
    assert ReseedLedger.STAGE_CONTRACT in dict(ReseedLedger.STAGE_CHOICES)


def test_the_signature_stage_now_advances_to_the_contract_stage():
    assert ReseedLedger.ADVANCES[ReseedLedger.STAGE_SIGNATURE] == \
        ReseedLedger.STAGE_CONTRACT


def test_nothing_advances_out_of_the_contract_stage():
    """
    Deliberate. The next thing that happens to a reseeded question is suite
    authoring, which is not a reseed write and has not been built. Wiring
    CONTRACT_SET -> COMPLETE would let the orchestrator mark questions
    finished on a transition nothing has verified.
    """
    assert ReseedLedger.STAGE_CONTRACT not in ReseedLedger.ADVANCES
    assert ReseedLedger.STAGE_COMPLETE not in ReseedLedger.ADVANCES.values()


@pytest.mark.django_db
def test_derive_stage_reports_the_contract_stage_from_the_trail(ready,
                                                                operator):
    """
    A V1_SUFFICIENT question's contract write is invisible in the row, so the
    stage MUST come from the append-only trail rather than live state.
    """
    batch = RemediationBatch.objects.get(batch_key="contract-batch")

    # statement + signature recorded, contract not yet
    ready.content = "<p>A real statement.</p>"
    ready.save(update_fields=["content"])
    pre_image.record_action(
        batch, ready, RemediationAction.CLASS_STATEMENT_GENERATION, operator)
    pre_image.record_action(
        batch, ready, RemediationAction.CLASS_SIGNATURE_DECLARATION, operator)
    ready.refresh_from_db()
    stage, discrepancies = reseed_authoring.derive_stage(ready, batch)
    assert stage == ReseedLedger.STAGE_SIGNATURE
    assert discrepancies == []

    pre_image.record_action(
        batch, ready, RemediationAction.CLASS_CONTRACT_DECLARATION, operator)
    ready.refresh_from_db()
    stage, discrepancies = reseed_authoring.derive_stage(ready, batch)
    assert stage == ReseedLedger.STAGE_CONTRACT


@pytest.mark.django_db
def test_derive_stage_flags_a_reverted_contract_write(ready, operator):
    """The trail says v3 was chosen; the row says v1. Someone reverted it."""
    batch = RemediationBatch.objects.get(batch_key="contract-batch")
    ready.content = "<p>A real statement.</p>"
    ready.save(update_fields=["content"])
    for cls in (RemediationAction.CLASS_STATEMENT_GENERATION,
                RemediationAction.CLASS_SIGNATURE_DECLARATION,
                RemediationAction.CLASS_CONTRACT_DECLARATION):
        pre_image.record_action(batch, ready, cls, operator)

    ready.refresh_from_db()
    assert ready.execution_contract_version == "v1"   # never actually written
    _stage, discrepancies = reseed_authoring.derive_stage(ready, batch)
    assert any("reverted or overwritten" in d for d in discrepancies)


# ═════════════════════════════════════════════════════════════
# 12G — the q1974 regression
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_q1974_moves_from_v1_to_v3_and_then_binds(ready, operator):
    """
    The whole phase in one test: declared signature -> V3_REQUIRED -> v3 ->
    the argument arrives as ONE list.
    """
    assert ready.execution_contract_version == "v1"
    assert reseed_authoring.contract_target(ready)[1] == census.V3_REQUIRED

    run(ready, operator)

    ready.refresh_from_db()
    assert ready.execution_contract_version == "v3"

    from groups.services import GradingService
    assert GradingService.prepare_stdin(ready, "python", "[3, 6, 4]") == \
        "[[3,6,4]]"

    invocation = ea.build_invocation("[3, 6, 4]", CONTAINER)
    assert invocation.ok
    assert invocation.arguments == [[3, 6, 4]]


def test_v1_produces_the_known_splat_failure():
    """
    The defect the migration exists for, isolated from Judge0. The generic v1
    harness json-parses the whole blob and, seeing a list, calls
    `method(*parsed)` — so a one-parameter method receives three arguments.
    """
    from groups.services import GENERIC_PYTHON_WRAPPER

    assert "target_method(*args)" in GENERIC_PYTHON_WRAPPER or \
        "target_method(*parsed_input)" in GENERIC_PYTHON_WRAPPER, \
        "the v1 harness no longer splats; this test's premise has changed"

    parsed = json.loads("[3, 6, 4]")
    declared = ea.declared_signature(CONTAINER)
    assert len(declared[1]) == 1
    assert len(parsed) == 3            # one parameter, three arguments

    def findGreatestCommonDivisorOfArray(self, nums):
        return nums

    with pytest.raises(TypeError, match="positional argument"):
        findGreatestCommonDivisorOfArray(None, *parsed)


@pytest.mark.django_db
def test_the_v3_envelope_delivers_the_list_and_the_answer_is_three(ready,
                                                                   operator):
    """
    Executed in memory: the envelope v3 builds, fed to the real GCD logic.
    No Judge0, no production.
    """
    import math

    run(ready, operator)
    ready.refresh_from_db()

    from groups.services import GradingService
    envelope = GradingService.prepare_stdin(ready, "python", "[3, 6, 4]")
    arguments = json.loads(envelope)
    assert arguments == [[3, 6, 4]]

    def findGreatestCommonDivisorOfArray(nums):
        return math.gcd(min(nums), max(nums))

    assert findGreatestCommonDivisorOfArray(*arguments) == 3


# ═════════════════════════════════════════════════════════════
# 12H — the five operator-verified pilot signatures, offline
# ═════════════════════════════════════════════════════════════

PILOT = {
    1940: (("class Solution:\n    def sumOfDigitsOfStringAfterConvert("
            "self, s: str, k: int) -> int:\n        pass\n"), "v1"),
    1974: (CONTAINER, "v3"),
    2027: (("class Solution:\n    def removeColoredPiecesIfBothNeighbors"
            "AreTheSameColor(self, colors: str) -> bool:\n        pass\n"),
           "v1"),
    2057: (("class Solution:\n    def checkWhetherTwoStringsAreAlmost"
            "Equivalent(self, word1: str, word2: str) -> bool:\n"
            "        pass\n"), "v1"),
    2290: (("class Solution:\n    def countNumberOfWaysToPlaceHouses("
            "self, n: int) -> int:\n        pass\n"), "v1"),
}


@pytest.mark.django_db
@pytest.mark.parametrize("question_id", sorted(PILOT))
def test_each_pilot_signature_classifies_as_recorded(topic, question_id):
    starter, expected = PILOT[question_id]
    question = make_question(topic, question_id, starter=starter)
    target, _verdict = reseed_authoring.contract_target(question)
    assert target == expected


@pytest.mark.django_db
def test_exactly_one_pilot_question_needs_v3(topic):
    """1 of 5, and it is q1974 — the empirical anchor for the whole phase."""
    needing = [qid for qid, (starter, _) in PILOT.items()
               if reseed_authoring.contract_target(
                   make_question(topic, qid, starter=starter))[0] == "v3"]
    assert needing == [1974]


@pytest.mark.django_db
def test_the_contract_is_decided_after_the_signature_and_before_the_cases(
        topic, operator):
    """
    The lifecycle invariant, asserted as a sequence rather than described.

    Before the signature exists the command refuses; once it exists the
    command acts; and it acts only while the suite is empty. Authoring cases
    first would leave every stored expected output bound to a contract chosen
    afterwards.
    """
    question = make_question(topic, 7040, starter=VARIADIC)
    freeze(operator, [question])

    # 1. signature not yet declared -> refused
    with pytest.raises(CommandError, match="still variadic"):
        run(question, operator)

    # 2. declare_signature's effect, applied directly (that command is not
    #    under test here and must not be run against production paths)
    question.boilerplate_code = {"python": CONTAINER}
    question.save(update_fields=["boilerplate_code"])
    question.refresh_from_db()
    run(question, operator)
    question.refresh_from_db()
    assert question.execution_contract_version == "v3"

    # 3. a suite authored now is bound against v3, and the contract can no
    #    longer be moved by this command
    question.hidden_test_cases = [{"stdin": "[3, 6, 4]", "expected_output": "3"}]
    question.save(update_fields=["hidden_test_cases"])
    question.refresh_from_db()
    assert reseed_authoring.contract_blockers(question) != []


# ═════════════════════════════════════════════════════════════
# 12I — the privilege boundary
# ═════════════════════════════════════════════════════════════

def test_the_command_runs_under_the_contract_role_and_its_probe():
    import inspect

    from groups.management.commands import reseed_contract

    source = inspect.getsource(reseed_contract)
    assert "ALLOWED_CONTRACT_ROLES" in source
    assert "CONTRACT_REPAIR_PROBE" in source
    assert "CONTRACT_REPAIR_FORBIDDEN" in source


def test_the_contract_role_may_write_only_the_contract_column():
    assert ops.CONTRACT_REPAIR_PROBE == (
        ("groups_question", "execution_contract_version", "UPDATE"),)


@pytest.mark.parametrize("column", [
    "content", "hidden_test_cases", "status", "trust_state",
    "boilerplate_code", "hidden_wrapper_code",
])
def test_the_contract_role_is_forbidden_every_other_question_column(column):
    """
    The least-privilege claim, as a runtime check rather than a comment. If a
    column were dropped from this tuple an over-granted role would be trusted
    instead of refused.
    """
    assert ("groups_question", column, "UPDATE") in \
        ops.CONTRACT_REPAIR_FORBIDDEN


@pytest.mark.parametrize("privilege", ["INSERT", "DELETE", "TRUNCATE"])
def test_the_contract_role_may_not_create_or_destroy_questions(privilege):
    assert ("groups_question", None, privilege) in \
        ops.CONTRACT_REPAIR_FORBIDDEN


def test_the_contract_role_is_the_one_remediate_contract_already_uses():
    """
    Shared deliberately. Postgres grants are per column and both commands
    write the same one, so a dedicated role would need identical grants and
    would separate credentials, not capabilities. The separation that matters
    is the precondition, and that is asserted above.
    """
    assert ops.ALLOWED_CONTRACT_ROLES == frozenset({"learnlm_contract_rw"})


# ═════════════════════════════════════════════════════════════
# 13A/13G — migration 0049
#
# Applied to production, so its safety is asserted rather than reviewed by
# eye. The claim being defended is narrow and total: this migration changes
# CHOICES ONLY, and choices have no representation in PostgreSQL.
# ═════════════════════════════════════════════════════════════

import pathlib  # noqa: E402

MIGRATION_0049 = (pathlib.Path(__file__).parent / "migrations"
                  / "0049_reseed_contract_stage.py")

#: Operations that could alter or destroy data. 0049 must contain none.
FORBIDDEN_OPERATIONS = {
    "RunSQL", "RunPython", "RemoveField", "DeleteModel", "AddField",
    "CreateModel", "AlterModelTable", "RenameField", "RenameModel",
    "AddConstraint", "RemoveConstraint", "AddIndex", "RemoveIndex",
}


def migration_operations():
    tree = ast.parse(MIGRATION_0049.read_text(encoding="utf-8"))
    return [node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "migrations"]


def test_0049_contains_no_data_altering_operation():
    used = set(migration_operations())
    assert not (used & FORBIDDEN_OPERATIONS), used & FORBIDDEN_OPERATIONS


def test_0049_is_exactly_two_alter_fields():
    from collections import Counter

    assert Counter(migration_operations()) == Counter({"AlterField": 2})


def test_0049_depends_on_the_expected_predecessor():
    source = MIGRATION_0049.read_text(encoding="utf-8")
    assert "'0048_reseed_action_classes_and_ledger'" in source


@pytest.mark.parametrize("field,length", [
    ("action_class", "max_length=32"),
    ("stage", "max_length=24"),
])
def test_0049_does_not_move_the_column_width(field, length):
    """
    A `max_length` change WOULD be real DDL — `ALTER TYPE varchar(n)` — and
    would stop this being a no-op. Both widths must match 0048 exactly.
    """
    source = MIGRATION_0049.read_text(encoding="utf-8")
    block = source.split(f"name='{field}'")[1].split("),\n")[0]
    assert length in block


def test_0049_preserves_the_ledger_default():
    source = MIGRATION_0049.read_text(encoding="utf-8")
    assert "default='PENDING'" in source


def test_0049_is_required_by_the_deployed_choices():
    """
    The migration is not optional decoration: the two values the Phase 12
    command depends on must be the ones it introduces, so a deployment that
    skips it has models whose choices disagree with the recorded state.
    """
    source = MIGRATION_0049.read_text(encoding="utf-8")
    assert "'CONTRACT_DECLARATION'" in source
    assert "'CONTRACT_SET'" in source

    predecessor = (MIGRATION_0049.parent
                   / "0048_reseed_action_classes_and_ledger.py"
                   ).read_text(encoding="utf-8")
    assert "CONTRACT_DECLARATION" not in predecessor
    assert "CONTRACT_SET" not in predecessor


def test_0049_choices_match_the_models_exactly():
    """
    Generated migrations drift when a model is edited and no migration is
    made. If these disagree, `makemigrations --check` would want another one.
    """
    source = MIGRATION_0049.read_text(encoding="utf-8")
    for value, _label in RemediationAction.CLASS_CHOICES:
        assert f"'{value}'" in source, value
    for value, _label in ReseedLedger.STAGE_CHOICES:
        assert f"'{value}'" in source, value


def test_no_further_migration_is_outstanding():
    """
    `makemigrations --check` in test form: the models and the migration graph
    must agree, or applying 0049 leaves production still out of step.

    Uses the autodetector directly rather than the management command, so it
    compares the model state against the on-disk graph without a connection.
    """
    from django.apps import apps
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    loader = MigrationLoader(None, ignore_no_migrations=True)
    autodetector = MigrationAutodetector(
        loader.project_state(), ProjectState.from_apps(apps))
    changes = autodetector.changes(graph=loader.graph)

    assert "groups" not in changes, [
        str(op) for migration in changes.get("groups", [])
        for op in migration.operations]


def test_remediate_contract_is_not_weakened():
    """Its refusal must still be there, unchanged."""
    import inspect

    from groups.management.commands import remediate_contract

    source = inspect.getsource(remediate_contract)
    assert "the question stores no test cases" in source
    assert "demonstrates that the contract executes" in source
