"""
Input repair — the command that can change what a question ASKS.

`remediate_hidden_tests` can change an answer's stored form; this one changes
the input it is an answer to. They write the same column, so no grant separates
them and the weight falls entirely on the invariant below and on these tests.

Local/synthetic database only.
"""

import ast
import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_hidden_tests as answers
from groups.management.commands import remediate_inputs as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

ORIGINAL_CASES = [
    {"stdin": "hello\nll\n", "expected_output": "2"},
    {"stdin": "\n\n", "expected_output": "0"},
    {"stdin": "abc\na\n", "expected_output": "0"},
]
TARGET = {"question": 9800, "changes": [
    {"case": 2, "before": "\n\n", "after": '["",""]'}]}
REPAIRED_CASES = [
    {"stdin": "hello\nll\n", "expected_output": "2"},
    {"stdin": '["",""]', "expected_output": "0"},
    {"stdin": "abc\na\n", "expected_output": "0"},
]


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="in-op", password="pw",
                                    email="i@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Input Portal")
    made, _ = Topic.objects.get_or_create(
        name="InputTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, cases, content="Statement."):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def strStr(self, "
                                    "haystack: str, needle: str) -> int: pass\n"},
        hidden_test_cases=json.loads(json.dumps(cases)),
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9800, ORIGINAL_CASES)


@pytest.fixture
def control(db, topic):
    """Stands in for q264 — must never move."""
    return make_question(topic, 264, [{"stdin": "1", "expected_output": "1"}],
                         content="Control.")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="in-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def changes_file(tmp_path, payload, name="changes.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def repair(path, operator, question_id=9800, batch="in-batch", extra=()):
    call_command("remediate_inputs", "--batch", batch,
                 "--question", str(question_id), "--changes-file", path,
                 "--reason", "approved plan", "--operator", operator.username,
                 "--local", *extra)


APPLY = ("--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# The repair
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_named_stdin_changes(frozen_batch, question, operator, tmp_path):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == REPAIRED_CASES
    for name in pre_image.CAPTURED_FIELDS:
        if name == "hidden_test_cases":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_every_expected_output_is_held_fixed(frozen_batch, question, operator,
                                             tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert [case["expected_output"] for case in question.hidden_test_cases] \
        == ["2", "0", "0"]


@pytest.mark.django_db
def test_untouched_cases_are_byte_identical(frozen_batch, question, operator,
                                            tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases[0] == ORIGINAL_CASES[0]
    assert question.hidden_test_cases[2] == ORIGINAL_CASES[2]


@pytest.mark.django_db
def test_case_count_and_order_are_preserved(frozen_batch, question, operator,
                                            tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 3
    assert [case["expected_output"] for case in question.hidden_test_cases] \
        == [case["expected_output"] for case in ORIGINAL_CASES]


@pytest.mark.django_db
def test_the_statement_and_trust_state_cannot_change(frozen_batch, question,
                                                     operator, tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.content == "Statement."
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_the_action_is_recorded_as_input_repair(frozen_batch, question,
                                                operator, tmp_path):
    before = pre_image.live_digest(question)
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    action = RemediationAction.objects.get()
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)
    assert action.action_class == RemediationAction.CLASS_INPUT_REPAIR
    assert action.question_id == question.pk
    assert action.applied_by_id == operator.pk
    assert action.post_digest == pre_image.live_digest(question)
    assert action.post_digest != before
    assert action.pre_image_id == record.pk


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_inputs(frozen_batch, question,
                                                       operator, tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    record = QuestionPreImage.objects.get(question=question)
    assert record.hidden_test_cases == ORIGINAL_CASES
    pre_image.verify(record)


@pytest.mark.django_db
def test_rollback_restores_the_original_inputs(frozen_batch, question,
                                               operator, tmp_path):
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


# ═════════════════════════════════════════════════════════════
# The invariant — what an input repair must refuse
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_changing_an_expected_output_is_refused(frozen_batch, question,
                                                operator, tmp_path,
                                                monkeypatch):
    """
    The dangerous version: an 'input repair' that quietly moves an answer.
    Forced by making the derivation itself misbehave, since the change file
    cannot express an answer edit.
    """
    real = cmd.Command._derive

    def sneaky(self, current, changes):
        proposed = real(self, current, changes)
        proposed[0]["expected_output"] = "999"
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", sneaky)

    with pytest.raises(CommandError, match="repairs INPUTS"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_dry_run_also_refuses_an_answer_change(frozen_batch, question,
                                                 operator, tmp_path,
                                                 monkeypatch):
    """The operator approves from the dry-run; it must refuse there too."""
    real = cmd.Command._derive
    monkeypatch.setattr(
        cmd.Command, "_derive",
        lambda self, current, changes: [
            dict(case, expected_output="999") if index == 0 else case
            for index, case in enumerate(real(self, current, changes))])

    with pytest.raises(CommandError, match="repairs INPUTS"):
        repair(changes_file(tmp_path, TARGET), operator)      # no --apply


@pytest.mark.django_db
def test_adding_a_case_is_refused(frozen_batch, question, operator, tmp_path,
                                  monkeypatch):
    real = cmd.Command._derive
    monkeypatch.setattr(
        cmd.Command, "_derive",
        lambda self, current, changes: real(self, current, changes)
        + [{"stdin": "x", "expected_output": "y"}])

    with pytest.raises(CommandError, match="different action class"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 3


@pytest.mark.django_db
def test_removing_a_case_is_refused(frozen_batch, question, operator, tmp_path,
                                    monkeypatch):
    real = cmd.Command._derive
    monkeypatch.setattr(
        cmd.Command, "_derive",
        lambda self, current, changes: real(self, current, changes)[:-1])

    with pytest.raises(CommandError, match="different action class"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 3


@pytest.mark.django_db
def test_reordering_cases_is_refused(frozen_batch, question, operator,
                                     tmp_path, monkeypatch):
    """
    Swapping two cases moves an answer onto a different input. Here the two
    swapped cases have DIFFERENT answers, so the expected-output invariant is
    what catches it — which is the point: the suite cannot be reordered,
    whichever rule sees it first.
    """
    real = cmd.Command._derive

    def shuffled(self, current, changes):
        proposed = real(self, current, changes)
        proposed[0], proposed[2] = proposed[2], proposed[0]
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", shuffled)

    with pytest.raises(CommandError, match="case 1 changes its expected_output"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_reordering_is_refused_even_when_the_answers_match(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The harder case: two cases sharing an answer, so the expected-output check
    cannot see the swap. The positional comparison must catch it, or a reorder
    would silently re-pair every input with someone else's answer.
    """
    uniform = [{"stdin": "a\nb\n", "expected_output": "0"},
               {"stdin": "\n\n", "expected_output": "0"},
               {"stdin": "c\nd\n", "expected_output": "0"}]
    Question.objects.filter(pk=question.pk).update(hidden_test_cases=uniform)

    real = cmd.Command._derive

    def shuffled(self, current, changes):
        proposed = real(self, current, changes)
        proposed[0], proposed[2] = proposed[2], proposed[0]
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", shuffled)

    with pytest.raises(CommandError, match="not named for repair but changed"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == uniform


@pytest.mark.django_db
def test_changing_an_unnamed_case_is_refused(frozen_batch, question, operator,
                                             tmp_path, monkeypatch):
    real = cmd.Command._derive

    def extra_edit(self, current, changes):
        proposed = real(self, current, changes)
        proposed[2]["stdin"] = "tampered"
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", extra_edit)

    with pytest.raises(CommandError, match="not named for repair but changed"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_stale_before_value_is_refused(frozen_batch, question, operator,
                                         tmp_path):
    """An approval written against a reading that no longer holds."""
    stale = {"question": 9800, "changes": [
        {"case": 2, "before": "something else", "after": '["",""]'}]}
    with pytest.raises(CommandError, match="has moved since the approval"):
        repair(changes_file(tmp_path, stale), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_no_op_change_is_refused(frozen_batch, operator, tmp_path):
    same = {"question": 9800, "changes": [
        {"case": 2, "before": "\n\n", "after": "\n\n"}]}
    with pytest.raises(CommandError, match="changes nothing"):
        repair(changes_file(tmp_path, same), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_file_for_another_question_is_refused(frozen_batch, question,
                                                operator, tmp_path):
    with pytest.raises(CommandError, match="different question"):
        repair(changes_file(tmp_path, dict(TARGET, question=1)), operator,
               extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_case_number_out_of_range_is_refused(frozen_batch, operator, tmp_path):
    payload = {"question": 9800, "changes": [
        {"case": 9, "before": "\n\n", "after": '["",""]'}]}
    with pytest.raises(CommandError, match="does not exist"):
        repair(changes_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_naming_a_case_twice_is_refused(frozen_batch, operator, tmp_path):
    payload = {"question": 9800, "changes": [
        {"case": 2, "before": "\n\n", "after": '["",""]'},
        {"case": 2, "before": '["",""]', "after": '["","x"]'}]}
    with pytest.raises(CommandError, match="named twice"):
        repair(changes_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_an_empty_change_list_is_refused(frozen_batch, operator, tmp_path):
    with pytest.raises(CommandError, match="no changes"):
        repair(changes_file(tmp_path, {"question": 9800, "changes": []}),
               operator, extra=APPLY)


@pytest.mark.django_db
def test_malformed_json_is_refused(frozen_batch, operator, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommandError, match="not valid JSON"):
        repair(str(path), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_non_text_stdin_is_refused(frozen_batch, operator, tmp_path):
    payload = {"question": 9800, "changes": [
        {"case": 2, "before": "\n\n", "after": ["", ""]}]}
    with pytest.raises(CommandError, match="must both be text"):
        repair(changes_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_moved_output_identity_is_refused(frozen_batch, question, operator,
                                            tmp_path, monkeypatch):
    """
    The backstop behind the byte-equality check on `expected_output`.

    It cannot fire today — identity is a pure function of the stored answer, so
    equal answers always have equal identities — which is exactly why it needs
    a test that forces it. A mutation sweep showed the check could be deleted
    unnoticed; if the byte check is ever relaxed, this is what still refuses to
    let an answer's meaning move under cover of an input repair.
    """
    real = pre_image.suite_case_identities

    def drifting(cases):
        identities = list(real(cases))
        # Only the PROPOSED suite drifts — the one carrying the repaired
        # stdin. Drifting every call would move the pre-image's own digest and
        # the run would fail earlier, on verification, testing nothing.
        proposed = any(isinstance(case, dict) and case.get("stdin") == '["",""]'
                       for case in cases)
        if proposed and identities:
            case, stdin, _expected = identities[0]
            identities[0] = (case, stdin, "f" * 64)
        return identities

    monkeypatch.setattr(pre_image, "suite_case_identities", drifting)

    with pytest.raises(CommandError, match="changes its output identity"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_suite_that_lands_wrong_reverts_the_repair(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The post-write re-check, forced to fire.

    `update_fields` plus the pre-write invariants make it unreachable in
    practice. It exists because "what was proposed" and "what landed" are only
    the same if the write did what it claimed, and a mutation sweep showed it
    could be removed with every other test still green.
    """
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 1:
            suite = json.loads(json.dumps(state["hidden_test_cases"]))
            suite[0]["expected_output"] = "999"
            state["hidden_test_cases"] = suite
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="repairs INPUTS"):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES, "the write was not reverted"
    assert not RemediationAction.objects.exists()


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_repair(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """The backstop behind `update_fields`, forced to fire."""
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
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES, "the write was not reverted"


# ═════════════════════════════════════════════════════════════
# Write-ahead and batch rules
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_question_outside_the_batch_is_refused(frozen_batch, topic, operator,
                                                 tmp_path):
    stranger = make_question(topic, 9899, ORIGINAL_CASES)
    payload = dict(TARGET, question=9899)
    with pytest.raises(pre_image.CaptureIncomplete):
        repair(changes_file(tmp_path, payload), operator, question_id=9899,
               extra=APPLY)
    stranger.refresh_from_db()
    assert stranger.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, question, control,
                                      tmp_path):
    batch = RemediationBatch.objects.create(
        batch_key="in-open", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)

    with pytest.raises(pre_image.CaptureIncomplete):
        repair(changes_file(tmp_path, TARGET), operator, batch="in-open",
               extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_corrupt_pre_image_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator, tmp_path):
    repair(changes_file(tmp_path, TARGET), operator)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES
    assert not RemediationAction.objects.exists()


@pytest.mark.django_db
def test_a_sibling_repair_does_not_move_the_control(frozen_batch, control,
                                                    operator, tmp_path):
    before = pre_image.live_digest(control)
    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


@pytest.mark.django_db
def test_a_prior_answer_repair_is_not_reverted(frozen_batch, question,
                                               operator, tmp_path):
    """
    The reason the proposal is derived from LIVE and not from the pre-image.

    A question whose answers were repaired earlier in the same batch must not
    have that repair silently undone by a later input repair.
    """
    Question.objects.filter(pk=question.pk).update(
        hidden_test_cases=[dict(ORIGINAL_CASES[0], expected_output="2"),
                           dict(ORIGINAL_CASES[1], expected_output="0"),
                           dict(ORIGINAL_CASES[2], expected_output="0000")])

    repair(changes_file(tmp_path, TARGET), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases[2]["expected_output"] == "0000"
    assert question.hidden_test_cases[1]["stdin"] == '["",""]'


# ═════════════════════════════════════════════════════════════
# Structural separation from the answer-repair command
# ═════════════════════════════════════════════════════════════

def test_the_repairable_field_and_key_are_exactly_named():
    assert cmd.REPAIRABLE_FIELD == "hidden_test_cases"
    assert cmd.REPAIRABLE_KEY == "stdin"
    assert cmd.PROTECTED_KEY == "expected_output"


def test_it_is_the_mirror_of_the_answer_command():
    """One may write what the other holds fixed, and vice versa."""
    assert answers.REPAIRABLE_FIELD == cmd.REPAIRABLE_FIELD
    assert cmd.REPAIRABLE_KEY != cmd.PROTECTED_KEY


def test_every_save_names_update_fields():
    tree = ast.parse(inspect.getsource(cmd))
    saves = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves
    for call in saves:
        keywords = {keyword.arg: keyword for keyword in call.keywords}
        assert "update_fields" in keywords
        value = keywords["update_fields"].value
        assert isinstance(value, ast.List) and len(value.elts) == 1
        assert isinstance(value.elts[0], ast.Name) \
            and value.elts[0].id == "REPAIRABLE_FIELD"


def test_no_other_captured_field_is_ever_assigned():
    """
    AST, not text search: a docstring mentioning `content` defeated an earlier
    text guard, and a text guard cannot see `setattr(q, name, value)` anyway.
    """
    tree = ast.parse(inspect.getsource(cmd))
    forbidden = {"content", "status", "trust_state", "boilerplate_code",
                 "hidden_wrapper_code", "execution_contract_version"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "setattr":
            target = node.args[1] if len(node.args) > 1 else None
            if isinstance(target, ast.Constant):
                assert target.value not in forbidden, ast.dump(node)
            else:
                assert isinstance(target, ast.Name) \
                    and target.id == "REPAIRABLE_FIELD"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in forbidden, target.attr


def test_it_never_writes_through_the_queryset():
    tree = ast.parse(inspect.getsource(cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("update", "delete", "bulk_update"), \
                ast.dump(node)


def test_it_records_its_own_action_class():
    tree = ast.parse(inspect.getsource(cmd))
    classes = {node.attr for node in ast.walk(tree)
               if isinstance(node, ast.Attribute)
               and node.attr.startswith("CLASS_")}
    assert classes == {"CLASS_INPUT_REPAIR"}


def test_it_uses_the_shared_hidden_test_role_deliberately():
    """
    Same column, so the same role — and the command says so rather than
    inventing a role list that does not exist.
    """
    source = inspect.getsource(cmd)
    assert "ALLOWED_HIDDEN_TEST_ROLES" in source
    assert "ALLOWED_REMEDIATION_ROLES" not in source
    assert "ALLOWED_CONTRACT_ROLES" not in source
    assert ops.ALLOWED_HIDDEN_TEST_ROLES == frozenset({"learnlm_hidden_test_rw"})


def test_it_probes_the_hidden_test_column():
    source = inspect.getsource(cmd)
    assert "HIDDEN_TEST_REPAIR_PROBE" in source
    assert "HIDDEN_TEST_REPAIR_FORBIDDEN" in source
    assert "STATEMENT_REPAIR_PROBE" not in source


def test_the_transaction_is_alias_scoped():
    """
    A bare `transaction.atomic()` opens on `default` while the write goes to
    the operator alias — the all-or-nothing guarantee would then cover the
    wrong connection.
    """
    tree = ast.parse(inspect.getsource(cmd))
    atomics = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "atomic"]
    assert atomics
    for call in atomics:
        assert "using" in {keyword.arg for keyword in call.keywords}


def test_the_row_is_locked_before_it_is_written():
    tree = ast.parse(inspect.getsource(cmd))
    assert any(isinstance(node, ast.Attribute)
               and node.attr == "select_for_update"
               for node in ast.walk(tree))
