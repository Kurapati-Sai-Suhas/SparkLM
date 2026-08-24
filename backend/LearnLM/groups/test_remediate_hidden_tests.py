"""
Hidden-test repair — the command that can rewrite an answer key's stored form.

Statement repair could get a statement wrong. This one touches the column the
grader compares against, so the tests are weighted toward what it must refuse:
changing an input, changing any other field, or running without a route back.

Local/synthetic database only.
"""

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_hidden_tests as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

ORIGINAL_CASES = [
    {"stdin": "23", "expected_output": '["ad","ae"]'},
    {"stdin": "1", "expected_output": []},
    {"stdin": "9", "expected_output": ["w", "x"]},
]
REPAIRED_CASES = [
    {"stdin": "23", "expected_output": '["ad","ae"]'},
    {"stdin": "1", "expected_output": "[]"},
    {"stdin": "9", "expected_output": '["w","x"]'},
]


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="ht-op", password="pw",
                                    email="h@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="HT Portal")
    made, _ = Topic.objects.get_or_create(
        name="HTTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, cases, content="Statement."):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=json.loads(json.dumps(cases)),
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9500, ORIGINAL_CASES)


@pytest.fixture
def control(db, topic):
    """Stands in for q264 — must never move."""
    return make_question(topic, 264, [{"stdin": "1", "expected_output": "1"}],
                         content="Control.")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="ht-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def cases_file(tmp_path, cases, name="cases.json"):
    path = tmp_path / name
    path.write_text(json.dumps(cases), encoding="utf-8")
    return str(path)


def repair(path, operator, question_id=9500, batch="ht-batch", extra=()):
    call_command("remediate_hidden_tests", "--batch", batch,
                 "--question", str(question_id), "--cases-file", path,
                 "--reason", "adjudication record", "--operator",
                 operator.username, "--local", *extra)


APPLY = ("--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# The repair
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_hidden_tests_change(frozen_batch, question, operator, tmp_path):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == REPAIRED_CASES
    for name in pre_image.CAPTURED_FIELDS:
        if name == "hidden_test_cases":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_the_statement_cannot_change(frozen_batch, question, operator, tmp_path):
    """The mirror of the statement command: this one must not touch content."""
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.content == "Statement."


@pytest.mark.django_db
def test_status_and_trust_state_cannot_change(frozen_batch, question, operator,
                                              tmp_path):
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_suite(frozen_batch, question,
                                                      operator, tmp_path):
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)
    record = QuestionPreImage.objects.get(question=question)
    assert record.hidden_test_cases == ORIGINAL_CASES
    pre_image.verify(record)


@pytest.mark.django_db
def test_rollback_restores_the_original_suite(frozen_batch, question, operator,
                                              tmp_path):
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == REPAIRED_CASES

    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_an_action_records_the_resulting_digest(frozen_batch, question,
                                                operator, tmp_path):
    before = pre_image.live_digest(question)
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)

    action = RemediationAction.objects.get(
        action_class=RemediationAction.CLASS_HIDDEN_TEST_REPAIR)
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)
    assert action.question_id == question.pk
    assert action.applied_by_id == operator.pk
    assert action.post_digest == pre_image.live_digest(question)
    assert action.post_digest != before
    # The before-digest is the pre-image, which the action links to.
    assert action.pre_image_id == record.pk
    assert record.state_digest == before


# ═════════════════════════════════════════════════════════════
# The stdin invariant
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_changing_an_input_is_refused(frozen_batch, question, operator,
                                      tmp_path):
    """
    The dangerous version of a "mechanical" repair: altering what is asked
    while claiming to fix how the answer is stored.
    """
    tampered = json.loads(json.dumps(REPAIRED_CASES))
    tampered[0]["stdin"] = "99"

    with pytest.raises(CommandError, match="changes stdin"):
        repair(cases_file(tmp_path, tampered), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_dry_run_also_refuses_an_input_change(frozen_batch, question,
                                                operator, tmp_path):
    """
    The check must run BEFORE the write, not only inside the transaction.

    A mutation sweep showed the pre-check could be deleted unnoticed: the
    post-write re-check still caught it on apply. But the dry-run is what an
    operator reads before approving, and a dry-run that displays an input
    change as acceptable is how the change gets approved.
    """
    tampered = json.loads(json.dumps(REPAIRED_CASES))
    tampered[0]["stdin"] = "99"

    with pytest.raises(CommandError, match="changes stdin"):
        repair(cases_file(tmp_path, tampered), operator)      # no --apply


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_repair(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The backstop behind `update_fields`, which cannot fire today — so it needs
    a test that makes it fire, or it is an unverified claim.
    """
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 1:
            state["content"] = "silently altered"
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="content changed"):
        repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES, "the write was not reverted"


@pytest.mark.django_db
def test_adding_a_case_is_refused(frozen_batch, question, operator, tmp_path):
    extra_case = REPAIRED_CASES + [{"stdin": "7", "expected_output": "x"}]
    with pytest.raises(CommandError, match="different action class"):
        repair(cases_file(tmp_path, extra_case), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 3


@pytest.mark.django_db
def test_removing_a_case_is_refused(frozen_batch, question, operator, tmp_path):
    with pytest.raises(CommandError, match="different action class"):
        repair(cases_file(tmp_path, REPAIRED_CASES[:2]), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 3


# ═════════════════════════════════════════════════════════════
# Write-ahead and batch rules
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_question_outside_the_batch_is_refused(frozen_batch, topic, operator,
                                                 tmp_path):
    stranger = make_question(topic, 9599, ORIGINAL_CASES)
    with pytest.raises(pre_image.CaptureIncomplete):
        repair(cases_file(tmp_path, REPAIRED_CASES), operator,
               question_id=stranger.pk, extra=APPLY)
    stranger.refresh_from_db()
    assert stranger.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, question, control,
                                      tmp_path):
    batch = RemediationBatch.objects.create(
        batch_key="ht-open", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)

    with pytest.raises(pre_image.CaptureIncomplete):
        repair(cases_file(tmp_path, REPAIRED_CASES), operator,
               batch="ht-open", extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_corrupt_pre_image_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_the_control_question_cannot_be_repaired_into_something_else(
        frozen_batch, control, operator, tmp_path):
    """
    q264 is in the batch, so it HAS a pre-image — the protection is not that
    the command refuses it, but that nothing asks it to, and that any attempt
    is recorded and reversible. What must hold unconditionally is that a repair
    aimed at another question never moves it.
    """
    before = pre_image.live_digest(control)
    repair(cases_file(tmp_path, REPAIRED_CASES), operator, extra=APPLY)
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator, tmp_path):
    repair(cases_file(tmp_path, REPAIRED_CASES), operator)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES
    assert not RemediationAction.objects.exists()


@pytest.mark.django_db
def test_a_no_op_is_refused(frozen_batch, question, operator, tmp_path):
    with pytest.raises(CommandError, match="identical to the stored suite"):
        repair(cases_file(tmp_path, ORIGINAL_CASES), operator, extra=APPLY)


@pytest.mark.django_db
def test_malformed_json_is_refused(frozen_batch, operator, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommandError, match="not valid JSON"):
        repair(str(path), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_case_missing_expected_output_is_refused(frozen_batch, operator,
                                                   tmp_path):
    broken = [{"stdin": "23"}, {"stdin": "1"}, {"stdin": "9"}]
    with pytest.raises(CommandError, match="missing stdin or expected_output"):
        repair(cases_file(tmp_path, broken), operator, extra=APPLY)


@pytest.mark.django_db
def test_an_empty_suite_is_refused(frozen_batch, operator, tmp_path):
    with pytest.raises(CommandError, match="non-empty"):
        repair(cases_file(tmp_path, []), operator, extra=APPLY)


# ═════════════════════════════════════════════════════════════
# Structural and privilege separation
# ═════════════════════════════════════════════════════════════

def test_the_repairable_field_is_exactly_hidden_test_cases():
    assert cmd.REPAIRABLE_FIELD == "hidden_test_cases"


def test_every_save_names_update_fields():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(cmd))
    saves = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves
    for call in saves:
        assert "update_fields" in {k.arg for k in call.keywords}


def test_the_command_cannot_name_the_statement_field():
    import inspect
    source = inspect.getsource(cmd)
    for forbidden in ('"content"', "'content'", '"trust_state"', '"status"'):
        assert forbidden not in source, forbidden


def test_it_uses_its_own_role_list():
    import inspect
    source = inspect.getsource(cmd)
    assert "ALLOWED_HIDDEN_TEST_ROLES" in source
    assert "ALLOWED_REMEDIATION_ROLES" not in source


def test_it_probes_its_own_column():
    """
    The privilege gate must ask about `hidden_test_cases`, not `content`.

    Structural, because the probe only executes against production: a local
    test database is owned by a role holding both columns, so a wrong probe
    passes there for the wrong reason and no behavioural test can see it.
    """
    import inspect
    source = inspect.getsource(cmd)
    assert "HIDDEN_TEST_REPAIR_PROBE" in source
    assert "STATEMENT_REPAIR_PROBE" not in source
    assert "HIDDEN_TEST_REPAIR_FORBIDDEN" in source
    assert "STATEMENT_REPAIR_FORBIDDEN" not in source


def test_the_three_role_lists_are_disjoint():
    """
    Statement repair, hidden-test repair and capture are three privileges.
    Overlap would make the remediation ORDER a convention rather than a
    boundary.
    """
    lists = (ops.ALLOWED_WRITE_ROLES, ops.ALLOWED_REMEDIATION_ROLES,
             ops.ALLOWED_HIDDEN_TEST_ROLES)
    combined = set().union(*lists)
    assert len(combined) == sum(len(item) for item in lists)


def test_the_probes_are_mirror_images():
    """
    Each action class may write exactly one column, and each forbids the
    other's column. That is what makes statement-before-key a privilege.
    """
    assert ops.HIDDEN_TEST_REPAIR_PROBE == (
        ("groups_question", "hidden_test_cases", "UPDATE"),)
    assert ops.STATEMENT_REPAIR_PROBE == (
        ("groups_question", "content", "UPDATE"),)

    hidden_forbidden = {c for _t, c, _p in ops.HIDDEN_TEST_REPAIR_FORBIDDEN if c}
    statement_forbidden = {c for _t, c, _p in ops.STATEMENT_REPAIR_FORBIDDEN if c}
    assert "content" in hidden_forbidden
    assert "hidden_test_cases" in statement_forbidden
