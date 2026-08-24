"""
The rollback privilege contract, proven against REAL roles (M2 P2.7).

Rollback wrote every captured field, so it demanded UPDATE on all seven columns
of `groups_question`. The four repair roles hold exactly one column each, so no
connection in the system could perform a restore — and the mocked suites did not
notice, because they run against a test database owned by a role that holds
everything. The undo for ten production writes had never been executable.

These tests build roles with exact grants and become them with `SET LOCAL ROLE`,
so the database decides what is permitted. Each denial runs in its own SAVEPOINT:
a permission error aborts the surrounding transaction, and without one the first
denial would make every later statement fail for the wrong reason.

Local/synthetic database only. Roles are created in the test transaction and
vanish with it.
"""

import ast
import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import preimage_rollback as cmd
from groups.models import (
    CodingPortal, Question, QuestionApproval, QuestionPreImage,
    RemediationAction, RemediationBatch, Topic,
)

User = get_user_model()

ORIGINAL_CASES = [{"stdin": "code", "expected_output": "False"},
                  {"stdin": "abcba", "expected_output": "True"}]
REPAIRED_CASES = [{"stdin": "code", "expected_output": "false"},
                  {"stdin": "abcba", "expected_output": "true"}]
STARTER = "class Solution:\n    def solve(self, s: str): pass\n"


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="rb-op", password="pw",
                                    email="r@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="RB Portal")
    made, _ = Topic.objects.get_or_create(
        name="RBTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, **overrides):
    fields = dict(
        id=question_id, title=f"Q{question_id}", content="Original.",
        topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": STARTER},
        hidden_test_cases=json.loads(json.dumps(ORIGINAL_CASES)),
        hidden_wrapper_code={}, execution_contract_version="v1")
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9600)


@pytest.fixture
def bystander(db, topic):
    """Another batch member that must not be touched."""
    return make_question(topic, 9601, content="Bystander.")


@pytest.fixture
def frozen_batch(db, operator, question, bystander):
    batch = RemediationBatch.objects.create(
        batch_key="rb-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, bystander, operator)
    pre_image.freeze(batch, operator)
    return batch


@pytest.fixture
def repaired(frozen_batch, question, operator):
    """q9600 with its ANSWER FORM repaired — the q266 shape."""
    Question.objects.filter(pk=question.pk).update(
        hidden_test_cases=REPAIRED_CASES)
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)
    RemediationAction.objects.create(
        batch=frozen_batch, question=question, pre_image=record,
        action_class=RemediationAction.CLASS_HIDDEN_TEST_REPAIR,
        detail="casing", post_digest=pre_image.live_digest(question),
        applied_by=operator, applied_at=frozen_batch.frozen_at)
    return question


def make_role(name, grants):
    with connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {name}")
        cursor.execute(f"CREATE ROLE {name} NOLOGIN")
        for grant in grants:
            cursor.execute(grant.format(role=name))
    return name


BASE_GRANTS = [
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT SELECT ON groups_remediationbatch, groups_questionpreimage TO {role}",
    "GRANT SELECT, INSERT ON groups_remediationaction TO {role}",
]


def expect_denied(role, sql, params=()):
    with pytest.raises(ProgrammingError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {role}")
                cursor.execute(sql, params)


def allowed(role, sql, params=()):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {role}")
            cursor.execute(sql, params)
            cursor.execute("RESET ROLE")
    return True


# ═════════════════════════════════════════════════════════════
# A-C: the hidden-test role is sufficient, and needs nothing more
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db(transaction=False)
def test_a_hidden_test_role_can_perform_an_answer_form_rollback(
        repaired, frozen_batch):
    """A. The whole point: undoing a key-form repair needs only that column."""
    role = make_role("test_rb_hidden", BASE_GRANTS + [
        "GRANT UPDATE (hidden_test_cases) ON groups_question TO {role}"])

    record = QuestionPreImage.objects.get(question=repaired)
    fields = pre_image.differing_fields(record, repaired)
    assert fields == ("hidden_test_cases",)

    required = pre_image.required_column_writes(
        [pre_image.RollbackTarget(record, repaired, fields, "", False, False)])
    assert required == (("groups_question", "hidden_test_cases", "UPDATE"),)

    with connection.cursor() as cursor:
        for _table, column, _privilege in required:
            cursor.execute(
                "select has_column_privilege(%s, 'groups_question', %s, "
                "'UPDATE')", [role, column])
            assert cursor.fetchone()[0], column

    allowed(role, "update groups_question set hidden_test_cases = %s "
                  "where id = %s",
            [json.dumps(ORIGINAL_CASES), repaired.pk])


@pytest.mark.django_db(transaction=False)
def test_b_a_role_without_that_column_is_refused_by_the_database(repaired):
    """B."""
    role = make_role("test_rb_nothing", BASE_GRANTS)
    expect_denied(role, "update groups_question set hidden_test_cases = %s "
                        "where id = %s",
                  [json.dumps(ORIGINAL_CASES), repaired.pk])


@pytest.mark.django_db(transaction=False)
def test_c_rollback_does_not_require_insert_on_the_pre_image_table(repaired):
    """
    C. The old gate demanded INSERT on groups_questionpreimage — a privilege
    rollback never uses, and one that would let the restorer forge its own undo.
    """
    role = make_role("test_rb_hidden_noinsert", BASE_GRANTS + [
        "GRANT UPDATE (hidden_test_cases) ON groups_question TO {role}"])

    with connection.cursor() as cursor:
        cursor.execute("select has_table_privilege(%s, "
                       "'groups_questionpreimage', 'INSERT')", [role])
        assert cursor.fetchone()[0] is False

    allowed(role, "update groups_question set hidden_test_cases = %s "
                  "where id = %s",
            [json.dumps(ORIGINAL_CASES), repaired.pk])

    required, _forbidden = ops.rollback_privileges(
        (("groups_question", "hidden_test_cases", "UPDATE"),))
    assert ("groups_questionpreimage", None, "INSERT") not in required
    assert ops.CAPTURE_PROBE not in (required,)


@pytest.mark.django_db(transaction=False)
def test_d_an_over_granted_role_is_refused(repaired):
    """D. A role that can also rewrite statements must not perform this."""
    _required, forbidden = ops.rollback_privileges(
        (("groups_question", "hidden_test_cases", "UPDATE"),))
    forbidden_columns = {column for _t, column, _p in forbidden if column}
    assert "content" in forbidden_columns
    assert "execution_contract_version" in forbidden_columns
    assert "hidden_test_cases" not in forbidden_columns

    role = make_role("test_rb_wide", BASE_GRANTS + [
        "GRANT UPDATE (hidden_test_cases, content) ON groups_question TO {role}"])
    with connection.cursor() as cursor:
        cursor.execute("select has_column_privilege(%s, 'groups_question', "
                       "'content', 'UPDATE')", [role])
        assert cursor.fetchone()[0] is True, "the fixture is not over-granted"


# ═════════════════════════════════════════════════════════════
# E-G: the requirement is derived per question
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_e_a_statement_rollback_requires_content(frozen_batch, question):
    """E. q963-shaped: only the statement moved."""
    Question.objects.filter(pk=question.pk).update(content="Repaired.")
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)

    assert pre_image.differing_fields(record, question) == ("content",)
    targets = pre_image.rollback_plan(frozen_batch, [question.pk])
    assert pre_image.required_column_writes(targets) == (
        ("groups_question", "content", "UPDATE"),)


@pytest.mark.django_db
def test_f_a_multi_field_rollback_requires_every_differing_column(
        frozen_batch, question):
    """F. q1436-shaped: four fields moved, four privileges required."""
    Question.objects.filter(pk=question.pk).update(
        content="Repaired.", hidden_test_cases=REPAIRED_CASES,
        boilerplate_code={"python": STARTER.replace("s: str", "s: list[str]")},
        execution_contract_version="v3")
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)

    assert set(pre_image.differing_fields(record, question)) == {
        "content", "hidden_test_cases", "boilerplate_code",
        "execution_contract_version"}

    targets = pre_image.rollback_plan(frozen_batch, [question.pk])
    required = pre_image.required_column_writes(targets)
    assert {column for _t, column, _p in required} == {
        "content", "hidden_test_cases", "boilerplate_code",
        "execution_contract_version"}
    # and status/trust_state, which did NOT move, are not demanded
    assert "status" not in {column for _t, column, _p in required}


@pytest.mark.django_db(transaction=False)
def test_g_one_missing_privilege_refuses_before_any_write(
        frozen_batch, question, operator):
    """
    G. Three of four columns granted: the restore must not begin.
    """
    Question.objects.filter(pk=question.pk).update(
        content="Repaired.", hidden_test_cases=REPAIRED_CASES,
        execution_contract_version="v3")
    question.refresh_from_db()
    before = pre_image.live_digest(question)

    role = make_role("test_rb_partial", BASE_GRANTS + [
        "GRANT UPDATE (content, hidden_test_cases) ON groups_question TO {role}"])

    targets = pre_image.rollback_plan(frozen_batch, [question.pk])
    required, _forbidden = ops.rollback_privileges(
        pre_image.required_column_writes(targets))

    with connection.cursor() as cursor:
        missing = []
        for _table, column, _privilege in required:
            cursor.execute("select has_column_privilege(%s, 'groups_question', "
                           "%s, 'UPDATE')", [role, column])
            if not cursor.fetchone()[0]:
                missing.append(column)
    assert missing == ["execution_contract_version"]

    expect_denied(role, "update groups_question set "
                        "execution_contract_version = 'v1' where id = %s",
                  [question.pk])
    question.refresh_from_db()
    assert pre_image.live_digest(question) == before, "state moved"


# ═════════════════════════════════════════════════════════════
# H-M: the safety properties that must survive the redesign
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_h_the_command_refuses_an_unknown_alias(repaired, operator):
    with pytest.raises(Exception):
        call_command("preimage_rollback", "--batch", "rb-batch",
                     "--questions", str(repaired.pk), "--operator",
                     operator.username, "--alias", "nonexistent", "--local")


def test_i_the_transaction_is_opened_on_the_batch_alias():
    tree = ast.parse(inspect.getsource(pre_image.rollback))
    atomics = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "atomic"]
    assert atomics
    for call in atomics:
        assert "using" in {keyword.arg for keyword in call.keywords}


def test_j_the_row_is_locked_before_it_is_restored():
    """
    BOTH passes lock: the verification pass, so state cannot move between the
    check and the write, and the apply pass, which re-fetches the row. A
    mutation sweep removed the lock from the apply pass and a "is there a lock
    anywhere" assertion still passed.
    """
    tree = ast.parse(inspect.getsource(pre_image))
    rollback = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_rollback")
    locks = [node for node in ast.walk(rollback)
             if isinstance(node, ast.Attribute)
             and node.attr == "select_for_update"]
    assert len(locks) == 2, f"{len(locks)} locked fetches in _rollback"


@pytest.mark.django_db
def test_j2_a_corrupt_pre_image_refuses_the_restore(frozen_batch, repaired,
                                                    operator):
    """The pre-image must be verified BEFORE anything is written."""
    record = QuestionPreImage.objects.get(question=repaired)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    repaired.refresh_from_db()
    assert repaired.hidden_test_cases == REPAIRED_CASES, "state moved"
    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_ROLLBACK).exists()


@pytest.mark.django_db
def test_k_divergence_still_refuses(frozen_batch, repaired, operator):
    """A question edited AFTER the repair must not be silently overwritten."""
    Question.objects.filter(pk=repaired.pk).update(content="edited later")
    repaired.refresh_from_db()

    with pytest.raises(pre_image.DigestMismatch):
        pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    repaired.refresh_from_db()
    assert repaired.content == "edited later"
    assert repaired.hidden_test_cases == REPAIRED_CASES


@pytest.mark.django_db
def test_l_the_whole_state_digest_is_still_verified(frozen_batch, repaired,
                                                    operator, monkeypatch):
    """
    The narrowed write must still be proved to reproduce the ENTIRE captured
    state. Forced by making the restore write the wrong value.
    """
    real = pre_image.differing_fields
    monkeypatch.setattr(pre_image, "differing_fields",
                        lambda record, question: ())

    with pytest.raises(pre_image.DigestMismatch):
        pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    repaired.refresh_from_db()
    assert repaired.hidden_test_cases == REPAIRED_CASES, "not reverted"
    monkeypatch.setattr(pre_image, "differing_fields", real)


@pytest.mark.django_db
def test_m_an_audit_action_is_appended_per_question(frozen_batch, repaired,
                                                    operator):
    pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    actions = list(RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_ROLLBACK))
    assert len(actions) == 1
    action = actions[0]
    assert action.question_id == repaired.pk
    record = QuestionPreImage.objects.get(question=repaired)
    assert action.pre_image_id == record.pk
    assert action.post_digest == record.state_digest
    assert "hidden_test_cases" in action.detail
    # the repair it undid is still in history
    assert RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_HIDDEN_TEST_REPAIR).exists()


@pytest.mark.django_db
def test_m2_a_multi_question_rollback_records_each_question(
        frozen_batch, repaired, bystander, operator):
    """
    The old code wrote ONE action carrying the FIRST pre-image's digest, which
    reads as a claim about every restored question and is true of one.
    """
    Question.objects.filter(pk=bystander.pk).update(content="Repaired too.")

    pre_image.rollback(frozen_batch, operator)

    actions = RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_ROLLBACK)
    assert actions.count() == 2
    for action in actions:
        record = QuestionPreImage.objects.get(question_id=action.question_id)
        assert action.post_digest == record.state_digest
        assert action.pre_image_id == record.pk


# ═════════════════════════════════════════════════════════════
# N-P: blast radius
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_n_another_question_is_not_touched(frozen_batch, repaired, bystander,
                                           operator):
    before = pre_image.live_digest(bystander)
    pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])
    bystander.refresh_from_db()
    assert pre_image.live_digest(bystander) == before


@pytest.mark.django_db
def test_o_learner_and_trust_records_are_not_touched(frozen_batch, repaired,
                                                     operator):
    """
    ROLLBACK_SCOPE is the question only. An approval or an execution records
    something that HAPPENED; undoing the data does not un-happen it.
    """
    assert pre_image.ROLLBACK_SCOPE == ("groups_question",)
    before = QuestionApproval.objects.count()

    pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    assert QuestionApproval.objects.count() == before
    source = inspect.getsource(pre_image._rollback)
    for model in ("QuestionApproval", "ReferenceSolution", "OracleExecution",
                  "CodeSubmission", "RecommendationLog"):
        assert model not in source, model


@pytest.mark.django_db
def test_p_a_partial_rollback_leaves_the_batch_captured(frozen_batch, repaired,
                                                        bystander, operator):
    """
    Restoring one member of two must not relabel the whole batch — the label
    said the batch had been undone while the other repair still stood, and no
    command could set it back.
    """
    Question.objects.filter(pk=bystander.pk).update(content="Repaired too.")

    pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    frozen_batch.refresh_from_db()
    assert frozen_batch.state == RemediationBatch.STATE_CAPTURED
    assert frozen_batch.frozen_at is not None


@pytest.mark.django_db
def test_p2_a_complete_rollback_does_mark_the_batch(frozen_batch, repaired,
                                                    bystander, operator):
    pre_image.rollback(frozen_batch, operator)
    frozen_batch.refresh_from_db()
    assert frozen_batch.state == RemediationBatch.STATE_ROLLED_BACK


# ═════════════════════════════════════════════════════════════
# The restore itself
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_differing_field_is_written(frozen_batch, repaired, operator,
                                             monkeypatch):
    seen = {}
    real_save = Question.save

    def recording(self, *args, **kwargs):
        if "update_fields" in kwargs:
            seen[self.pk] = list(kwargs["update_fields"])
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Question, "save", recording)
    pre_image.rollback(frozen_batch, operator, questions=[repaired.pk])

    assert seen == {repaired.pk: ["hidden_test_cases"]}
    repaired.refresh_from_db()
    assert repaired.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_question_already_at_its_capture_writes_nothing(frozen_batch,
                                                          bystander, operator,
                                                          monkeypatch):
    seen = []
    real_save = Question.save
    monkeypatch.setattr(
        Question, "save",
        lambda self, *a, **kw: (seen.append(self.pk), real_save(self, *a, **kw))[1])

    pre_image.rollback(frozen_batch, operator, questions=[bystander.pk])
    assert bystander.pk not in seen


@pytest.mark.django_db
def test_the_command_names_its_own_role_list_and_derived_privileges():
    source = inspect.getsource(cmd)
    assert "ALLOWED_ROLLBACK_ROLES" in source
    assert "rollback_privileges" in source
    assert "required_column_writes" in source
    # It must NOT fall back to the capture defaults any more.
    assert "CAPTURE_PROBE" not in source
    assert "ALLOWED_WRITE_ROLES" not in source


def test_the_gate_is_given_both_required_and_forbidden_privileges():
    """
    Structural: the forbidden list only bites against a real production role,
    so no local test can see it dropped from the call. A mutation sweep dropped
    it and every behavioural test still passed.
    """
    tree = ast.parse(inspect.getsource(cmd))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "gate_write_privilege"]
    assert calls, "the command never checks a write privilege"
    production = [call for call in calls
                  if {keyword.arg for keyword in call.keywords}
                  >= {"required", "forbidden"}]
    assert production, "no gate_write_privilege call passes forbidden="


@pytest.mark.django_db
def test_the_command_plans_only_the_selected_questions(frozen_batch, repaired,
                                                       bystander, operator):
    """
    `--questions` must reach the plan. A mutation that ignored it widened the
    derived privilege requirement to the whole batch — and, on a real role,
    would have refused a restore that should have been permitted.
    """
    from io import StringIO
    buffer = StringIO()
    call_command("preimage_rollback", "--batch", "rb-batch",
                 "--questions", str(repaired.pk), "--operator",
                 operator.username, "--local", stdout=buffer)
    output = buffer.getvalue()

    assert f"q{repaired.pk}" in output
    assert f"q{bystander.pk}" not in output
    assert "to restore      1" in output
    assert "UPDATE (hidden_test_cases)" in output


def test_the_rollback_role_list_excludes_capture_and_census():
    assert ops.ALLOWED_ROLLBACK_ROLES == (
        ops.ALLOWED_REMEDIATION_ROLES | ops.ALLOWED_HIDDEN_TEST_ROLES
        | ops.ALLOWED_CONTRACT_ROLES | ops.ALLOWED_BOILERPLATE_ROLES)
    assert "learnlm_preimage_rw" not in ops.ALLOWED_ROLLBACK_ROLES
    assert "learnlm_census_ro" not in ops.ALLOWED_ROLLBACK_ROLES


def test_the_forbidden_list_is_the_complement_of_what_is_written():
    required, forbidden = ops.rollback_privileges(
        (("groups_question", "content", "UPDATE"),))
    assert required == (("groups_question", "content", "UPDATE"),)
    columns = {column for _t, column, _p in forbidden if column}
    assert columns == set(ops.CAPTURED_COLUMNS) - {"content"}
    assert ("groups_question", None, "DELETE") in forbidden
