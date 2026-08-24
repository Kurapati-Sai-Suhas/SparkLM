"""
Suite expansion — the command that makes a question ASK MORE (M2 P2.7h-3).

The other three classes over `hidden_test_cases` correct data. This one grows
it, and growing it invalidates evidence rather than correcting it: oracle
executions are scoped to case digests, so a suite with new cases has cases no
execution covers.

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
from groups.management.commands import expand_hidden_tests as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

STARTER = ("class Solution:\n"
           "    def strStr(self, haystack: str, needle: str) -> int:\n"
           "        pass\n")
ORIGINAL = [
    {"stdin": "hello\nll\n", "expected_output": "2",
     "explanation": "ll is at 2"},
    {"stdin": "abc\na\n", "expected_output": "0", "explanation": "a is at 0"},
]
ADDITIONS = [
    {"stdin": '["","a"]', "expected_output": "-1",
     "explanation": "empty haystack", "category": "empty_input"},
    {"stdin": "a\na\n", "expected_output": "0",
     "explanation": "both length 1", "category": "singleton"},
]
PLAN = {"question": 9700, "labels": {"1": "typical", "2": "match_at_start"},
        "additions": ADDITIONS}


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="ex-op", password="pw",
                                    email="e@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="EX Portal")
    made, _ = Topic.objects.get_or_create(
        name="EXTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, cases=None):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content="Statement.",
        topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": STARTER},
        hidden_test_cases=json.loads(json.dumps(cases or ORIGINAL)),
        hidden_wrapper_code={}, execution_contract_version="v3")


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9700)


@pytest.fixture
def control(db, topic):
    return make_question(topic, 264, [{"stdin": "1\n1\n",
                                       "expected_output": "1",
                                       "explanation": "control"}])


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="ex-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def plan_file(tmp_path, payload=None, name="plan.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload or PLAN), encoding="utf-8")
    return str(path)


def expand(path, operator, question_id=9700, batch="ex-batch", extra=()):
    call_command("expand_hidden_tests", "--batch", batch,
                 "--question", str(question_id), "--plan", path,
                 "--reason", "reach the floor", "--operator",
                 operator.username, "--local", *extra)


APPLY = ("--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# The expansion
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_exactly_the_named_cases_are_added(frozen_batch, question, operator,
                                           tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == len(ORIGINAL) + len(ADDITIONS)


@pytest.mark.django_db
def test_existing_cases_keep_input_answer_and_explanation(frozen_batch,
                                                          question, operator,
                                                          tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    for index, original in enumerate(ORIGINAL):
        landed = question.hidden_test_cases[index]
        for key in ("stdin", "expected_output", "explanation"):
            assert landed[key] == original[key], key


@pytest.mark.django_db
def test_labels_are_the_only_key_added_to_existing_cases(frozen_batch,
                                                         question, operator,
                                                         tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    for index, original in enumerate(ORIGINAL):
        landed = question.hidden_test_cases[index]
        assert set(landed) - set(original) == {"category"}
    assert question.hidden_test_cases[0]["category"] == "typical"


@pytest.mark.django_db
def test_additions_are_appended_in_order(frozen_batch, question, operator,
                                         tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    tail = question.hidden_test_cases[len(ORIGINAL):]
    assert [case["stdin"] for case in tail] == [a["stdin"] for a in ADDITIONS]


@pytest.mark.django_db
def test_only_hidden_test_cases_changes(frozen_batch, question, operator,
                                        tmp_path):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}
    expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    for name in pre_image.CAPTURED_FIELDS:
        if name == "hidden_test_cases":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_the_action_is_recorded_as_suite_expansion(frozen_batch, question,
                                                   operator, tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    action = RemediationAction.objects.get()
    assert action.action_class == RemediationAction.CLASS_SUITE_EXPANSION
    assert action.question_id == question.pk
    question.refresh_from_db()
    assert action.post_digest == pre_image.live_digest(question)


@pytest.mark.django_db
def test_rollback_restores_the_original_suite(frozen_batch, question,
                                              operator, tmp_path):
    expand(plan_file(tmp_path), operator, extra=APPLY)
    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


# ═════════════════════════════════════════════════════════════
# What it must refuse
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_changed_expected_output_is_refused(frozen_batch, question, operator,
                                              tmp_path, monkeypatch):
    """The dangerous version: an 'expansion' that quietly moves an answer."""
    real = cmd.Command._derive

    def sneaky(self, current, labels, additions):
        proposed = real(self, current, labels, additions)
        proposed[0]["expected_output"] = "999"
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", sneaky)
    with pytest.raises(CommandError, match="changes its 'expected_output'"):
        expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_a_changed_stdin_is_refused(frozen_batch, question, operator, tmp_path,
                                    monkeypatch):
    real = cmd.Command._derive

    def sneaky(self, current, labels, additions):
        proposed = real(self, current, labels, additions)
        proposed[1]["stdin"] = "tampered"
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", sneaky)
    with pytest.raises(CommandError, match="changes its 'stdin'"):
        expand(plan_file(tmp_path), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_removed_case_is_refused(frozen_batch, question, operator, tmp_path,
                                   monkeypatch):
    real = cmd.Command._derive
    monkeypatch.setattr(
        cmd.Command, "_derive",
        lambda self, current, labels, additions:
            real(self, current, labels, additions)[1:])
    with pytest.raises(CommandError, match="case\\(s\\) and the proposal has"):
        expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == len(ORIGINAL)


@pytest.mark.django_db
def test_a_reordered_suite_is_refused(frozen_batch, question, operator,
                                      tmp_path, monkeypatch):
    real = cmd.Command._derive

    def shuffled(self, current, labels, additions):
        proposed = real(self, current, labels, additions)
        proposed[0], proposed[1] = proposed[1], proposed[0]
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", shuffled)
    with pytest.raises(CommandError, match="changes its"):
        expand(plan_file(tmp_path), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_duplicate_input_is_refused(frozen_batch, question, operator,
                                      tmp_path):
    payload = json.loads(json.dumps(PLAN))
    payload["additions"] = [dict(ADDITIONS[0]),
                            {"stdin": "hello\nll\n", "expected_output": "2",
                             "explanation": "same as case 1",
                             "category": "duplicate_values"}]
    with pytest.raises(CommandError, match="duplicate input"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == len(ORIGINAL)


@pytest.mark.django_db
def test_an_addition_without_a_category_is_refused(frozen_batch, operator,
                                                   tmp_path):
    payload = json.loads(json.dumps(PLAN))
    del payload["additions"][0]["category"]
    with pytest.raises(CommandError, match="has no category"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_an_empty_category_is_refused(frozen_batch, operator, tmp_path):
    payload = json.loads(json.dumps(PLAN))
    payload["additions"][0]["category"] = "   "
    with pytest.raises(CommandError, match="has no category"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_relabelling_an_already_labelled_case_is_refused(frozen_batch, topic,
                                                         operator, tmp_path):
    labelled = make_question(topic, 9701, [
        dict(ORIGINAL[0], category="typical"), ORIGINAL[1]])
    batch = RemediationBatch.objects.create(
        batch_key="ex-labelled", purpose="test", created_by=operator)
    pre_image.capture(batch, labelled, operator)
    pre_image.freeze(batch, operator)

    payload = json.loads(json.dumps(PLAN))
    payload["question"] = 9701
    payload["labels"] = {"1": "something_else"}
    with pytest.raises(CommandError, match="already labelled"):
        expand(plan_file(tmp_path, payload), operator, question_id=9701,
               batch="ex-labelled", extra=APPLY)


@pytest.mark.django_db
def test_a_plan_with_no_additions_is_refused(frozen_batch, operator, tmp_path):
    payload = json.loads(json.dumps(PLAN))
    payload["additions"] = []
    with pytest.raises(CommandError, match="adds no cases"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_plan_for_another_question_is_refused(frozen_batch, question,
                                                operator, tmp_path):
    payload = json.loads(json.dumps(PLAN))
    payload["question"] = 1
    with pytest.raises(CommandError, match="another"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_non_text_expected_output_is_refused(frozen_batch, operator,
                                               tmp_path):
    payload = json.loads(json.dumps(PLAN))
    payload["additions"][0]["expected_output"] = ["-1"]
    with pytest.raises(CommandError, match="must be\\s+text"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)


@pytest.mark.django_db
def test_an_unbindable_addition_is_refused(frozen_batch, question, operator,
                                           tmp_path):
    """A case the adapter cannot deliver is not coverage."""
    payload = json.loads(json.dumps(PLAN))
    payload["additions"] = [{"stdin": "only-one-line", "expected_output": "0",
                             "explanation": "cannot supply two parameters",
                             "category": "empty_input"}]
    with pytest.raises(CommandError, match="does not bind"):
        expand(plan_file(tmp_path, payload), operator, extra=APPLY)
    question.refresh_from_db()
    assert len(question.hidden_test_cases) == len(ORIGINAL)


@pytest.mark.django_db
def test_a_stale_expected_digest_is_refused(frozen_batch, question, operator,
                                            tmp_path):
    with pytest.raises(CommandError, match="moved since"):
        expand(plan_file(tmp_path), operator,
               extra=(*APPLY, "--expect-digest", "0" * 64))
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_expansion(
        frozen_batch, question, operator, tmp_path, monkeypatch):
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
        expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_a_stray_key_on_an_existing_case_is_refused(frozen_batch, question,
                                                    operator, tmp_path,
                                                    monkeypatch):
    """
    Only `category` may be added to an existing case. Unreachable through the
    plan file — the derivation adds nothing else — so a mutation sweep showed
    the check could be deleted unnoticed.
    """
    real = cmd.Command._derive

    def sneaky(self, current, labels, additions):
        proposed = real(self, current, labels, additions)
        proposed[0]["weight"] = 5
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", sneaky)
    with pytest.raises(CommandError, match="gains key"):
        expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_an_unlabelled_addition_is_refused_by_the_invariant_too(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The plan reader already refuses this. The invariant is the backstop for a
    case that reaches the suite another way, and it needs its own test or it is
    an unverified claim.
    """
    real = cmd.Command._derive

    def sneaky(self, current, labels, additions):
        proposed = real(self, current, labels, additions)
        # REPLACE the last addition rather than appending: adding one would
        # trip the count check first and leave the label check untested.
        proposed[-1] = {"stdin": "zz\nz\n", "expected_output": "-1",
                        "explanation": "no label"}
        return proposed

    monkeypatch.setattr(cmd.Command, "_derive", sneaky)
    with pytest.raises(CommandError, match="has no category"):
        expand(plan_file(tmp_path), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_a_case_that_binds_only_by_guessing_is_refused(db, topic, operator,
                                                       tmp_path):
    """
    An UNANNOTATED starter makes the adapter guess the parameter type. A case
    added under those conditions would be graded on a guess, which is the
    defect the v3 contract work exists to remove.
    """
    guessy = Question.objects.create(
        id=9702, title="Q9702", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n"
                                    "    def solve(self, values):\n"
                                    "        pass\n"},
        hidden_test_cases=[{"stdin": "[1,2]", "expected_output": "3",
                            "explanation": "sum"}],
        hidden_wrapper_code={}, execution_contract_version="v3")
    batch = RemediationBatch.objects.create(
        batch_key="ex-guess", purpose="test", created_by=operator)
    pre_image.capture(batch, guessy, operator)
    pre_image.freeze(batch, operator)

    payload = {"question": 9702, "labels": {},
               "additions": [{"stdin": "[3,4]", "expected_output": "7",
                              "explanation": "sum", "category": "typical"}]}
    with pytest.raises(CommandError, match="binds only by guessing"):
        expand(plan_file(tmp_path, payload), operator, question_id=9702,
               batch="ex-guess", extra=APPLY)


@pytest.mark.django_db
def test_a_suite_that_lands_wrong_reverts_the_expansion(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The post-write re-check, forced to fire: `update_fields` plus the pre-write
    invariants make it unreachable in practice, and a mutation sweep showed it
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
    with pytest.raises(CommandError, match="changes its 'expected_output'"):
        expand(plan_file(tmp_path), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL, "the write was not reverted"
    assert not RemediationAction.objects.exists()


# ═════════════════════════════════════════════════════════════
# Write-ahead, batch, blast radius
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_pre_image_is_required(frozen_batch, topic, operator, tmp_path):
    stranger = make_question(topic, 9799)
    payload = json.loads(json.dumps(PLAN))
    payload["question"] = 9799
    with pytest.raises(pre_image.CaptureIncomplete):
        expand(plan_file(tmp_path, payload), operator, question_id=9799,
               extra=APPLY)
    stranger.refresh_from_db()
    assert stranger.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, question, tmp_path):
    batch = RemediationBatch.objects.create(
        batch_key="ex-open", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    with pytest.raises(pre_image.CaptureIncomplete):
        expand(plan_file(tmp_path), operator, batch="ex-open", extra=APPLY)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL


@pytest.mark.django_db
def test_a_corrupt_pre_image_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")
    with pytest.raises(pre_image.DigestMismatch):
        expand(plan_file(tmp_path), operator, extra=APPLY)


@pytest.mark.django_db
def test_the_control_is_not_touched(frozen_batch, control, operator, tmp_path):
    before = pre_image.live_digest(control)
    expand(plan_file(tmp_path), operator, extra=APPLY)
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator, tmp_path):
    expand(plan_file(tmp_path), operator)
    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL
    assert not RemediationAction.objects.exists()


# ═════════════════════════════════════════════════════════════
# Structural separation
# ═════════════════════════════════════════════════════════════

def test_the_repairable_field_and_label_key_are_named():
    assert cmd.REPAIRABLE_FIELD == "hidden_test_cases"
    assert cmd.LABEL_KEY == "category"
    assert cmd.PRESERVED_KEYS == ("stdin", "expected_output", "explanation")


def test_every_save_names_only_that_field():
    tree = ast.parse(inspect.getsource(cmd))
    saves = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "save"]
    assert saves
    for call in saves:
        keywords = {k.arg: k for k in call.keywords}
        assert "update_fields" in keywords
        value = keywords["update_fields"].value
        assert isinstance(value, ast.List) and len(value.elts) == 1
        assert value.elts[0].id == "REPAIRABLE_FIELD"


def test_it_records_its_own_action_class():
    tree = ast.parse(inspect.getsource(cmd))
    classes = {node.attr for node in ast.walk(tree)
               if isinstance(node, ast.Attribute)
               and node.attr.startswith("CLASS_")}
    assert classes == {"CLASS_SUITE_EXPANSION"}


def test_the_action_class_is_distinct_from_the_other_suite_writers():
    from groups.models import RemediationAction as action
    assert action.CLASS_SUITE_EXPANSION not in (
        action.CLASS_HIDDEN_TEST_REPAIR, action.CLASS_INPUT_REPAIR,
        action.CLASS_EXPECTED_OUTPUT_REPAIR)


def test_it_uses_the_hidden_test_role_and_probe():
    source = inspect.getsource(cmd)
    assert "ALLOWED_HIDDEN_TEST_ROLES" in source
    assert "HIDDEN_TEST_REPAIR_PROBE" in source
    assert "HIDDEN_TEST_REPAIR_FORBIDDEN" in source
    for other in ("ALLOWED_REMEDIATION_ROLES", "ALLOWED_CONTRACT_ROLES",
                  "ALLOWED_BOILERPLATE_ROLES", "ALLOWED_ORACLE_ROLES"):
        assert other not in source, other


def test_the_transaction_is_alias_scoped():
    tree = ast.parse(inspect.getsource(cmd))
    atomics = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "atomic"]
    assert atomics
    for call in atomics:
        assert "using" in {k.arg for k in call.keywords}


def test_the_row_is_locked_before_it_is_written():
    tree = ast.parse(inspect.getsource(cmd))
    assert any(isinstance(node, ast.Attribute)
               and node.attr == "select_for_update"
               for node in ast.walk(tree))


def test_no_other_captured_field_is_assigned():
    tree = ast.parse(inspect.getsource(cmd))
    forbidden = {"content", "status", "trust_state", "boilerplate_code",
                 "hidden_wrapper_code", "execution_contract_version"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "setattr":
            target = node.args[1]
            assert isinstance(target, ast.Name) \
                and target.id == "REPAIRABLE_FIELD"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in forbidden, target.attr
