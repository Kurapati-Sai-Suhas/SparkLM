"""
Boilerplate repair — the command that can change the code a learner is handed.

It is also the column the execution adapter reads a signature from, so the
tests weigh two things: that nothing but an annotation can move, and that no
other field or language comes along with it.

Local/synthetic database only.
"""

import ast
import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import execution_adapter, pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_boilerplate as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

CURRENT = ("class Solution:\n"
           "    def destCity(self, paths):\n"
           "        # Write your code here\n"
           "        pass")
APPROVED = ("class Solution:\n"
            "    def destCity(self, paths: list[list[str]]):\n"
            "        # Write your code here\n"
            "        pass")
JAVA = "class Solution { String destCity(List<List<String>> paths) { } }"
CASES = [
    {"stdin": '[["London","New York"],["New York","Paris"]]',
     "expected_output": "Paris"},
    {"stdin": '[["B","D"],["A","B"],["C","D"]]', "expected_output": "D"},
]


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="bp-op", password="pw",
                                    email="b@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="BP Portal")
    made, _ = Topic.objects.get_or_create(
        name="BPTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, starter=CURRENT, content="Statement."):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": starter, "java": JAVA},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9900)


@pytest.fixture
def control(db, topic):
    return make_question(topic, 264, content="Control.")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="bp-batch", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def source_file(tmp_path, source, name="starter.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def repair(path, operator, question_id=9900, batch="bp-batch",
           language="python", extra=()):
    call_command("remediate_boilerplate", "--batch", batch,
                 "--question", str(question_id), "--language", language,
                 "--source-file", path, "--reason", "approved plan",
                 "--operator", operator.username, "--local", *extra)


APPLY = ("--apply", "--confirm")


# ═════════════════════════════════════════════════════════════
# The repair
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_the_named_starter_changes(frozen_batch, question, operator,
                                        tmp_path):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == APPROVED
    assert question.boilerplate_code["java"] == JAVA
    for name in pre_image.CAPTURED_FIELDS:
        if name == "boilerplate_code":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_the_other_language_is_byte_identical(frozen_batch, question, operator,
                                              tmp_path):
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)
    question.refresh_from_db()
    assert set(question.boilerplate_code) == {"python", "java"}
    assert question.boilerplate_code["java"] == JAVA


@pytest.mark.django_db
def test_the_statement_keys_and_contract_cannot_change(frozen_batch, question,
                                                       operator, tmp_path):
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)
    question.refresh_from_db()
    assert question.content == "Statement."
    assert question.hidden_test_cases == CASES
    assert question.execution_contract_version == "v1"
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db
def test_the_action_is_recorded_as_boilerplate_repair(frozen_batch, question,
                                                      operator, tmp_path):
    before = pre_image.live_digest(question)
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    action = RemediationAction.objects.get()
    question.refresh_from_db()
    record = QuestionPreImage.objects.get(question=question)
    assert action.action_class == RemediationAction.CLASS_BOILERPLATE_REPAIR
    assert action.question_id == question.pk
    assert action.applied_by_id == operator.pk
    assert action.post_digest == pre_image.live_digest(question)
    assert action.post_digest != before
    assert action.pre_image_id == record.pk


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_starter(frozen_batch, question,
                                                        operator, tmp_path):
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)
    record = QuestionPreImage.objects.get(question=question)
    assert record.boilerplate_code["python"] == CURRENT
    pre_image.verify(record)


@pytest.mark.django_db
def test_rollback_restores_the_original_starter(frozen_batch, question,
                                                operator, tmp_path):
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)
    pre_image.rollback(frozen_batch, operator, questions=[question])
    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_the_annotation_makes_the_cases_bind(frozen_batch, question, operator,
                                             tmp_path):
    """The point of the repair, checked through the real adapter."""
    for case in CASES:
        before = execution_adapter.build_invocation(case["stdin"], CURRENT)
        assert before.warnings == ("undeclared_parameter_type",)

    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    starter = question.boilerplate_code["python"]
    for case in CASES:
        after = execution_adapter.build_invocation(case["stdin"], starter)
        assert after.ok and not after.warnings
        assert len(after.arguments) == 1
        assert isinstance(after.arguments[0], list)


# ═════════════════════════════════════════════════════════════
# Annotation-only — what it must refuse
# ═════════════════════════════════════════════════════════════

def refused(tmp_path, operator, source, message, extra=APPLY):
    with pytest.raises(CommandError, match=message):
        repair(source_file(tmp_path, source), operator, extra=extra)


@pytest.mark.django_db
def test_a_renamed_method_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator,
            APPROVED.replace("destCity", "destinationCity"),
            "more than parameter annotations")
    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_a_renamed_parameter_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    refused(tmp_path, operator,
            "class Solution:\n"
            "    def destCity(self, routes: list[list[str]]):\n"
            "        # Write your code here\n"
            "        pass",
            "more than parameter annotations")


@pytest.mark.django_db
def test_an_edited_body_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator,
            APPROVED.replace("        pass", "        return 'D'"),
            "more than parameter annotations")


@pytest.mark.django_db
def test_an_added_import_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator, "import typing\n" + APPROVED,
            "more than parameter annotations")


@pytest.mark.django_db
def test_an_added_parameter_is_refused(frozen_batch, question, operator,
                                       tmp_path):
    refused(tmp_path, operator,
            APPROVED.replace("paths: list[list[str]]",
                             "paths: list[list[str]], limit: int = 0"),
            "more than parameter annotations")


@pytest.mark.django_db
def test_an_added_return_annotation_is_refused(frozen_batch, question,
                                               operator, tmp_path):
    """Approved explicitly: the adapter never reads it, so it is not a repair."""
    refused(tmp_path, operator,
            APPROVED.replace("):", ") -> str:"),
            "return annotation")


@pytest.mark.django_db
def test_an_extra_method_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator,
            APPROVED + "\n\n    def helper(self):\n        pass",
            "more than parameter annotations")


@pytest.mark.django_db
def test_a_whitespace_only_change_is_refused(frozen_batch, question, operator,
                                             tmp_path):
    """Not annotation-only in substance: nothing about the contract moved."""
    refused(tmp_path, operator, CURRENT + "\n", "no annotation changed")


@pytest.mark.django_db
def test_a_no_op_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator, CURRENT, "byte-identical")


@pytest.mark.django_db
def test_invalid_python_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator, "class Solution\n    def destCity(", "not valid Python")


@pytest.mark.django_db
def test_an_empty_file_is_refused(frozen_batch, question, operator, tmp_path):
    refused(tmp_path, operator, "   \n", "empty or only whitespace")


@pytest.mark.django_db
def test_a_missing_file_is_refused(frozen_batch, operator, tmp_path):
    with pytest.raises(CommandError, match="no such source file"):
        repair(str(tmp_path / "absent.py"), operator, extra=APPLY)


@pytest.mark.django_db
def test_an_unknown_language_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    with pytest.raises(CommandError, match="no .* starter"):
        repair(source_file(tmp_path, APPROVED), operator, language="rust",
               extra=APPLY)
    question.refresh_from_db()
    assert set(question.boilerplate_code) == {"python", "java"}


@pytest.mark.django_db
def test_a_stale_expected_digest_is_refused(frozen_batch, question, operator,
                                            tmp_path):
    with pytest.raises(CommandError, match="moved since"):
        repair(source_file(tmp_path, APPROVED), operator,
               extra=(*APPLY, "--expect-digest", "0" * 64))
    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_the_matching_expected_digest_is_accepted(frozen_batch, question,
                                                  operator, tmp_path):
    digest = pre_image.live_digest(question)
    repair(source_file(tmp_path, APPROVED), operator,
           extra=(*APPLY, "--expect-digest", digest))
    question.refresh_from_db()
    assert question.boilerplate_code["python"] == APPROVED


@pytest.mark.django_db
def test_an_unexpected_field_change_reverts_the_repair(
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
        repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_a_dropped_language_reverts_the_repair(frozen_batch, question,
                                               operator, tmp_path, monkeypatch):
    """
    The language-set backstop, forced to fire.

    It cannot fire today — the proposal is built by copying the stored dict and
    substituting one key, so the set is preserved by construction. A mutation
    sweep showed it could be deleted with every other test still green, and
    losing a language would silently take a starter away from every learner
    using it.
    """
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 1:
            starters = dict(state["boilerplate_code"])
            starters.pop("java", None)
            state["boilerplate_code"] = starters
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="set of languages changed"):
        repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    assert set(question.boilerplate_code) == {"python", "java"}
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_a_sibling_language_change_that_lands_reverts_the_repair(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """The post-write per-language check, forced to fire."""
    real = pre_image.question_state
    calls = {"n": 0}

    def drifting(question_obj):
        calls["n"] += 1
        state = dict(real(question_obj))
        if calls["n"] > 1:
            state["boilerplate_code"] = dict(state["boilerplate_code"],
                                             java="tampered")
        return state

    monkeypatch.setattr(pre_image, "question_state", drifting)

    with pytest.raises(CommandError, match="'java' starter changed"):
        repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT
    assert question.boilerplate_code["java"] == JAVA


# ═════════════════════════════════════════════════════════════
# Write-ahead and batch rules
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_question_outside_the_batch_is_refused(frozen_batch, topic, operator,
                                                 tmp_path):
    stranger = make_question(topic, 9999)
    with pytest.raises(pre_image.CaptureIncomplete):
        repair(source_file(tmp_path, APPROVED), operator, question_id=9999,
               extra=APPLY)
    stranger.refresh_from_db()
    assert stranger.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, question, control,
                                      tmp_path):
    batch = RemediationBatch.objects.create(
        batch_key="bp-open", purpose="test", created_by=operator)
    pre_image.capture(batch, question, operator)

    with pytest.raises(pre_image.CaptureIncomplete):
        repair(source_file(tmp_path, APPROVED), operator, batch="bp-open",
               extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_a_corrupt_pre_image_is_refused(frozen_batch, question, operator,
                                        tmp_path):
    record = QuestionPreImage.objects.get(question=question)
    QuestionPreImage.objects.filter(pk=record.pk).update(content="tampered")

    with pytest.raises(pre_image.DigestMismatch):
        repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT


@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator, tmp_path):
    repair(source_file(tmp_path, APPROVED), operator)
    question.refresh_from_db()
    assert question.boilerplate_code["python"] == CURRENT
    assert not RemediationAction.objects.exists()


@pytest.mark.django_db
def test_a_dry_run_also_refuses_a_body_change(frozen_batch, question, operator,
                                              tmp_path):
    """The operator approves from the dry-run, so it must refuse there too."""
    refused(tmp_path, operator,
            APPROVED.replace("        pass", "        return 'D'"),
            "more than parameter annotations", extra=())


@pytest.mark.django_db
def test_a_sibling_repair_does_not_move_the_control(frozen_batch, control,
                                                    operator, tmp_path):
    before = pre_image.live_digest(control)
    repair(source_file(tmp_path, APPROVED), operator, extra=APPLY)
    control.refresh_from_db()
    assert pre_image.live_digest(control) == before


# ═════════════════════════════════════════════════════════════
# Structural separation
# ═════════════════════════════════════════════════════════════

def test_the_repairable_field_is_exactly_boilerplate_code():
    assert cmd.REPAIRABLE_FIELD == "boilerplate_code"


def test_every_save_names_only_that_field():
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
    tree = ast.parse(inspect.getsource(cmd))
    forbidden = {"content", "status", "trust_state", "hidden_test_cases",
                 "hidden_wrapper_code", "execution_contract_version"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "setattr":
            target = node.args[1] if len(node.args) > 1 else None
            if isinstance(target, ast.Constant):
                assert target.value not in forbidden
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
            assert node.func.attr not in ("update", "delete", "bulk_update")


def test_it_records_its_own_action_class():
    tree = ast.parse(inspect.getsource(cmd))
    classes = {node.attr for node in ast.walk(tree)
               if isinstance(node, ast.Attribute)
               and node.attr.startswith("CLASS_")}
    assert classes == {"CLASS_BOILERPLATE_REPAIR"}


def test_it_uses_its_own_role_list_and_probe():
    source = inspect.getsource(cmd)
    assert "ALLOWED_BOILERPLATE_ROLES" in source
    assert "BOILERPLATE_REPAIR_PROBE" in source
    assert "BOILERPLATE_REPAIR_FORBIDDEN" in source
    for other in ("ALLOWED_REMEDIATION_ROLES", "ALLOWED_HIDDEN_TEST_ROLES",
                  "ALLOWED_CONTRACT_ROLES", "STATEMENT_REPAIR_PROBE",
                  "HIDDEN_TEST_REPAIR_PROBE", "CONTRACT_REPAIR_PROBE"):
        assert other not in source, other


def test_the_five_role_lists_are_disjoint():
    lists = (ops.ALLOWED_WRITE_ROLES, ops.ALLOWED_REMEDIATION_ROLES,
             ops.ALLOWED_HIDDEN_TEST_ROLES, ops.ALLOWED_CONTRACT_ROLES,
             ops.ALLOWED_BOILERPLATE_ROLES)
    combined = set().union(*lists)
    assert len(combined) == sum(len(item) for item in lists)


def test_the_probe_and_its_forbidden_list_are_complements():
    """
    Each repair class may write exactly one column and must be refused the
    others — the property that keeps five roles from collapsing into one.
    """
    assert ops.BOILERPLATE_REPAIR_PROBE == (
        ("groups_question", "boilerplate_code", "UPDATE"),)
    forbidden = {column for _t, column, _p in ops.BOILERPLATE_REPAIR_FORBIDDEN
                 if column}
    assert forbidden == {"content", "hidden_test_cases", "status",
                         "trust_state", "execution_contract_version",
                         "hidden_wrapper_code"}
    assert "boilerplate_code" not in forbidden

    for other in (ops.STATEMENT_REPAIR_FORBIDDEN,
                  ops.HIDDEN_TEST_REPAIR_FORBIDDEN,
                  ops.CONTRACT_REPAIR_FORBIDDEN):
        assert "boilerplate_code" in {c for _t, c, _p in other if c}


def test_the_transaction_is_alias_scoped():
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
