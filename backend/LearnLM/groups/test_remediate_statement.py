"""
Statement repair — the first command that can change grading truth.

Every test here is about what it must NOT do. A command that repairs a
statement correctly but also touches a hidden test would be worse than one that
does not work at all, because the damage would be invisible until a learner hit
it.

Local/synthetic database only.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_statement as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

ORIGINAL = "Original statement, with a contradiction."
REPAIRED = "Repaired statement, contradiction removed."


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="rem-op", password="pw",
                                    email="r@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Rem Portal")
    made, _ = Topic.objects.get_or_create(
        name="RemTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def question(db, topic):
    return Question.objects.create(
        id=9100, title="Subject", content=ORIGINAL, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=[{"stdin": "110", "expected_output": "3"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def control(db, topic):
    return Question.objects.create(
        id=9101, title="Control", content="Untouched.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, n: int): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="rem-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


@pytest.fixture
def statement_file(tmp_path):
    path = tmp_path / "statement.txt"
    path.write_text(REPAIRED, encoding="utf-8")
    return str(path)


def repair(statement_file, operator, extra=()):
    call_command("remediate_statement", "--batch", "rem-batch",
                 "--question", "9100", "--content-file", statement_file,
                 "--reason", "adjudication record", "--operator",
                 operator.username, "--local", *extra)


# ═════════════════════════════════════════════════════════════
# The write-ahead rule
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_no_pre_image_means_no_repair(db, topic, operator, statement_file,
                                      frozen_batch):
    """The rule the whole tooling exists to enforce."""
    stranger = Question.objects.create(
        id=9199, title="Stranger", content="x", topic=topic,
        base_difficulty=1200.0, boilerplate_code={}, hidden_test_cases=[],
        hidden_wrapper_code={}, execution_contract_version="v1")

    with pytest.raises(pre_image.CaptureIncomplete):
        call_command("remediate_statement", "--batch", "rem-batch",
                     "--question", str(stranger.pk), "--content-file",
                     statement_file, "--reason", "x", "--operator",
                     operator.username, "--local", "--apply", "--confirm")

    stranger.refresh_from_db()
    assert stranger.content == "x"


@pytest.mark.django_db
def test_an_unfrozen_batch_blocks_the_repair(db, operator, question, control,
                                             statement_file):
    batch = RemediationBatch.objects.create(
        batch_key="open-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)

    with pytest.raises(pre_image.CaptureIncomplete):
        call_command("remediate_statement", "--batch", "open-batch",
                     "--question", "9100", "--content-file", statement_file,
                     "--reason", "x", "--operator", operator.username,
                     "--local", "--apply", "--confirm")

    question.refresh_from_db()
    assert question.content == ORIGINAL


@pytest.mark.django_db
def test_a_corrupt_pre_image_blocks_the_repair(frozen_batch, question,
                                               operator, statement_file):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        repair(statement_file, operator, ("--apply", "--confirm"))

    question.refresh_from_db()
    assert question.content == ORIGINAL


# ═════════════════════════════════════════════════════════════
# Dry-run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_dry_run_is_the_default_and_writes_nothing(frozen_batch, question,
                                                   operator, statement_file):
    repair(statement_file, operator)
    question.refresh_from_db()
    assert question.content == ORIGINAL
    assert not RemediationAction.objects.exists()


# ═════════════════════════════════════════════════════════════
# The repair itself
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_statement_is_repaired(frozen_batch, question, operator,
                                   statement_file):
    repair(statement_file, operator, ("--apply", "--confirm"))
    question.refresh_from_db()
    assert question.content == REPAIRED


@pytest.mark.django_db
def test_nothing_but_the_statement_changes(frozen_batch, question, operator,
                                           statement_file):
    """
    The property that matters most. A repair that also moved a hidden test
    would be invisible until a learner hit it.
    """
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    repair(statement_file, operator, ("--apply", "--confirm"))

    question.refresh_from_db()
    for name in pre_image.CAPTURED_FIELDS:
        if name == "content":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_the_control_question_is_untouched(frozen_batch, question, control,
                                           operator, statement_file):
    before = pre_image.live_digest(control)
    repair(statement_file, operator, ("--apply", "--confirm"))
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original(frozen_batch, question,
                                                operator, statement_file):
    """The route back must survive the repair, or rollback is a fiction."""
    repair(statement_file, operator, ("--apply", "--confirm"))
    record = QuestionPreImage.objects.get(question=question)
    assert record.content == ORIGINAL
    pre_image.verify(record)


@pytest.mark.django_db
def test_rollback_restores_the_original_statement(frozen_batch, question,
                                                  operator, statement_file):
    repair(statement_file, operator, ("--apply", "--confirm"))
    question.refresh_from_db()
    assert question.content == REPAIRED

    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.content == ORIGINAL


@pytest.mark.django_db
def test_an_action_is_recorded_with_both_digests(frozen_batch, question,
                                                 operator, statement_file):
    before = pre_image.live_digest(question)
    repair(statement_file, operator, ("--apply", "--confirm"))

    action = RemediationAction.objects.get(
        action_class=RemediationAction.CLASS_STATEMENT_REPAIR)
    question.refresh_from_db()
    assert action.question_id == question.pk
    assert action.applied_by_id == operator.pk
    assert action.detail == "adjudication record"
    assert action.post_digest == pre_image.live_digest(question)
    assert action.post_digest != before


@pytest.mark.django_db
def test_the_repair_is_atomic(frozen_batch, question, operator, statement_file,
                              monkeypatch):
    """
    The content write and the audit record are one unit. If the record fails,
    the change must not survive — otherwise grading truth moves with nothing
    saying who moved it or why.
    """
    def exploding(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(pre_image, "record_action", exploding)

    with pytest.raises(RuntimeError):
        repair(statement_file, operator, ("--apply", "--confirm"))

    question.refresh_from_db()
    assert question.content == ORIGINAL, (
        "the statement change survived a failed audit write; the repair is "
        "not atomic")
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_STATEMENT_REPAIR).exists()


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_whole_repair(
        frozen_batch, question, operator, statement_file, monkeypatch):
    """
    The backstop behind `update_fields`. It cannot fire today — which is
    exactly why it needs a test that makes it fire, or it is an unverified
    claim rather than a guard.

    `question_state` is made to report a moved `trust_state` on the read AFTER
    the write. The command must abort and revert rather than accept it.
    """
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 1:                       # the post-write read
            state["trust_state"] = "ORACLE_VERIFIED"
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="trust_state changed"):
        repair(statement_file, operator, ("--apply", "--confirm"))

    question.refresh_from_db()
    assert question.content == ORIGINAL, "the write was not reverted"


@pytest.mark.django_db
def test_a_no_op_repair_is_refused(frozen_batch, question, operator, tmp_path):
    """Recording a repair that changes nothing would pollute the audit trail."""
    same = tmp_path / "same.txt"
    same.write_text(ORIGINAL, encoding="utf-8")
    with pytest.raises(CommandError, match="byte-identical"):
        repair(str(same), operator, ("--apply", "--confirm"))


@pytest.mark.django_db
def test_an_empty_statement_is_refused(frozen_batch, operator, tmp_path):
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n", encoding="utf-8")
    with pytest.raises(CommandError, match="empty"):
        repair(str(blank), operator, ("--apply", "--confirm"))


@pytest.mark.django_db
def test_a_missing_file_is_refused(frozen_batch, operator):
    with pytest.raises(CommandError, match="no such content file"):
        repair("does-not-exist.txt", operator, ("--apply", "--confirm"))


@pytest.mark.django_db
def test_a_reason_is_required(frozen_batch, operator, statement_file):
    with pytest.raises(CommandError):
        call_command("remediate_statement", "--batch", "rem-batch",
                     "--question", "9100", "--content-file", statement_file,
                     "--operator", operator.username, "--local",
                     "--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# Structural guarantees
# ═════════════════════════════════════════════════════════════

def test_the_command_can_only_write_the_content_field():
    """
    By AST: every `save()` in this module must name
    `update_fields=[REPAIRABLE_FIELD]`. A save without update_fields writes
    every column.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cmd))
    saves = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves, "expected a save call"
    for call in saves:
        keywords = {k.arg for k in call.keywords}
        assert "update_fields" in keywords, "a save without update_fields "\
                                            "writes every column"


def test_the_repairable_field_is_exactly_content():
    assert cmd.REPAIRABLE_FIELD == "content"


def test_the_command_names_no_grading_data_field():
    """
    Statement repair precedes key repair. This command must be incapable of
    the second half of that order.
    """
    import inspect
    source = inspect.getsource(cmd)
    for forbidden in ("hidden_test_cases", "expected_output", "trust_state",
                      "adaptive_eligible", "execution_contract_version"):
        assert f'"{forbidden}"' not in source, forbidden
        assert f"'{forbidden}'" not in source, forbidden


def test_it_uses_the_remediation_role_list_not_the_capture_one():
    """
    Capture and remediation are different privileges. Reusing the capture list
    would grant the capture role a write it exists to make unnecessary.
    """
    import inspect
    source = inspect.getsource(cmd)
    assert "ALLOWED_REMEDIATION_ROLES" in source
    assert "ALLOWED_WRITE_ROLES" not in source


@pytest.mark.django_db
def test_the_remediation_role_list_is_separate_and_narrow():
    assert ops.ALLOWED_REMEDIATION_ROLES == frozenset({"learnlm_remediate_rw"})
    assert not (ops.ALLOWED_REMEDIATION_ROLES & ops.ALLOWED_WRITE_ROLES)


@pytest.mark.django_db
def test_the_capture_role_is_refused_for_remediation(monkeypatch):
    """The role that preserves the question must not be able to change it."""
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_preimage_rw", "PG 17"))
    with pytest.raises(ops.GateFailure, match="not an authorized"):
        ops.gate_writing_role("default",
                              allowed=ops.ALLOWED_REMEDIATION_ROLES)


@pytest.mark.django_db
def test_the_owner_is_refused_for_remediation(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "neondb_owner", "PG 17"))
    with pytest.raises(ops.GateFailure, match="neondb_owner"):
        ops.gate_writing_role("default",
                              allowed=ops.ALLOWED_REMEDIATION_ROLES)


@pytest.mark.django_db
def test_the_remediation_role_is_accepted(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_remediate_rw", "PG 17"))
    assert ops.gate_writing_role(
        "default", allowed=ops.ALLOWED_REMEDIATION_ROLES) == "learnlm_remediate_rw"
