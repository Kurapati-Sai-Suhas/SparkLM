"""
Trust demotion: ORACLE_VERIFIED -> UNVERIFIED (M2 P2.7h-35).

Local/synthetic only. No production read, no production write.

Before this command, ORACLE_VERIFIED was a one-way door: `question_promote`
refuses a question that is not UNVERIFIED, and `question_status` cannot write
`trust_state` at all. A question whose evidence stopped covering its suite
kept claiming trust and could never re-earn it. These tests hold the
withdrawal narrow enough to be safe to expose.
"""

import ast
import inspect

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import question_demote
from groups.models import (CodingPortal, Question, QuestionApproval,
                           ReferenceSolution, RemediationAction,
                           RemediationBatch, Topic)

User = get_user_model()
BATCH = "demote-batch"


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="demoter", password="pw",
                                    email="d@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Demote Portal")
    row, _ = Topic.objects.get_or_create(
        name="DemoteTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


def make_question(topic, qid=7700, **overrides):
    fields = dict(
        id=qid, title=f"Q{qid}", content="<p>A real statement.</p>",
        topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def f(self, n: int): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1",
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def verified(db, topic, operator):
    """A published, oracle-verified question inside a frozen batch."""
    question = make_question(topic)
    call_command("preimage_capture", "--batch", BATCH,
                 "--questions", str(question.pk), "--purpose", "demotion test",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")
    call_command("preimage_capture", "--batch", BATCH, "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")
    return question


def demote(question, operator, *, expect=Question.TRUST_ORACLE_VERIFIED,
           batch=BATCH, apply=True):
    args = ["question_demote", "--question", str(question.pk),
            "--batch", batch, "--expect-trust", expect,
            "--reason", "suite exposed publicly",
            "--operator", operator.username, "--local"]
    if apply:
        args += ["--apply", "--confirm"]
    call_command(*args)


# ═════════════════════════════════════════════════════════════
# The transition
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_verified_question_can_be_demoted(verified, operator):
    assert verified.is_adaptive_eligible is True

    demote(verified, operator)

    verified.refresh_from_db()
    assert verified.trust_state == Question.TRUST_UNVERIFIED
    assert verified.is_adaptive_eligible is False


@pytest.mark.django_db
def test_demotion_alone_stops_a_question_teaching_the_model(verified, operator):
    """
    The reason demotion need not unpublish. `is_adaptive_eligible` requires
    BOTH, so withdrawing trust is sufficient to stop learner submissions
    reaching the adaptive model.
    """
    demote(verified, operator)

    verified.refresh_from_db()
    assert verified.status == Question.STATUS_PUBLISHED
    assert verified.is_adaptive_eligible is False


@pytest.mark.django_db
def test_an_unverified_question_is_refused(db, topic, operator):
    question = make_question(topic, 7701,
                             status=Question.STATUS_DRAFT,
                             trust_state=Question.TRUST_UNVERIFIED)
    call_command("preimage_capture", "--batch", BATCH, "--questions",
                 str(question.pk), "--purpose", "t", "--operator",
                 operator.username, "--local", "--apply", "--confirm")
    call_command("preimage_capture", "--batch", BATCH, "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")

    # Matching the PRE-LOCK message specifically. The in-lock guard says
    # "between the plan and the lock" instead, so this pins which guard
    # fired -- without that the two mask each other and neither can be
    # shown to be necessary.
    with pytest.raises(CommandError, match="performs exactly"):
        demote(question, operator, expect=Question.TRUST_UNVERIFIED)


@pytest.mark.django_db
def test_a_wrong_expected_state_is_refused(verified, operator):
    """
    The handshake. A demotion planned against ORACLE_VERIFIED must not
    succeed on a question something else already demoted.
    """
    with pytest.raises(CommandError, match="planned against"):
        demote(verified, operator, expect=Question.TRUST_UNVERIFIED)

    verified.refresh_from_db()
    assert verified.trust_state == Question.TRUST_ORACLE_VERIFIED


@pytest.mark.django_db
def test_a_missing_pre_image_is_refused(db, topic, operator, verified):
    other = make_question(topic, 7702)
    with pytest.raises(pre_image.CaptureIncomplete):
        demote(other, operator)
    other.refresh_from_db()
    assert other.trust_state == Question.TRUST_ORACLE_VERIFIED


@pytest.mark.django_db
def test_a_wrong_batch_is_refused(verified, operator):
    with pytest.raises(CommandError, match="no such batch"):
        demote(verified, operator, batch="not-a-batch")


@pytest.mark.django_db
def test_a_dry_run_writes_nothing(verified, operator):
    demote(verified, operator, apply=False)

    verified.refresh_from_db()
    assert verified.trust_state == Question.TRUST_ORACLE_VERIFIED
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_TRUST_DEMOTION).exists()


# ═════════════════════════════════════════════════════════════
# Write scope and audit
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_trust_state_changes(verified, operator):
    before = pre_image.question_state(verified)

    demote(verified, operator)

    verified.refresh_from_db()
    after = pre_image.question_state(verified)
    assert [n for n in before if before[n] != after[n]] == ["trust_state"]


@pytest.mark.django_db
def test_status_is_never_written(verified, operator):
    demote(verified, operator)
    verified.refresh_from_db()
    assert verified.status == Question.STATUS_PUBLISHED


def test_the_command_writes_exactly_one_update_field():
    tree = ast.parse(inspect.getsource(question_demote))
    saves = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "save"]
    assert len(saves) == 1
    keywords = {k.arg: k for k in saves[0].keywords}
    assert [e.id for e in keywords["update_fields"].value.elts] == ["TRUST_FIELD"]
    assert question_demote.TRUST_FIELD == "trust_state"


@pytest.mark.django_db
def test_exactly_one_audit_action_is_recorded(verified, operator):
    demote(verified, operator)

    actions = RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_TRUST_DEMOTION)
    assert actions.count() == 1
    action = actions.get()
    assert action.question_id == verified.pk
    assert action.applied_by_id == operator.pk
    assert action.pre_image is not None
    assert "ORACLE_VERIFIED -> UNVERIFIED" in action.detail
    assert "suite exposed publicly" in action.detail
    verified.refresh_from_db()
    assert action.post_digest == pre_image.live_digest(verified)


def test_trust_demotion_is_its_own_action_class():
    """
    NOT STATUS_TRANSITION. Servability and trust are independent axes; a
    question can be withdrawn from publication and keep verified answers, or
    lose them while still published. Sharing a class would make "this stopped
    being trusted" unfindable in the trail.
    """
    assert RemediationAction.CLASS_TRUST_DEMOTION != \
        RemediationAction.CLASS_STATUS_TRANSITION
    assert RemediationAction.CLASS_TRUST_DEMOTION in \
        dict(RemediationAction.CLASS_CHOICES)


@pytest.mark.django_db
def test_the_approval_is_left_exactly_as_it_was(verified, operator, topic):
    """
    A promotion stamps the approval because it is an event on that approval.
    A demotion is not: the approval records that a person once read and
    vouched for an artifact, and that remains true after trust is withdrawn.
    """
    reference = ReferenceSolution.objects.create(
        question=verified, language="python", source_code="x = 1\n")
    approval = QuestionApproval.objects.create(
        question=verified, reference=reference,
        reference_source_hash="a" * 64, artifact_digest="b" * 64,
        approved_by=operator, approved_at=timezone.now())
    before = (approval.artifact_digest, approval.approved_by_id,
              approval.approved_at, approval.promoted_at)

    demote(verified, operator)

    approval.refresh_from_db()
    assert (approval.artifact_digest, approval.approved_by_id,
            approval.approved_at, approval.promoted_at) == before


# ═════════════════════════════════════════════════════════════
# Narrowness — this must not become a trust setter
# ═════════════════════════════════════════════════════════════

def declared_options():
    """
    The flags the command actually declares, by AST.

    Read from `add_argument` calls rather than the file text: the docstring
    discusses the flags that deliberately do NOT exist, and a substring search
    cannot tell an explanation from a definition.
    """
    tree = ast.parse(inspect.getsource(question_demote))
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(
                        argument.value, str):
                    flags.add(argument.value)
    return flags


def test_there_is_no_arbitrary_target_state_argument():
    """
    A general "set the trust state" command is a promotion path that skips
    every promotion gate. Demotion is safe to expose only because it can
    ONLY remove a claim.
    """
    flags = declared_options()
    for forbidden in ("--to", "--trust-state", "--target-trust",
                      "--set-trust", "--trust"):
        assert forbidden not in flags, forbidden
    assert "--expect-trust" in flags, "the handshake flag must still exist"


def test_the_transition_is_hard_coded_and_one_directional():
    assert question_demote.FROM_STATE == Question.TRUST_ORACLE_VERIFIED
    assert question_demote.TO_STATE == Question.TRUST_UNVERIFIED
    assert question_demote.TO_STATE != Question.TRUST_ORACLE_VERIFIED


def test_it_runs_under_the_trust_owning_role_only():
    source = inspect.getsource(question_demote)
    assert "ALLOWED_DEMOTION_ROLES" in source
    assert ops.ALLOWED_DEMOTION_ROLES == ops.ALLOWED_PROMOTION_ROLES
    assert ops.ALLOWED_DEMOTION_ROLES == frozenset({"learnlm_promote_rw"})


def test_the_probe_asks_for_trust_state_only():
    assert ops.DEMOTION_PROBE == (("groups_question", "trust_state", "UPDATE"),)


@pytest.mark.parametrize("column", [
    "status", "content", "hidden_test_cases", "boilerplate_code",
    "hidden_wrapper_code", "execution_contract_version",
])
def test_every_other_question_column_is_forbidden(column):
    assert ("groups_question", column, "UPDATE") in ops.PROMOTION_FORBIDDEN


# ═════════════════════════════════════════════════════════════
# The guards inside the lock
#
# Each condition is also checked while planning, so in a single-threaded
# test the second check never fires and could be deleted unnoticed. These
# make the state disagree with itself between the two reads, which is the
# only way to show the in-lock guard is load-bearing.
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_question_demoted_between_the_plan_and_the_lock_is_refused(
        verified, operator, monkeypatch):
    """
    The race. Another writer demotes first; this run must not file a second
    withdrawal of a claim that is already gone.
    """
    # `_render_plan` runs AFTER the planning checks and BEFORE the lock is
    # taken, which makes it the exact seam a competing writer would slip
    # through. Demoting the row here reproduces the race against the real
    # database rather than against a patched queryset.
    original = question_demote.Command._render_plan

    def demote_first(self, batch, question, record, before_digest):
        Question.objects.filter(pk=question.pk).update(
            trust_state=Question.TRUST_UNVERIFIED)
        return original(self, batch, question, record, before_digest)

    monkeypatch.setattr(question_demote.Command, "_render_plan", demote_first)

    with pytest.raises(CommandError, match="between the plan and the lock"):
        demote(verified, operator)

    monkeypatch.undo()
    verified.refresh_from_db()
    # the competing writer's demotion stands; THIS run filed nothing
    assert verified.trust_state == Question.TRUST_UNVERIFIED
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_TRUST_DEMOTION).exists()


@pytest.mark.django_db
def test_a_field_changing_during_the_write_reverts_it(verified, operator,
                                                       monkeypatch):
    """
    The substitution proof. If anything outside trust_state moved while the
    row was locked, the transaction must not commit.
    """
    real = Question.refresh_from_db

    def tampering(self, *args, **kwargs):
        real(self, *args, **kwargs)
        self.content = "<p>somebody else wrote this</p>"

    monkeypatch.setattr(Question, "refresh_from_db", tampering)

    with pytest.raises(CommandError, match="changed during a trust demotion"):
        demote(verified, operator)

    monkeypatch.undo()
    verified.refresh_from_db()
    assert verified.trust_state == Question.TRUST_ORACLE_VERIFIED


def test_the_command_actually_passes_its_probe_to_the_gates():
    """
    `test_the_probe_asks_for_trust_state_only` pins the CONSTANT. This pins
    that the command hands it to `run_gates` — emptying that argument left
    the constant correct and the gate toothless.
    """
    tree = ast.parse(inspect.getsource(question_demote))
    gate_calls = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "run_gates"]
    assert len(gate_calls) == 1
    keywords = {k.arg: k.value for k in gate_calls[0].keywords}

    for argument, expected in (("required_privileges", "DEMOTION_PROBE"),
                               ("allowed_roles", "ALLOWED_DEMOTION_ROLES"),
                               ("forbidden_privileges", "PROMOTION_FORBIDDEN")):
        node = keywords[argument]
        assert isinstance(node, ast.Attribute), argument
        assert node.attr == expected, (argument, node.attr)
