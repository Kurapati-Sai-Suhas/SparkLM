"""
Contract repair — the command that changes what a question's stored inputs
MEAN when they execute.

Statement repair can get a sentence wrong and hidden-test repair can get an
answer wrong. This one can leave a question declaring a contract it cannot run
under, which is worse than both: the declaration is what the grader and the
oracle each trust. So the tests are weighted toward the feasibility rule and
toward everything the command must refuse.

Local/synthetic database only.
"""

import ast
import inspect

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_contract as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

#: Two declared `str` parameters and one line per parameter — q3309's shape.
ANNOTATED = ("class Solution:\n"
             "    def strStr(self, haystack: str, needle: str) -> int:\n"
             "        pass\n")

#: One UNANNOTATED parameter — q1436's shape, and the one the command refuses.
UNANNOTATED = ("class Solution:\n"
               "    def destCity(self, paths):\n"
               "        pass\n")

BINDABLE_CASES = [
    {"stdin": "hello\nll\n", "expected_output": "2"},
    {"stdin": "abc\na\n", "expected_output": "0"},
]
#: Two blank lines cannot supply two parameters — q3309's case 4.
UNBINDABLE_CASE = {"stdin": "\n\n", "expected_output": "0"}


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="ct-op", password="pw",
                                    email="c@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="CT Portal")
    made, _ = Topic.objects.get_or_create(
        name="CTTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, *, starter=ANNOTATED,
                  cases=None, wrapper=None, version="v1",
                  content="Statement."):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": starter},
        hidden_test_cases=list(cases if cases is not None else BINDABLE_CASES),
        hidden_wrapper_code=wrapper or {},
        execution_contract_version=version)


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9600)


@pytest.fixture
def control(db, topic):
    """Stands in for q264 — must never move."""
    return make_question(topic, 264, content="Control.")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="ct-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def repair(operator, question_id=9600, batch="ct-batch", version="v3",
           extra=()):
    call_command("remediate_contract", "--batch", batch,
                 "--question", str(question_id), "--to-version", version,
                 "--reason", "adjudication record", "--operator",
                 operator.username, "--local", *extra)


APPLY = ("--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# The repair
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_contract_column_changes(frozen_batch, question, operator):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    repair(operator, extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v3"
    for name in pre_image.CAPTURED_FIELDS:
        if name == "execution_contract_version":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_the_statement_cannot_change(frozen_batch, question, operator):
    repair(operator, extra=APPLY)
    question.refresh_from_db()
    assert question.content == "Statement."


@pytest.mark.django_db
def test_the_hidden_tests_cannot_change(frozen_batch, question, operator):
    """
    The mirror of the hidden-test command. A contract migration changes how
    inputs are DELIVERED; the inputs themselves are read-only here.
    """
    repair(operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == BINDABLE_CASES


@pytest.mark.django_db
def test_the_starter_and_wrapper_cannot_change(frozen_batch, question,
                                               operator):
    repair(operator, extra=APPLY)
    question.refresh_from_db()
    assert question.boilerplate_code == {"python": ANNOTATED}
    assert question.hidden_wrapper_code == {}


@pytest.mark.django_db
def test_status_and_trust_state_cannot_change(frozen_batch, question, operator):
    repair(operator, extra=APPLY)
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_contract(frozen_batch, question,
                                                         operator):
    repair(operator, extra=APPLY)
    record = QuestionPreImage.objects.get(question=question)
    assert record.execution_contract_version == "v1"
    pre_image.verify(record)


@pytest.mark.django_db
def test_rollback_restores_the_original_contract(frozen_batch, question,
                                                 operator):
    repair(operator, extra=APPLY)
    question.refresh_from_db()
    assert question.execution_contract_version == "v3"

    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_an_action_records_the_resulting_digest(frozen_batch, question,
                                                operator):
    before = pre_image.live_digest(question)
    repair(operator, extra=APPLY)

    action = RemediationAction.objects.get(
        action_class=RemediationAction.CLASS_CONTRACT_REPAIR)
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)
    assert action.question_id == question.pk
    assert action.applied_by_id == operator.pk
    assert action.post_digest == pre_image.live_digest(question)
    assert action.post_digest != before
    assert action.pre_image_id == record.pk
    assert record.state_digest == before


# ═════════════════════════════════════════════════════════════
# The feasibility rule
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_unannotated_parameter_is_refused(db, topic, operator):
    """
    q1436's blocker. The adapter binds the whole stored JSON text as a STRING
    and says so with a warning; declaring v3 would make that guess official.
    """
    question = make_question(topic, 9601, starter=UNANNOTATED,
                             cases=[{"stdin": '[["A","Z"]]',
                                     "expected_output": "Z"}])
    batch = RemediationBatch.objects.create(
        batch_key="ct-unannotated", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="binds only by guessing"):
        repair(operator, question_id=9601, batch="ct-unannotated", extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_dry_run_also_refuses_an_unbindable_question(db, topic, operator):
    """
    The refusal must happen in the dry-run too. The dry-run is what an operator
    reads before approving; one that displays an impossible migration as
    acceptable is how it gets approved.
    """
    question = make_question(topic, 9602, starter=UNANNOTATED)
    batch = RemediationBatch.objects.create(
        batch_key="ct-dry", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="binds only by guessing"):
        repair(operator, question_id=9602, batch="ct-dry")      # no --apply


@pytest.mark.django_db
def test_a_case_that_cannot_be_expressed_is_refused(db, topic, operator):
    """q3309's case 4: two blank lines cannot supply two parameters."""
    question = make_question(topic, 9603,
                             cases=BINDABLE_CASES + [UNBINDABLE_CASE])
    batch = RemediationBatch.objects.create(
        batch_key="ct-unbindable", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="cannot be expressed"):
        repair(operator, question_id=9603, batch="ct-unbindable", extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_question_with_its_own_wrapper_is_refused(db, topic, operator):
    """
    `wrapper_for()` takes precedence, so declaring v3 would change nothing
    while recording that it repaired something.
    """
    question = make_question(topic, 9604, wrapper={"python": "print(1)"})
    batch = RemediationBatch.objects.create(
        batch_key="ct-wrapper", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="own wrapper"):
        repair(operator, question_id=9604, batch="ct-wrapper", extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_several_public_methods_are_refused(db, topic, operator):
    question = make_question(
        topic, 9605,
        starter=("class Solution:\n"
                 "    def helper(self, s: str) -> int:\n        pass\n"
                 "    def strStr(self, haystack: str, needle: str) -> int:\n"
                 "        pass\n"))
    batch = RemediationBatch.objects.create(
        batch_key="ct-methods", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="public methods"):
        repair(operator, question_id=9605, batch="ct-methods", extra=APPLY)


@pytest.mark.django_db
def test_no_python_starter_is_refused(db, topic, operator):
    question = Question.objects.create(
        id=9606, title="Q9606", content="S", topic=topic, base_difficulty=1200.0,
        boilerplate_code={"java": "class Solution {}"},
        hidden_test_cases=list(BINDABLE_CASES), hidden_wrapper_code={},
        execution_contract_version="v1")
    batch = RemediationBatch.objects.create(
        batch_key="ct-nostarter", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="no python starter"):
        repair(operator, question_id=9606, batch="ct-nostarter", extra=APPLY)


@pytest.mark.django_db
def test_a_question_with_no_cases_is_refused(db, topic, operator):
    question = make_question(topic, 9607, cases=[])
    batch = RemediationBatch.objects.create(
        batch_key="ct-nocases", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="no test cases"):
        repair(operator, question_id=9607, batch="ct-nocases", extra=APPLY)


@pytest.mark.django_db
def test_a_structurally_broken_case_is_refused(db, topic, operator):
    question = make_question(topic, 9608,
                             cases=[{"stdin": ["not", "text"],
                                     "expected_output": "2"}])
    batch = RemediationBatch.objects.create(
        batch_key="ct-broken", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="not structurally usable"):
        repair(operator, question_id=9608, batch="ct-broken", extra=APPLY)


# ═════════════════════════════════════════════════════════════
# Target, write-ahead and batch rules
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_v3_is_a_repair(frozen_batch, question, operator):
    for version in ("v1", "v2", "v4", ""):
        with pytest.raises(CommandError, match="not a repair"):
            repair(operator, version=version, extra=APPLY)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_no_op_is_refused(db, topic, operator):
    question = make_question(topic, 9609, version="v3")
    batch = RemediationBatch.objects.create(
        batch_key="ct-noop", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    with pytest.raises(CommandError, match="already grades under"):
        repair(operator, question_id=9609, batch="ct-noop", extra=APPLY)


@pytest.mark.django_db
def test_a_blank_contract_column_is_treated_as_v1(db, topic, operator):
    """
    A blank column means v1, so migrating it is a real change and must be
    allowed — refusing it would strand every question written before
    versioning existed.
    """
    question = make_question(topic, 9610, version="")
    batch = RemediationBatch.objects.create(
        batch_key="ct-blank", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)

    repair(operator, question_id=9610, batch="ct-blank", extra=APPLY)
    question.refresh_from_db()
    assert question.execution_contract_version == "v3"


@pytest.mark.django_db
def test_a_question_outside_the_batch_is_refused(frozen_batch, topic, operator):
    stranger = make_question(topic, 9699)
    with pytest.raises(pre_image.CaptureIncomplete):
        repair(operator, question_id=stranger.pk, extra=APPLY)
    stranger.refresh_from_db()
    assert stranger.execution_contract_version == "v1"


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="ct-open", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)

    with pytest.raises(pre_image.CaptureIncomplete):
        repair(operator, batch="ct-open", extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_a_corrupt_pre_image_is_refused(frozen_batch, question, operator):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        repair(operator, extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_repair(frozen_batch, question,
                                                       operator, monkeypatch):
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
        repair(operator, extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1", "the write was not reverted"


@pytest.mark.django_db
def test_a_write_that_did_not_land_reverts_the_repair(frozen_batch, question,
                                                      operator, monkeypatch):
    """
    The second backstop: the column must actually hold the declared contract
    afterwards. Like the untouched-field check it cannot fire in normal
    operation — the save just wrote it — so a mutation sweep could delete it
    unnoticed until this test forced it to fire.

    It matters because the value recorded in the `RemediationAction` is what
    rollback later compares against: an action claiming v3 over a row that is
    still v1 would make the audit trail describe a repair that did not happen.
    """
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 2:
            state["execution_contract_version"] = "v1"
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="after the write"):
        repair(operator, extra=APPLY)

    question.refresh_from_db()
    assert question.execution_contract_version == "v1"
    assert not RemediationAction.objects.exists()


@pytest.mark.django_db
def test_the_control_question_is_not_moved(frozen_batch, control, operator):
    before = pre_image.live_digest(control)
    repair(operator, extra=APPLY)
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator):
    repair(operator)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"
    assert not RemediationAction.objects.exists()


# ═════════════════════════════════════════════════════════════
# Structural and privilege separation
# ═════════════════════════════════════════════════════════════

def test_the_repairable_field_is_exactly_the_contract_column():
    assert cmd.REPAIRABLE_FIELD == "execution_contract_version"
    assert cmd.TARGET_CONTRACT == "v3"


def _tree():
    return ast.parse(inspect.getsource(cmd))


def test_every_save_names_only_the_contract_column():
    """
    Structural rather than textual: this command legitimately READS the
    statement, the starter and the cases to decide feasibility, so a
    "the word content must not appear" guard would be both wrong and, as an
    earlier phase found, defeated by its own docstring.
    """
    saves = [node for node in ast.walk(_tree())
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves
    for call in saves:
        keywords = {k.arg: k.value for k in call.keywords}
        assert "update_fields" in keywords
        fields = keywords["update_fields"]
        assert isinstance(fields, ast.List) and len(fields.elts) == 1
        only = fields.elts[0]
        assert isinstance(only, ast.Name) and only.id == "REPAIRABLE_FIELD"


def test_nothing_but_the_contract_column_is_ever_assigned():
    """No `setattr(q, "content", ...)` and no `q.status = ...` anywhere."""
    protected = set(pre_image.CAPTURED_FIELDS) - {cmd.REPAIRABLE_FIELD}
    for node in ast.walk(_tree()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2):
            target = node.args[1]
            if isinstance(target, ast.Constant):
                assert target.value not in protected, target.value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in protected, target.attr


def test_the_write_takes_a_row_lock():
    """
    Structural, because a lost lock is invisible to a single-threaded test: the
    write still succeeds, and only a concurrent remediation would show the
    read-modify-write race. A mutation sweep dropped `select_for_update()` and
    every behavioural test still passed — this is what closes it.
    """
    apply_fn = next(node for node in ast.walk(_tree())
                    if isinstance(node, ast.FunctionDef) and node.name == "_apply")
    locking = [node for node in ast.walk(apply_fn)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "select_for_update"]
    assert locking, "the applied write does not lock the row it reads"

    saves = [node for node in ast.walk(apply_fn)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves, "nothing is saved inside _apply"


def test_the_write_happens_inside_one_transaction():
    apply_fn = next(node for node in ast.walk(_tree())
                    if isinstance(node, ast.FunctionDef) and node.name == "_apply")
    atomics = [node for node in ast.walk(apply_fn)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "atomic"]
    assert atomics, "the write is not wrapped in transaction.atomic"
    # Alias-scoped: a bare atomic() opens the transaction on `default` while the
    # writes go to the remediation connection.
    assert any("using" in {k.arg for k in call.keywords} for call in atomics)


def test_it_never_updates_or_deletes_through_a_queryset():
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("update", "delete"), node.func.attr


def test_it_uses_its_own_role_list_and_probe():
    source = inspect.getsource(cmd)
    assert "ALLOWED_CONTRACT_ROLES" in source
    assert "CONTRACT_REPAIR_PROBE" in source
    assert "CONTRACT_REPAIR_FORBIDDEN" in source
    for other in ("ALLOWED_REMEDIATION_ROLES", "ALLOWED_HIDDEN_TEST_ROLES",
                  "STATEMENT_REPAIR_PROBE", "HIDDEN_TEST_REPAIR_PROBE"):
        assert other not in source, other


def test_the_four_role_lists_are_disjoint():
    lists = (ops.ALLOWED_WRITE_ROLES, ops.ALLOWED_REMEDIATION_ROLES,
             ops.ALLOWED_HIDDEN_TEST_ROLES, ops.ALLOWED_CONTRACT_ROLES)
    combined = set().union(*lists)
    assert len(combined) == sum(len(item) for item in lists)


def test_each_probe_forbids_the_other_two_columns():
    """
    Three action classes, three columns, and each one's forbidden list names
    the other two. That is what keeps the remediation ORDER a privilege rather
    than a convention.
    """
    assert ops.CONTRACT_REPAIR_PROBE == (
        ("groups_question", "execution_contract_version", "UPDATE"),)

    contract_forbidden = {c for _t, c, _p in ops.CONTRACT_REPAIR_FORBIDDEN if c}
    statement_forbidden = {c for _t, c, _p in ops.STATEMENT_REPAIR_FORBIDDEN if c}
    hidden_forbidden = {c for _t, c, _p in ops.HIDDEN_TEST_REPAIR_FORBIDDEN if c}

    assert {"content", "hidden_test_cases"} <= contract_forbidden
    assert "execution_contract_version" in statement_forbidden
    assert "execution_contract_version" in hidden_forbidden
    # And none of the three may touch the trust boundary.
    for forbidden in (contract_forbidden, statement_forbidden, hidden_forbidden):
        assert {"status", "trust_state"} <= forbidden


def test_the_forbidden_list_covers_every_other_captured_field():
    """
    A field added to CAPTURED_FIELDS without being added here would be
    writable by this role and nobody would notice.
    """
    forbidden = {c for _t, c, _p in ops.CONTRACT_REPAIR_FORBIDDEN if c}
    for name in pre_image.CAPTURED_FIELDS:
        if name == cmd.REPAIRABLE_FIELD:
            continue
        assert name in forbidden, name
