"""
Migration 0045 — adding INPUT_REPAIR to the remediation vocabulary.

A migration that touches the table recording what was done to grading truth is
worth proving harmless BEFORE it runs against production, not after. These
tests assert what 0045 is (one AlterField), what it emits (no DDL), what it
adds (exactly one class) and what it leaves alone (every other schema in the
remediation path).

Migration DRIFT is already guarded elsewhere — `test_reference_lifecycle.py`
runs `makemigrations --check` — so it is not repeated here.
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import migrations, models
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from groups import pre_image
from groups.models import (
    PRE_IMAGE_SCHEMA_VERSION, CodingPortal, Question, QuestionPreImage,
    RemediationAction, RemediationBatch, Topic,
)

User = get_user_model()

BEFORE = ("groups", "0044_pre_image_rollback")
AFTER = ("groups", "0045_input_repair_action_class")

#: What existed before 0045, in order. Written out rather than imported so the
#: test fails if a class is renamed, reordered or dropped — importing the model
#: would make the assertion agree with whatever the model currently says.
EXISTING = [
    "CONTRACT_REPAIR", "STATEMENT_REPAIR", "BOILERPLATE_REPAIR",
    "HIDDEN_TEST_REPAIR", "EXPECTED_OUTPUT_REPAIR", "MANUAL_REVIEW",
    "COMPLETE_REBUILD", "ROLLBACK",
]


def load(target):
    """The project state at one migration, without touching a database."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return loader.project_state(target)


def action_class_field(target):
    state = load(target)
    return state.models["groups", "remediationaction"].fields["action_class"]


def operations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return loader.disk_migrations[AFTER].operations


# ═════════════════════════════════════════════════════════════
# What the migration IS
# ═════════════════════════════════════════════════════════════

def test_it_is_exactly_one_alter_field():
    ops = operations()
    assert len(ops) == 1, [type(op).__name__ for op in ops]
    assert isinstance(ops[0], migrations.AlterField)
    assert ops[0].model_name == "remediationaction"
    assert ops[0].name == "action_class"


def test_it_contains_no_data_migration_and_no_raw_sql():
    forbidden = (migrations.RunSQL, migrations.RunPython, migrations.CreateModel,
                 migrations.DeleteModel, migrations.AddField,
                 migrations.RemoveField, migrations.RenameField,
                 migrations.RenameModel, migrations.AlterModelTable,
                 migrations.AddIndex, migrations.RemoveIndex,
                 migrations.AddConstraint, migrations.RemoveConstraint)
    for operation in operations():
        assert not isinstance(operation, forbidden), type(operation).__name__


@pytest.mark.django_db
def test_it_emits_no_ddl():
    """
    State-only means the database is not asked to do anything. `sqlmigrate`
    renders what would run; for a CharField `choices` change that is nothing.
    """
    buffer = StringIO()
    call_command("sqlmigrate", "groups", "0045", stdout=buffer)
    sql = buffer.getvalue().upper()
    assert "(NO-OP)" in sql
    for statement in ("ALTER TABLE", "CREATE ", "DROP ", "UPDATE ", "INSERT "):
        assert statement not in sql, sql


def test_it_depends_on_0044():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.disk_migrations[AFTER].dependencies == [BEFORE]


# ═════════════════════════════════════════════════════════════
# What it adds, and what it must not
# ═════════════════════════════════════════════════════════════

def test_it_adds_only_input_repair():
    before = [value for value, _label in action_class_field(BEFORE).choices]
    after = [value for value, _label in action_class_field(AFTER).choices]

    assert before == EXISTING
    assert set(after) - set(before) == {"INPUT_REPAIR"}
    assert set(before) - set(after) == set()


def test_the_existing_classes_keep_their_meaning_and_order():
    """
    A reorder or a relabel would silently rewrite what past audit rows mean.
    Every pre-existing value must still be present, still map to itself, and
    still appear in the same relative order.
    """
    after = [value for value, _label in action_class_field(AFTER).choices]
    labels = dict(action_class_field(AFTER).choices)

    assert [value for value in after if value in EXISTING] == EXISTING
    for value in EXISTING:
        assert labels[value] == value


def test_nothing_but_choices_changed_on_the_field():
    before = action_class_field(BEFORE)
    after = action_class_field(AFTER)

    _n, _p, _a, before_kwargs = before.deconstruct()
    _n, _p, _a, after_kwargs = after.deconstruct()
    before_kwargs.pop("choices")
    after_kwargs.pop("choices")

    assert before_kwargs == after_kwargs
    assert after.max_length == 32
    assert isinstance(after, models.CharField)


def test_the_model_and_the_migrations_agree_exactly():
    """
    Ties the two together, in order, at the LATEST migration.

    Reordering the vocabulary on the model alone leaves every stored row valid,
    so no behavioural test notices — but the model and the migrations would
    then describe different states, and the next `makemigrations` would emit a
    mystery AlterField nobody asked for. Found by a mutation sweep.

    Resolved dynamically rather than pinned to 0045: later migrations legitimately
    extend the vocabulary (0046 added SUITE_EXPANSION), and a pinned comparison
    would fail for the one reason that is not a defect.
    """
    loader = MigrationLoader(None, ignore_no_migrations=True)
    latest = max(name for app, name in loader.disk_migrations
                 if app == "groups")
    state = load(("groups", latest))
    from_migration = list(
        state.models["groups", "remediationaction"].fields["action_class"].choices)
    assert from_migration == list(RemediationAction.CLASS_CHOICES), latest


def test_no_other_model_is_touched():
    for operation in operations():
        assert getattr(operation, "model_name", "") == "remediationaction"


def test_the_question_schema_is_untouched():
    before = load(BEFORE).models["groups", "question"].fields
    after = load(AFTER).models["groups", "question"].fields
    assert set(before) == set(after)
    for name in before:
        assert before[name].deconstruct()[1:] == after[name].deconstruct()[1:], name
    assert after["execution_contract_version"].max_length == 8


def test_the_pre_image_schema_is_untouched():
    for model in ("questionpreimage", "remediationbatch"):
        before = load(BEFORE).models["groups", model].fields
        after = load(AFTER).models["groups", model].fields
        assert set(before) == set(after)
        for name in before:
            assert before[name].deconstruct()[1:] == after[name].deconstruct()[1:], name

    assert PRE_IMAGE_SCHEMA_VERSION == 1
    assert pre_image.CAPTURED_FIELDS == (
        "content", "status", "trust_state", "execution_contract_version",
        "boilerplate_code", "hidden_wrapper_code", "hidden_test_cases")


# ═════════════════════════════════════════════════════════════
# Compatibility with rows that already exist
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def recorded(db):
    operator = User.objects.create_user(username="mig-op", password="pw",
                                        email="m@example.com", is_staff=True)
    portal = CodingPortal.objects.create(name="Mig Portal")
    topic, _ = Topic.objects.get_or_create(
        name="MigTopic", defaults={"structure_type": "flat", "portal": portal})
    question = Question.objects.create(
        id=9700, title="Q9700", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")
    batch = RemediationBatch.objects.create(
        batch_key="mig-batch", purpose="test", created_by=operator)
    record = pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)
    return batch, question, record, operator


def make_action(recorded, action_class):
    batch, question, record, operator = recorded
    return RemediationAction(
        batch=batch, question=question, pre_image=record,
        action_class=action_class, detail="", post_digest="0" * 64,
        applied_by=operator, applied_at=timezone.now())


@pytest.mark.django_db
@pytest.mark.parametrize("action_class", EXISTING)
def test_every_pre_existing_class_still_validates(recorded, action_class):
    action = make_action(recorded, action_class)
    action.full_clean()
    action.save()
    assert RemediationAction.objects.get(pk=action.pk).action_class == action_class


@pytest.mark.django_db
def test_input_repair_validates_and_stores(recorded):
    action = make_action(recorded, RemediationAction.CLASS_INPUT_REPAIR)
    action.full_clean()
    action.save()

    stored = RemediationAction.objects.get(pk=action.pk)
    assert stored.action_class == "INPUT_REPAIR"
    assert len("INPUT_REPAIR") <= 32


@pytest.mark.django_db
def test_an_unknown_class_is_still_rejected(recorded):
    """The vocabulary grew by one; it did not stop being a vocabulary."""
    action = make_action(recorded, "INPUT_REPAIRS")
    with pytest.raises(Exception):
        action.full_clean()


@pytest.mark.django_db
def test_actions_remain_append_only(recorded):
    action = make_action(recorded, RemediationAction.CLASS_INPUT_REPAIR)
    action.save()
    action.detail = "edited"
    with pytest.raises(Exception):
        action.save()
