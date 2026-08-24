"""
Migration safety and the pre-image operator boundary (M2 P2.7, blocker J8).

Two things are proven here:

1. migration 0044 stays additive — it may never acquire an operation that
   could touch existing grading data;
2. the operator commands refuse before they write, for every reason they are
   supposed to refuse.

A gate that is only checked by reading the code is a gate that survives exactly
until someone edits the code.

Local/synthetic database only.
"""

import ast
import pathlib

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

MIGRATION = (pathlib.Path(__file__).parent / "migrations"
             / "0044_pre_image_rollback.py")

#: Operations that could alter or destroy existing grading data. 0044 must
#: never contain one. Listed by name so a future edit that adds one fails here
#: rather than in production.
FORBIDDEN_OPERATIONS = {
    "RunSQL", "RunPython", "AlterField", "RemoveField", "DeleteModel",
    "AlterModelTable", "RenameField", "RenameModel",
}


# ═════════════════════════════════════════════════════════════
# Step 1 — migration safety
# ═════════════════════════════════════════════════════════════

def migration_operations():
    """The `migrations.X` names 0044 actually uses, by AST."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "migrations"):
            names.append(node.func.attr)
    return names


def test_0044_contains_no_forbidden_operation():
    """The whole safety claim for applying this to production."""
    used = set(migration_operations())
    assert not (used & FORBIDDEN_OPERATIONS), used & FORBIDDEN_OPERATIONS


def test_0044_is_exactly_the_intended_additive_operations():
    from collections import Counter
    assert Counter(migration_operations()) == Counter({
        "CreateModel": 3, "AddIndex": 2, "AddConstraint": 1,
    })


def test_0044_creates_only_the_three_pre_image_models():
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    created = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "CreateModel"):
            for keyword in node.keywords:
                if keyword.arg == "name":
                    created.append(keyword.value.value)
    assert sorted(created) == ["QuestionPreImage", "RemediationAction",
                               "RemediationBatch"]


def test_0044_depends_on_the_expected_predecessor():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "'0043_glicko_snapshot'" in source


# ═════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def operator(db):
    return User.objects.create_user(username="op", password="pw",
                                    email="op@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Op Portal")
    made, _ = Topic.objects.get_or_create(
        name="OpTopic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, **overrides):
    fields = {
        "id": question_id, "title": f"Q{question_id}", "content": "Body.",
        "topic": topic, "base_difficulty": 1200.0,
        "boilerplate_code": {"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        "hidden_test_cases": [{"stdin": "110", "expected_output": "3"}],
        "hidden_wrapper_code": {}, "execution_contract_version": "v1",
    }
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def questions(db, topic):
    return [make_question(topic, 8200 + n) for n in range(2)]


def capture(operator, questions, batch="op-batch", extra=()):
    call_command("preimage_capture", "--batch", batch,
                 "--questions", *[str(q.pk) for q in questions],
                 "--purpose", "operator boundary test",
                 "--operator", operator.username,
                 "--local", "--apply", "--confirm", *extra)


# ═════════════════════════════════════════════════════════════
# Step 8 — the operator boundary
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_non_staff_operator_is_rejected(db, questions):
    User.objects.create_user(username="civilian", password="pw",
                             email="c@example.com", is_staff=False)
    with pytest.raises(CommandError, match="not staff"):
        call_command("preimage_capture", "--batch", "b", "--questions",
                     str(questions[0].pk), "--purpose", "x",
                     "--operator", "civilian", "--local", "--apply", "--confirm")


@pytest.mark.django_db
def test_an_inactive_operator_is_rejected(db, questions):
    User.objects.create_user(username="retired", password="pw",
                             email="r@example.com", is_staff=True,
                             is_active=False)
    with pytest.raises(CommandError, match="not an active account"):
        call_command("preimage_capture", "--batch", "b", "--questions",
                     str(questions[0].pk), "--purpose", "x",
                     "--operator", "retired", "--local", "--apply", "--confirm")


@pytest.mark.django_db
def test_an_unknown_operator_is_rejected(db, questions):
    with pytest.raises(CommandError, match="no such user"):
        call_command("preimage_capture", "--batch", "b", "--questions",
                     str(questions[0].pk), "--purpose", "x",
                     "--operator", "ghost", "--local", "--apply", "--confirm")


@pytest.mark.django_db
def test_dry_run_is_the_default_and_writes_nothing(operator, questions):
    call_command("preimage_capture", "--batch", "dry", "--questions",
                 *[str(q.pk) for q in questions], "--purpose", "x",
                 "--operator", operator.username, "--local")

    assert not RemediationBatch.objects.exists()
    assert not QuestionPreImage.objects.exists()


@pytest.mark.django_db
def test_capture_writes_pre_images_and_no_question_changes(operator, questions):
    before = {q.pk: pre_image.live_digest(q) for q in questions}
    capture(operator, questions)

    assert QuestionPreImage.objects.count() == 2
    for question in questions:
        question.refresh_from_db()
        assert pre_image.live_digest(question) == before[question.pk]


def test_the_capture_command_makes_no_direct_row_writes():
    """
    Structural, by AST, and deliberately blunt: `preimage_capture` may not call
    `.save()`, `.update()` or `.delete()` on ANYTHING.

    An earlier version of this test only looked for `Question.objects.<verb>`,
    which a mutation sweep walked straight past — `for q in questions:
    q.save(...)` writes questions through a loop variable and matches no such
    pattern. Naming the permitted operations is checkable; enumerating the
    forbidden receivers is not.

    Everything legitimate here goes through `pre_image.capture` / `freeze` or
    `RemediationBatch.objects.create`.
    """
    import inspect
    from groups.management.commands import preimage_capture

    tree = ast.parse(inspect.getsource(preimage_capture))
    offenders = [node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr in ("save", "update", "delete")]
    assert offenders == [], (
        f"preimage_capture performs direct row writes: {offenders}. Capture "
        f"must write pre-image rows only, through pre_image.capture.")


@pytest.mark.django_db
def test_capture_leaves_every_question_field_untouched(operator, questions):
    """Behavioural companion: the whole captured state, field by field."""
    before = [(q.pk, {name: getattr(q, name)
                      for name in pre_image.CAPTURED_FIELDS})
              for q in questions]
    capture(operator, questions)
    for question_id, state in before:
        question = Question.objects.get(pk=question_id)
        for name, value in state.items():
            assert getattr(question, name) == value, f"q{question_id}.{name}"


@pytest.mark.django_db
def test_capture_refuses_an_unknown_question(operator, questions):
    with pytest.raises(CommandError, match="no such question"):
        call_command("preimage_capture", "--batch", "b", "--questions",
                     "999999", "--purpose", "x", "--operator",
                     operator.username, "--local", "--apply", "--confirm")


@pytest.mark.django_db
def test_creating_a_batch_without_a_purpose_is_refused(operator, questions):
    with pytest.raises(CommandError, match="purpose"):
        call_command("preimage_capture", "--batch", "nameless", "--questions",
                     str(questions[0].pk), "--operator", operator.username,
                     "--local", "--apply", "--confirm")


@pytest.mark.django_db
def test_recapture_does_not_duplicate_or_overwrite(operator, questions):
    capture(operator, questions)
    original = {p.question_id: p.state_digest
                for p in QuestionPreImage.objects.all()}

    questions[0].content = "changed after capture"
    questions[0].save(update_fields=["content"])
    capture(operator, questions)

    assert QuestionPreImage.objects.count() == 2
    for record in QuestionPreImage.objects.all():
        assert record.state_digest == original[record.question_id]


@pytest.mark.django_db
def test_a_frozen_batch_refuses_further_capture(operator, questions, topic):
    capture(operator, questions)
    call_command("preimage_capture", "--batch", "op-batch", "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")

    extra = make_question(topic, 8299)
    with pytest.raises(CommandError, match="frozen"):
        capture(operator, [extra])


@pytest.mark.django_db
def test_freeze_is_a_separate_invocation_from_capture(operator, questions):
    """Capture must not freeze implicitly — the operator gets a look first."""
    capture(operator, questions)
    batch = RemediationBatch.objects.get(batch_key="op-batch")
    assert batch.frozen_at is None
    assert batch.state == RemediationBatch.STATE_OPEN


@pytest.mark.django_db
def test_inspect_is_read_only(operator, questions):
    capture(operator, questions)
    before = QuestionPreImage.objects.count()
    call_command("preimage_inspect", "--batch", "op-batch",
                 "--operator", operator.username, "--local")
    assert QuestionPreImage.objects.count() == before


def command_options(module):
    """
    The option strings a command's parser actually accepts, by AST.

    Not a text search: `preimage_rollback`'s docstring explains that there is
    no `--force`, and a substring check fails on the explanation — the same
    prose-versus-behaviour trap that has bitten this codebase before. What
    matters is what `add_argument` registers.
    """
    import inspect
    tree = ast.parse(inspect.getsource(module))
    options = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(
                        argument.value, str):
                    options.add(argument.value)
    return options


def test_no_command_offers_a_generic_force_flag():
    """
    A general-purpose override is a gate that exists in the code and not in
    practice, because the one time it matters someone is in a hurry.
    """
    from groups.management.commands import (
        preimage_capture, preimage_inspect, preimage_rollback)

    for module in (preimage_capture, preimage_inspect, preimage_rollback):
        options = command_options(module)
        assert "--force" not in options, module.__name__
        assert "--yes" not in options, module.__name__


def test_the_only_override_is_narrow_and_lives_on_rollback_alone():
    """
    `--allow-divergence` is named for exactly what it permits and appears on
    one command. It cannot override a corrupt pre-image — proven separately by
    `test_corruption_is_not_overridable_by_allow_divergence`.
    """
    from groups.management.commands import (
        preimage_capture, preimage_inspect, preimage_rollback)

    assert "--allow-divergence" in command_options(preimage_rollback)
    assert "--allow-divergence" not in command_options(preimage_capture)
    assert "--allow-divergence" not in command_options(preimage_inspect)


def test_inspect_has_no_write_path_at_all():
    """The command you run mid-incident must be safe by construction."""
    from groups.management.commands import preimage_inspect
    options = command_options(preimage_inspect)
    assert "--apply" not in options
    assert "--confirm" not in options


# ── role and identity gates ────────────────────────────────────────────────

@pytest.mark.django_db
def test_the_census_role_is_refused(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_census_ro", "PG 17"))
    with pytest.raises(ops.GateFailure, match="learnlm_census_ro"):
        ops.gate_writing_role("default")


@pytest.mark.django_db
def test_the_pilot_role_is_refused(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_pilot_rw", "PG 17"))
    with pytest.raises(ops.GateFailure, match="learnlm_pilot_rw"):
        ops.gate_writing_role("default")


@pytest.mark.django_db
def test_the_dedicated_capture_role_passes_the_role_gate(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_preimage_rw", "PG 17"))
    assert ops.gate_writing_role("default") == "learnlm_preimage_rw"


@pytest.mark.django_db
def test_the_database_owner_is_refused(monkeypatch):
    """
    The reason the gate is an allow-list. `neondb_owner` can write every table
    including `groups_question`, so capturing with it would make the
    least-privilege capture role decorative.
    """
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "neondb_owner", "PG 17"))
    with pytest.raises(ops.GateFailure, match="neondb_owner"):
        ops.gate_writing_role("default")


@pytest.mark.django_db
def test_an_unreviewed_role_is_refused_by_default(monkeypatch):
    """
    The whole point of an allow-list: a role nobody considered is refused,
    not permitted. Under the previous deny-list this passed.
    """
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "some_new_role", "PG 17"))
    with pytest.raises(ops.GateFailure, match="not an authorized"):
        ops.gate_writing_role("default")


def test_the_allowed_write_role_set_is_exactly_the_capture_role():
    assert ops.ALLOWED_WRITE_ROLES == frozenset({"learnlm_preimage_rw"})


@pytest.mark.django_db
def test_a_wrong_production_target_is_refused(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("some_other_db", "admin", "PG 17"))
    with pytest.raises(ops.GateFailure, match="not the documented production"):
        ops.gate_production_target("default", require_production=True)


@pytest.mark.django_db
def test_local_mode_refuses_to_run_against_production(monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "admin", "PG 17"))
    with pytest.raises(ops.GateFailure, match="--local"):
        ops.gate_production_target("default", require_production=False)


@pytest.mark.django_db
def test_production_writes_require_confirmation():
    identity = {"database": "neondb", "is_production": True}
    with pytest.raises(ops.GateFailure, match="--confirm"):
        ops.require_confirmation(False, "capture pre-images", identity)
    assert ops.require_confirmation(True, "capture pre-images", identity)


@pytest.mark.django_db
def test_a_local_target_does_not_demand_confirmation():
    identity = {"database": "test_learnlm_db", "is_production": False}
    assert ops.require_confirmation(False, "capture", identity)


def test_the_documented_production_database_is_pinned():
    assert ops.PRODUCTION_DATABASE == "neondb"
    assert set(ops.REFUSED_ROLES) == {
        "learnlm_census_ro", "learnlm_pilot_rw", "neondb_owner"}


@pytest.mark.django_db
def test_the_role_allow_list_applies_to_production_only(operator, monkeypatch):
    """
    A local test database uses whatever role that throwaway instance has, so
    demanding the production capture role there would make the gates
    untestable — and an untestable gate is an unverified one. The capability
    check still runs everywhere.
    """
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("test_learnlm_db", "postgres", "PG 17"))
    monkeypatch.setattr(ops, "connections", _FakeConnections(1))

    resolved, identity = ops.run_gates(
        "default", operator.username, action="capture", confirmed=False,
        require_production=False, needs_write=True)
    assert identity["is_production"] is False
    assert resolved.username == operator.username


@pytest.mark.django_db
def test_the_role_allow_list_is_enforced_on_production(operator, monkeypatch):
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "postgres", "PG 17"))
    monkeypatch.setattr(ops, "connections", _FakeConnections(1))

    with pytest.raises(ops.GateFailure, match="not an authorized"):
        ops.run_gates("default", operator.username, action="capture",
                      confirmed=True, require_production=True, needs_write=True)


class _FakeCursor:
    """Reports whatever INSERT-privilege count the test wants."""

    def __init__(self, count):
        self._count = count

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return (self._count,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConnections:
    def __init__(self, count):
        self._count = count

    def __getitem__(self, alias):
        cursor = _FakeCursor(self._count)
        return type("Conn", (), {"cursor": lambda self=None: cursor})()


@pytest.mark.django_db
def test_a_role_without_insert_privilege_is_refused(monkeypatch):
    """
    Discovered before the batch starts. A capture that fails on row 4 of 7
    leaves a partial batch somebody then has to reason about.
    """
    monkeypatch.setattr(ops, "connections", _FakeConnections(0))
    with pytest.raises(ops.GateFailure, match="cannot INSERT"):
        ops.gate_write_privilege("default")


@pytest.mark.django_db
def test_a_role_with_insert_privilege_passes(monkeypatch):
    monkeypatch.setattr(ops, "connections", _FakeConnections(1))
    assert ops.gate_write_privilege("default") is True


@pytest.mark.django_db
def test_run_gates_enforces_the_write_side_gates_together(operator, monkeypatch):
    """
    The gates must be enforced through the ENTRY POINT, not merely exist as
    functions. Testing each one directly leaves `run_gates` free to stop
    calling them — which a mutation sweep proved, so this drives the real path.
    """
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_census_ro", "PG 17"))
    monkeypatch.setattr(ops, "connections", _FakeConnections(1))

    with pytest.raises(ops.GateFailure, match="learnlm_census_ro"):
        ops.run_gates("default", operator.username, action="capture",
                      confirmed=True, require_production=True, needs_write=True)


@pytest.mark.django_db
def test_run_gates_demands_confirmation_through_the_entry_point(operator,
                                                                monkeypatch):
    # The authorized capture role, so the confirmation gate is what fails
    # rather than the role gate ahead of it.
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_preimage_rw", "PG 17"))
    monkeypatch.setattr(ops, "connections", _FakeConnections(1))

    with pytest.raises(ops.GateFailure, match="--confirm"):
        ops.run_gates("default", operator.username, action="capture",
                      confirmed=False, require_production=True, needs_write=True)


@pytest.mark.django_db
def test_run_gates_skips_write_gates_only_for_read_only_actions(operator,
                                                                monkeypatch):
    """`preimage_inspect` must remain runnable as the census role."""
    monkeypatch.setattr(ops, "database_identity",
                        lambda alias: ("neondb", "learnlm_census_ro", "PG 17"))
    resolved, identity = ops.run_gates(
        "default", operator.username, action="inspect", confirmed=False,
        require_production=True, needs_write=False)
    assert resolved.username == operator.username
    assert identity["is_production"] is True


# ── rollback through the command ───────────────────────────────────────────

@pytest.mark.django_db
def test_rollback_dry_run_restores_nothing(operator, questions):
    capture(operator, questions)
    call_command("preimage_capture", "--batch", "op-batch", "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")
    questions[0].content = "remediated"
    questions[0].save(update_fields=["content"])

    call_command("preimage_rollback", "--batch", "op-batch",
                 "--operator", operator.username, "--local")

    questions[0].refresh_from_db()
    assert questions[0].content == "remediated"


@pytest.mark.django_db
def test_rollback_restores_the_exact_pre_image(operator, questions):
    before = pre_image.live_digest(questions[0])
    capture(operator, questions)
    call_command("preimage_capture", "--batch", "op-batch", "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")

    batch = RemediationBatch.objects.get(batch_key="op-batch")
    questions[0].content = "remediated"
    questions[0].save(update_fields=["content"])
    pre_image.record_action(batch, questions[0],
                            RemediationAction.CLASS_STATEMENT_REPAIR, operator)

    call_command("preimage_rollback", "--batch", "op-batch",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")

    questions[0].refresh_from_db()
    assert pre_image.live_digest(questions[0]) == before


@pytest.mark.django_db
def test_rollback_preserves_provenance_history(operator, questions):
    capture(operator, questions)
    call_command("preimage_capture", "--batch", "op-batch", "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")
    batch = RemediationBatch.objects.get(batch_key="op-batch")
    questions[0].content = "remediated"
    questions[0].save(update_fields=["content"])
    pre_image.record_action(batch, questions[0],
                            RemediationAction.CLASS_STATEMENT_REPAIR, operator)
    before = set(RemediationAction.objects.values_list("pk", flat=True))

    call_command("preimage_rollback", "--batch", "op-batch",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")

    assert before <= set(RemediationAction.objects.values_list("pk", flat=True))


@pytest.mark.django_db
def test_rollback_refuses_a_corrupt_pre_image(operator, questions):
    capture(operator, questions)
    call_command("preimage_capture", "--batch", "op-batch", "--freeze",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")
    record = QuestionPreImage.objects.filter(question=questions[0]).first()
    QuestionPreImage.objects.filter(pk=record.pk).update(content="corrupted")

    with pytest.raises(pre_image.DigestMismatch):
        call_command("preimage_rollback", "--batch", "op-batch",
                     "--operator", operator.username, "--local", "--apply",
                     "--confirm")


@pytest.mark.django_db
def test_the_rollback_scope_is_unchanged():
    """Step 6: question-side only, and it must stay that way."""
    assert pre_image.ROLLBACK_SCOPE == ("groups_question",)


def test_the_rollback_command_names_no_history_model():
    """
    Historical events remain historical: the rollback command must not so much
    as reference the models that record them.
    """
    import inspect
    from groups.management.commands import preimage_rollback

    source = inspect.getsource(preimage_rollback)
    for model in ("ReferenceSolution", "OracleExecution", "QuestionApproval",
                  "CodeSubmission", "RecommendationLog"):
        assert f"{model}.objects" not in source, model


# ── the control question ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_the_control_question_is_unchanged_by_capture(operator, topic):
    """q264 goes through the workflow and must come out byte-identical."""
    control = make_question(topic, 264)
    before = pre_image.live_digest(control)

    capture(operator, [control], batch="control-batch")

    control.refresh_from_db()
    assert pre_image.live_digest(control) == before
    assert QuestionPreImage.objects.filter(question=control).count() == 1
