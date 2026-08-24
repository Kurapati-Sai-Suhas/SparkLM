"""
The reseed ledger and its two action classes (M2 P2.7h-14).

The ledger exists for one reason: reseed's candidate selector is
`content__icontains=PLACEHOLDER_MARKER`, and reseed's first write removes that
marker — so a question that received a statement but not a signature is
finished by the selector's reckoning and half-done in fact. Progress cannot be
derived from the selector, so a 50-question slice that fails in the middle
needs a record.

These tests hold the boundary that makes such a record safe: it is
ORCHESTRATION state and nothing else. It cannot carry a digest, cannot grant
trust, publication or visibility, and rollback must not depend on it.

Local/synthetic database only.
"""

import json

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from groups import pre_image
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, ReseedLedger, Topic,
)

STUB_STARTER = ("class Solution:\n"
                "    def widgetCount(self, *args, **kwargs):\n"
                "        pass\n")


@pytest.fixture
def operator(db, django_user_model):
    return django_user_model.objects.create_user(
        username="reseed-op", password="pw", email="r@example.com",
        is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Reseed Portal")
    row, _ = Topic.objects.get_or_create(
        name="ReseedTopic", defaults={"structure_type": "flat",
                                      "portal": portal})
    return row


@pytest.fixture
def stub(topic):
    """A candidate in exactly the production shape: placeholder + variadic."""
    return Question.objects.create(
        id=9830, title="Widget Count", topic=topic, base_difficulty=1300.0,
        content=f"<p>{Question.PLACEHOLDER_MARKER} Widget Count</p>",
        boilerplate_code={"python": STUB_STARTER},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")


@pytest.fixture
def batch(stub, operator):
    row = RemediationBatch.objects.create(
        batch_key="reseed-test-A", purpose="ledger tests",
        created_by=operator)
    pre_image.capture(row, stub, operator)
    RemediationBatch.objects.filter(pk=row.pk).update(
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    row.refresh_from_db()
    return row


# ═════════════════════════════════════════════════════════════
# Action classes
# ═════════════════════════════════════════════════════════════

def test_the_two_generation_classes_exist_and_are_distinct():
    assert RemediationAction.CLASS_STATEMENT_GENERATION == "STATEMENT_GENERATION"
    assert RemediationAction.CLASS_SIGNATURE_DECLARATION == "SIGNATURE_DECLARATION"
    assert (RemediationAction.CLASS_STATEMENT_GENERATION
            != RemediationAction.CLASS_STATEMENT_REPAIR)
    assert (RemediationAction.CLASS_SIGNATURE_DECLARATION
            != RemediationAction.CLASS_BOILERPLATE_REPAIR)


def test_both_classes_are_selectable():
    choices = {value for value, _label in RemediationAction.CLASS_CHOICES}
    assert RemediationAction.CLASS_STATEMENT_GENERATION in choices
    assert RemediationAction.CLASS_SIGNATURE_DECLARATION in choices


def test_no_existing_action_class_was_removed_or_renamed():
    """
    The choice list is written into every historical row. Dropping a value
    would orphan the audit trail this milestone exists to keep.
    """
    choices = {value for value, _label in RemediationAction.CLASS_CHOICES}
    assert {"CONTRACT_REPAIR", "STATEMENT_REPAIR", "BOILERPLATE_REPAIR",
            "HIDDEN_TEST_REPAIR", "EXPECTED_OUTPUT_REPAIR", "INPUT_REPAIR",
            "SUITE_EXPANSION", "STATUS_TRANSITION", "MANUAL_REVIEW",
            "COMPLETE_REBUILD", "ROLLBACK"} <= choices


def test_expected_output_repair_still_has_no_writer():
    """
    Adding classes must not have quietly added the one the architecture
    refuses to build.
    """
    import pathlib

    commands = pathlib.Path(
        RemediationAction.__module__.replace(".", "/")).parent / "management"
    root = pathlib.Path(__file__).resolve().parent / "management" / "commands"
    writers = [path.name for path in root.glob("*.py")
               if "CLASS_EXPECTED_OUTPUT_REPAIR" in
               path.read_text(encoding="utf-8")]
    assert writers == []


# ═════════════════════════════════════════════════════════════
# Ledger: creation and membership
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_ledger_row_starts_pending(stub, batch):
    row = ReseedLedger.objects.create(batch=batch, question=stub)

    assert row.stage == ReseedLedger.STAGE_PENDING
    assert row.attempts == 0
    assert row.last_error == ""
    assert row.next_stage() == ReseedLedger.STAGE_STATEMENT


@pytest.mark.django_db
def test_one_ledger_row_per_question_per_batch(stub, batch):
    ReseedLedger.objects.create(batch=batch, question=stub)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReseedLedger.objects.create(batch=batch, question=stub)


@pytest.mark.django_db
def test_a_question_cannot_hold_two_rows_at_different_stages(stub, batch):
    """
    The uniqueness that matters. Two rows at the SAME stage collide under any
    constraint containing (batch, question); two rows at DIFFERENT stages
    collide only if `stage` is absent from the constraint — and a question
    recorded as both PENDING and COMPLETE has no answer to "what remains".
    A mutation sweep widened the constraint to include `stage` and every
    other test still passed.
    """
    ReseedLedger.objects.create(batch=batch, question=stub,
                                stage=ReseedLedger.STAGE_PENDING)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReseedLedger.objects.create(batch=batch, question=stub,
                                        stage=ReseedLedger.STAGE_COMPLETE)


@pytest.mark.django_db
def test_the_same_question_may_appear_in_a_different_slice(stub, batch,
                                                           operator):
    """Uniqueness is per batch — a later slice may revisit a question."""
    other = RemediationBatch.objects.create(
        batch_key="reseed-test-B", purpose="second slice", created_by=operator)
    ReseedLedger.objects.create(batch=batch, question=stub)

    ReseedLedger.objects.create(batch=other, question=stub)

    assert ReseedLedger.objects.filter(question=stub).count() == 2


# ═════════════════════════════════════════════════════════════
# Stage transitions and resume
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_stage_order_is_statement_then_signature(stub, batch):
    row = ReseedLedger.objects.create(batch=batch, question=stub)

    assert row.next_stage() == ReseedLedger.STAGE_STATEMENT
    row.stage = ReseedLedger.STAGE_STATEMENT
    assert row.next_stage() == ReseedLedger.STAGE_SIGNATURE
    row.stage = ReseedLedger.STAGE_SIGNATURE
    assert row.next_stage() == ReseedLedger.STAGE_CONTRACT
    # CONTRACT_SET is terminal in ADVANCES on purpose: what follows it is
    # suite authoring, which is not a reseed write (M2 P2.7h-27).
    row.stage = ReseedLedger.STAGE_CONTRACT
    assert row.next_stage() is None
    row.stage = ReseedLedger.STAGE_COMPLETE
    assert row.next_stage() is None


@pytest.mark.django_db
def test_a_completed_question_is_not_resumable(stub, batch):
    row = ReseedLedger.objects.create(batch=batch, question=stub,
                                      stage=ReseedLedger.STAGE_COMPLETE)
    assert row.is_resumable is False


@pytest.mark.django_db
def test_a_failed_question_is_resumable_and_records_why(stub, batch):
    row = ReseedLedger.objects.create(batch=batch, question=stub)

    row.stage = ReseedLedger.STAGE_FAILED
    row.last_error = "generated starter still declares *args"
    row.attempts = 1
    row.save()

    row.refresh_from_db()
    assert row.is_resumable is True
    assert "declares *args" in row.last_error
    assert row.attempts == 1
    # FAILED does not silently advance — the orchestrator must re-derive.
    assert row.next_stage() is None


@pytest.mark.django_db
def test_recording_a_successful_stage_is_idempotent(stub, batch):
    """
    Resume must be safe to run twice: re-recording the stage a question is
    already at changes nothing.
    """
    row = ReseedLedger.objects.create(batch=batch, question=stub)
    for _ in range(3):
        ReseedLedger.objects.update_or_create(
            batch=batch, question=stub,
            defaults={"stage": ReseedLedger.STAGE_STATEMENT})

    assert ReseedLedger.objects.filter(batch=batch, question=stub).count() == 1
    row.refresh_from_db()
    assert row.stage == ReseedLedger.STAGE_STATEMENT


@pytest.mark.django_db
def test_the_models_and_the_migrations_have_not_drifted():
    """
    The ledger's uniqueness is enforced by the MIGRATION, so a model-only
    edit is inert against an existing database — a mutation sweep widened the
    constraint in `models.py` and every behavioural test still passed, because
    the table had already been built from 0048.

    Drift is therefore the defect to detect: any change to these models
    without a migration is unenforced everywhere the table already exists.
    """
    import io

    from django.core.management import call_command

    try:
        call_command("makemigrations", "groups", check=True, dry_run=True,
                     verbosity=0, stdout=io.StringIO())
    except SystemExit as exit_signal:
        raise AssertionError(
            "groups models have changes with no migration — an unmigrated "
            "constraint is not enforced on any existing database"
        ) from exit_signal


# ═════════════════════════════════════════════════════════════
# What the ledger must NOT be
# ═════════════════════════════════════════════════════════════

def test_the_ledger_carries_no_digest():
    """
    A resumable writer is exactly where a digest handshake rots into "the
    ledger says this was the value". There is no field here to trust.
    """
    names = {field.name for field in ReseedLedger._meta.get_fields()}
    for forbidden in ("digest", "state_digest", "post_digest",
                      "expect_digest", "expected_digest", "before_digest"):
        assert forbidden not in names
    assert not any("digest" in name for name in names)


def test_the_ledger_carries_no_trust_or_visibility_field():
    names = {field.name for field in ReseedLedger._meta.get_fields()}
    for forbidden in ("status", "trust_state", "hidden_test_cases",
                      "expected_output", "approval", "published",
                      "adaptive_eligible", "is_authoritative",
                      "execution_contract_version"):
        assert forbidden not in names


@pytest.mark.django_db
def test_a_ledger_row_cannot_make_a_question_trusted_or_visible(stub, batch):
    """
    The property in one test: drive the ledger to COMPLETE and the question is
    untouched — still DRAFT, still UNVERIFIED, still invisible.
    """
    from groups import coding_views

    before = pre_image.live_digest(stub)

    ReseedLedger.objects.create(batch=batch, question=stub,
                                stage=ReseedLedger.STAGE_COMPLETE)

    stub.refresh_from_db()
    assert stub.status == Question.STATUS_DRAFT
    assert stub.trust_state == Question.TRUST_UNVERIFIED
    assert stub.is_adaptive_eligible is False
    assert stub.hidden_test_cases == []
    assert pre_image.live_digest(stub) == before
    assert coding_views._servable_question(stub.pk) is None


def test_no_production_code_reads_the_ledger_to_make_a_decision():
    """
    Orchestration state, structurally. Until the orchestrator exists nothing
    imports it; when it does, it must be the only reader — and never the
    grading, serving, trust or approval paths.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent
    readers = []
    for path in list(root.glob("*.py")) + list(
            root.glob("management/commands/*.py")):
        if "test" in path.name or path.name == "models.py":
            continue
        if "ReseedLedger" in path.read_text(encoding="utf-8"):
            readers.append(path.name)
    forbidden = {"coding_views.py", "services.py", "question_promote.py",
                 "question_approve.py", "question_status.py",
                 "oracle_execute.py", "question_artifact.py", "oracle.py",
                 "pre_image.py", "provenance.py"}
    assert not (set(readers) & forbidden), (
        f"the trust/serving path reads the ledger: {set(readers) & forbidden}")


# ═════════════════════════════════════════════════════════════
# Rollback does not depend on the ledger
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_rollback_works_with_no_ledger_row_at_all(stub, batch, operator):
    """
    Restoration reads the PRE-IMAGE. The ledger records what was attempted;
    the pre-image records what was true, and only the second is authoritative.
    """
    assert not ReseedLedger.objects.filter(question=stub).exists()

    Question.objects.filter(pk=stub.pk).update(content="<p>generated</p>")
    stub.refresh_from_db()
    record = QuestionPreImage.objects.get(batch=batch, question=stub)
    assert "content" in pre_image.differing_fields(record, stub)

    plan = pre_image.rollback_plan(batch, [stub])
    assert plan, "rollback could not be planned without a ledger row"


@pytest.mark.django_db
def test_a_wrong_ledger_stage_does_not_change_what_rollback_restores(
        stub, batch, operator):
    """A lying ledger must not alter restoration."""
    Question.objects.filter(pk=stub.pk).update(content="<p>generated</p>")
    stub.refresh_from_db()
    record = QuestionPreImage.objects.get(batch=batch, question=stub)
    without = pre_image.differing_fields(record, stub)

    ReseedLedger.objects.create(batch=batch, question=stub,
                                stage=ReseedLedger.STAGE_COMPLETE)

    stub.refresh_from_db()
    assert pre_image.differing_fields(record, stub) == without


# ═════════════════════════════════════════════════════════════
# Existing machinery is unaffected
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_existing_remediation_actions_still_record_normally(stub, batch,
                                                            operator):
    action = pre_image.record_action(
        batch, stub, RemediationAction.CLASS_MANUAL_REVIEW, operator,
        detail="unchanged by the new classes")

    assert action.action_class == RemediationAction.CLASS_MANUAL_REVIEW
    assert action.post_digest == pre_image.live_digest(stub)


@pytest.mark.django_db
def test_an_action_is_still_append_only(stub, batch, operator):
    action = pre_image.record_action(
        batch, stub, RemediationAction.CLASS_STATEMENT_GENERATION, operator,
        detail="generated")

    action.detail = "edited"
    with pytest.raises(ValidationError):
        action.save()


@pytest.mark.django_db
def test_a_generation_action_records_like_any_other(stub, batch, operator):
    for action_class in (RemediationAction.CLASS_STATEMENT_GENERATION,
                         RemediationAction.CLASS_SIGNATURE_DECLARATION):
        action = pre_image.record_action(batch, stub, action_class, operator,
                                         detail="reseed")
        assert action.pk is not None
        assert action.post_digest == pre_image.live_digest(stub)

    assert RemediationAction.objects.filter(question=stub).count() == 2


@pytest.mark.django_db
def test_the_pre_image_is_still_immutable(stub, batch):
    record = QuestionPreImage.objects.get(batch=batch, question=stub)
    record.content = "tampered"
    with pytest.raises(ValidationError):
        record.save()


# ─── P2.7h-15: the ledger's foreign keys ────────────────────────────────
#
# Migration 0048 emits both FKs as plain references with NO ACTION on
# delete (confdeltype='a'), verified directly against a disposable
# database. So the database alone guarantees only that a ledger row can
# never be orphaned; `on_delete=PROTECT` is what turns that into a clear
# application-level refusal instead of an IntegrityError from the driver.
#
# This matters in one direction worth stating plainly: the ledger now
# holds a veto over deleting a question. That is deliberate — an
# orchestration record must not outlive its subject — but it is a new
# coupling, so it is asserted rather than assumed.


@pytest.mark.django_db
def test_a_question_with_a_ledger_row_cannot_be_deleted(stub, batch):
    ReseedLedger.objects.create(batch=batch, question=stub)

    with pytest.raises(ProtectedError):
        stub.delete()

    assert Question.objects.filter(pk=stub.pk).exists()
    assert ReseedLedger.objects.filter(question=stub).count() == 1


@pytest.mark.django_db
def test_a_batch_with_a_ledger_row_cannot_be_deleted(stub, batch):
    ReseedLedger.objects.create(batch=batch, question=stub)

    with pytest.raises(ProtectedError):
        batch.delete()

    assert RemediationBatch.objects.filter(pk=batch.pk).exists()


@pytest.mark.django_db
def test_the_ledger_cannot_reference_a_question_that_does_not_exist(batch):
    """Both FKs are DEFERRABLE INITIALLY DEFERRED, as Django emits them.

    So the violation is not raised by the INSERT — it is raised at COMMIT,
    which never arrives inside a test wrapped in a rolled-back
    transaction. The check is forced here rather than skipped, because
    the point being asserted is that the constraint exists and bites.
    """
    with transaction.atomic():
        ReseedLedger.objects.create(batch=batch, question_id=999_999_999)
        with pytest.raises(IntegrityError):
            connection.check_constraints()
        transaction.set_rollback(True)


@pytest.mark.django_db
def test_deleting_a_ledger_row_leaves_its_question_and_batch_intact(
        stub, batch):
    """The veto runs one way only — discarding orchestration state is free."""
    row = ReseedLedger.objects.create(batch=batch, question=stub)
    digest_before = pre_image.live_digest(stub)

    row.delete()

    stub.refresh_from_db()
    assert pre_image.live_digest(stub) == digest_before
    assert Question.objects.filter(pk=stub.pk).exists()
    assert RemediationBatch.objects.filter(pk=batch.pk).exists()
