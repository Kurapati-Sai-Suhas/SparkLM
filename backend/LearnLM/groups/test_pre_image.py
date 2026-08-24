"""
Pre-image capture and rollback (M2 P2.7, blocker J8).

The tooling that must exist before any production grading-truth write. These
tests are the proof that it does what its name claims — that a captured state
really restores, that a partial restore is impossible, and that the system
refuses rather than guesses when anything is off.

Local/synthetic database only. No production, no Judge0, no oracle.
"""

import copy

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from groups import pre_image as pi
from groups import provenance
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)
from django.contrib.auth import get_user_model

User = get_user_model()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def actor(db):
    return User.objects.create_user(username="operator", password="pw",
                                    email="op@example.com")


@pytest.fixture
def other_actor(db):
    return User.objects.create_user(username="second", password="pw",
                                    email="second@example.com")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="PreImage Portal")
    made, _ = Topic.objects.get_or_create(
        name="PreImageTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id=7100, **overrides):
    fields = {
        "id": question_id,
        "title": f"Q{question_id}",
        "content": "Return the input.",
        "topic": topic,
        "base_difficulty": 1200.0,
        "boilerplate_code": {"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        "hidden_test_cases": [
            {"stdin": "110", "expected_output": "3"},
            {"stdin": "007", "expected_output": "3"},
        ],
        "hidden_wrapper_code": {},
        "execution_contract_version": "v1",
    }
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def question(db, topic):
    return make_question(topic)


@pytest.fixture
def batch(db, actor):
    return RemediationBatch.objects.create(
        batch_key="pilot-test", purpose="unit test batch", created_by=actor)


def captured(batch, question, actor):
    """Capture one question and freeze the batch — the whole of Phase A."""
    pre = pi.capture(batch, question, actor)
    pi.freeze(batch, actor)
    return pre


# ═════════════════════════════════════════════════════════════
# 1. Capture completeness
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_every_captured_field_is_stored_verbatim(batch, question, actor):
    pre = pi.capture(batch, question, actor)
    for name in pi.CAPTURED_FIELDS:
        assert pre.captured_state()[name] == getattr(question, name), name


def test_captured_fields_covers_every_remediable_question_field():
    """
    The list that makes restoration possible. A remediable field missing here
    produces a pre-image that silently cannot restore, so this pins the set
    against the model rather than against a comment.
    """
    remediable = {
        "content", "status", "trust_state", "execution_contract_version",
        "boilerplate_code", "hidden_wrapper_code", "hidden_test_cases",
    }
    assert set(pi.CAPTURED_FIELDS) == remediable


@pytest.mark.django_db
def test_the_derived_eligibility_is_recorded(batch, topic, actor):
    """
    `adaptive_eligible` is derived on Question, not stored, so the pre-image
    records the trust boundary's verdict as a cross-check rather than as a
    field to restore.
    """
    trusted = make_question(topic, 7101, status=Question.STATUS_PUBLISHED,
                            trust_state=Question.TRUST_ORACLE_VERIFIED)
    pre = pi.capture(batch, trusted, actor)
    assert pre.was_adaptive_eligible is True


@pytest.mark.django_db
def test_capture_uses_the_shared_case_identity(batch, question, actor):
    """Not a second scheme — the digest the oracle and the artifact use."""
    pre = pi.capture(batch, question, actor)
    expected = provenance.case_identity("110")
    assert pre.case_identities[0]["case"] == expected


@pytest.mark.django_db
def test_case_identity_normalises_and_is_not_a_raw_hash(batch, topic, actor):
    """
    `case_identity` digests `normalize_output(stdin)`; `output_identity`
    digests the raw bytes. For a clean input the two AGREE, which is why an
    input carrying trailing whitespace is needed to tell them apart — without
    it, swapping one for the other is undetectable.
    """
    messy = make_question(topic, 7103, hidden_test_cases=[
        {"stdin": "110  \n", "expected_output": "3"}])
    pre = pi.capture(batch, messy, actor)

    assert pre.case_identities[0]["case"] == provenance.case_identity("110  \n")
    assert pre.case_identities[0]["case"] != provenance.output_identity("110  \n")


@pytest.mark.django_db
def test_a_structurally_broken_suite_can_still_be_captured(batch, topic, actor):
    """
    48 production questions store a list where text belongs. Refusing to
    capture them would make exactly the questions most in need of repair the
    ones that cannot be repaired safely.
    """
    broken = make_question(topic, 7102, hidden_test_cases=[
        {"stdin": "1", "expected_output": ["a", "b"]}])
    pre = pi.capture(batch, broken, actor)
    assert pre.hidden_test_cases == [{"stdin": "1", "expected_output": ["a", "b"]}]
    pi.verify(pre)


# ═════════════════════════════════════════════════════════════
# 2. Immutability
# ═════════════════════════════════════════════════════════════

def test_the_production_capture_alias_never_exists_under_pytest():
    """
    The `preimage` alias carries PRODUCTION credentials with INSERT rights on
    the pre-image tables. Under pytest it must not exist at all — one
    `.using("preimage")` in a test would otherwise write to production.

    `sparklm_test_isolation` blanks PREIMAGE_* before Django loads. Blanking,
    not deleting: `load_dotenv` restores an absent key and skips a present one.
    """
    from django.conf import settings
    assert "preimage" not in settings.DATABASES


@pytest.mark.django_db
def test_every_write_follows_the_batch_alias(batch, question, actor,
                                             monkeypatch):
    """
    The alias the batch came from is the alias every related write must use.

    Until this was fixed, `pre_image` used the default manager throughout while
    the command created the batch on `--alias`. Running the real command as the
    capture role exposed it: the batch went to one connection and the
    pre-images to another. On a database where `default` happened to be
    writable, rows would have landed silently on the wrong target with a batch
    pointing at nothing.

    Asserted by capturing which alias each write names, rather than by reading
    the code.
    """
    used = []
    original = pi.QuestionPreImage.objects.using

    def record(alias):
        used.append(alias)
        return original(alias)

    monkeypatch.setattr(pi.QuestionPreImage.objects, "using", record)
    pi.capture(batch, question, actor)

    assert used, "capture did not route its write through an explicit alias"
    assert set(used) == {pi.alias_of(batch)}


@pytest.mark.django_db
def test_alias_of_falls_back_to_the_default(batch):
    from django.db import DEFAULT_DB_ALIAS
    assert pi.alias_of(batch) == batch._state.db
    batch._state.db = None
    assert pi.alias_of(batch) == DEFAULT_DB_ALIAS


@pytest.mark.django_db
def test_a_pre_image_cannot_be_edited(batch, question, actor):
    pre = pi.capture(batch, question, actor)
    pre.content = "tampered"
    with pytest.raises(ValidationError):
        pre.save()


@pytest.mark.django_db
def test_a_second_capture_does_not_overwrite_the_first(batch, question, actor,
                                                       other_actor):
    """
    The core immutability requirement: a later remediation must not be able to
    destroy the record of what an earlier one found.
    """
    first = pi.capture(batch, question, actor)
    original_digest = first.state_digest

    question.content = "changed by remediation 1"
    question.save(update_fields=["content"])

    second = pi.capture(batch, question, other_actor)
    assert second.pk == first.pk
    assert second.state_digest == original_digest
    assert second.content == "Return the input."


@pytest.mark.django_db
def test_two_pre_images_for_one_question_in_one_batch_are_impossible(
        batch, question, actor):
    pi.capture(batch, question, actor)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            QuestionPreImage.objects.create(
                batch=batch, question=question, content="x", status="DRAFT",
                trust_state="UNVERIFIED", execution_contract_version="v1",
                state_digest="0" * 64, captured_by=actor,
                captured_at=question.created_at if hasattr(question, "created_at")
                else None)


@pytest.mark.django_db
def test_every_capture_records_actor_time_batch_and_schema(batch, question, actor):
    pre = pi.capture(batch, question, actor)
    assert pre.captured_by_id == actor.id
    assert pre.captured_at is not None
    assert pre.batch_id == batch.id
    assert pre.schema_version == 1
    assert len(pre.state_digest) == 64


@pytest.mark.django_db
def test_a_remediation_action_cannot_be_edited(batch, question, actor):
    captured(batch, question, actor)
    action = pi.record_action(batch, question, RemediationAction.CLASS_MANUAL_REVIEW,
                              actor, detail="looked at it")
    action.detail = "rewritten history"
    with pytest.raises(ValidationError):
        action.save()


# ═════════════════════════════════════════════════════════════
# 3. Digest behaviour
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_digest_is_deterministic(batch, question, actor):
    first = pi.live_digest(question)
    question.refresh_from_db()
    assert pi.live_digest(question) == first


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [
    ("content", "different statement"),
    ("status", Question.STATUS_PUBLISHED),
    ("trust_state", Question.TRUST_ORACLE_VERIFIED),
    ("execution_contract_version", "v3"),
    ("boilerplate_code", {"python": "class Solution:\n    def solve(self): pass\n"}),
    ("hidden_wrapper_code", {"python": "# harness\n{user_code}\n"}),
    ("hidden_test_cases", [{"stdin": "9", "expected_output": "1"}]),
])
def test_changing_any_captured_field_moves_the_digest(question, field, value):
    before = pi.live_digest(question)
    setattr(question, field, value)
    assert pi.live_digest(question) != before, field


@pytest.mark.django_db
def test_a_nested_json_edit_moves_the_digest(question):
    """A one-character change inside a nested case must not hide."""
    before = pi.live_digest(question)
    cases = copy.deepcopy(question.hidden_test_cases)
    cases[1]["expected_output"] = "4"
    question.hidden_test_cases = cases
    assert pi.live_digest(question) != before


@pytest.mark.django_db
def test_a_non_identity_edit_inside_a_case_still_moves_the_digest(question):
    """
    The suite is digested as a FIELD as well as by case identity. Adding a
    `category` key changes neither the stdin nor the expected output, so the
    case identities are untouched — only the field frame catches it. Without
    that frame this edit would be invisible to rollback's divergence check.
    """
    before = pi.live_digest(question)
    cases = copy.deepcopy(question.hidden_test_cases)
    cases[0]["category"] = "boundary"
    question.hidden_test_cases = cases
    assert pi.live_digest(question) != before


@pytest.mark.django_db
def test_the_schema_version_participates_in_the_digest(question, monkeypatch):
    """
    Emitted first, so a pre-image taken under one field set can never collide
    with one taken under another. Without it, a schema change would silently
    reinterpret old pre-images instead of invalidating them.
    """
    before = pi.live_digest(question)
    monkeypatch.setattr(pi, "PRE_IMAGE_SCHEMA_VERSION", 99)
    assert pi.live_digest(question) != before


@pytest.mark.django_db
def test_verify_rejects_a_corrupted_pre_image(batch, question, actor):
    pre = pi.capture(batch, question, actor)
    QuestionPreImage.objects.filter(pk=pre.pk).update(content="corrupted")
    pre.refresh_from_db()
    with pytest.raises(pi.DigestMismatch):
        pi.verify(pre)


# ═════════════════════════════════════════════════════════════
# 4. Write-ahead safety (Phase A -> Phase B)
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_modification_without_a_pre_image_is_rejected(batch, question, topic,
                                                      actor):
    """The write-ahead rule, and the whole point of the module."""
    pi.capture(batch, question, actor)
    pi.freeze(batch, actor)

    uncaptured = make_question(topic, 7199)
    with pytest.raises(pi.CaptureIncomplete):
        pi.require_pre_image(batch, uncaptured)


@pytest.mark.django_db
def test_modification_before_the_batch_is_frozen_is_rejected(batch, question,
                                                             actor):
    """Partial capture must block modification: the set is not final yet."""
    pi.capture(batch, question, actor)
    with pytest.raises(pi.CaptureIncomplete):
        pi.require_pre_image(batch, question)


@pytest.mark.django_db
def test_a_rejected_modification_changes_nothing(batch, question, topic, actor):
    pi.capture(batch, question, actor)
    pi.freeze(batch, actor)
    uncaptured = make_question(topic, 7198)
    before = pi.live_digest(uncaptured)

    with pytest.raises(pi.CaptureIncomplete):
        pi.record_action(batch, uncaptured,
                         RemediationAction.CLASS_CONTRACT_REPAIR, actor)

    uncaptured.refresh_from_db()
    assert pi.live_digest(uncaptured) == before
    assert not RemediationAction.objects.filter(question=uncaptured).exists()


@pytest.mark.django_db
def test_an_empty_batch_cannot_be_frozen(batch, actor):
    """Freezing nothing would look like a completed capture phase."""
    with pytest.raises(pi.CaptureIncomplete):
        pi.freeze(batch, actor)


@pytest.mark.django_db
def test_a_frozen_batch_refuses_new_members(batch, question, topic, actor):
    captured(batch, question, actor)
    with pytest.raises(pi.PreImageError):
        pi.capture(batch, make_question(topic, 7197), actor)


@pytest.mark.django_db
def test_freezing_verifies_every_capture(batch, question, actor):
    pre = pi.capture(batch, question, actor)
    QuestionPreImage.objects.filter(pk=pre.pk).update(content="corrupted")
    with pytest.raises(pi.DigestMismatch):
        pi.freeze(batch, actor)


# ═════════════════════════════════════════════════════════════
# 5. Rollback
# ═════════════════════════════════════════════════════════════

def remediate(question, **changes):
    for name, value in changes.items():
        setattr(question, name, value)
    question.save(update_fields=list(changes))


@pytest.mark.django_db
def test_one_question_rolls_back(batch, question, actor):
    captured(batch, question, actor)
    before = pi.live_digest(question)

    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)

    pi.rollback(batch, actor)
    question.refresh_from_db()
    assert question.content == "Return the input."
    assert pi.live_digest(question) == before


@pytest.mark.django_db
def test_hidden_test_and_expected_output_changes_roll_back(batch, question, actor):
    captured(batch, question, actor)
    original = copy.deepcopy(question.hidden_test_cases)

    remediate(question, hidden_test_cases=[
        {"stdin": "110", "expected_output": "REWRITTEN"},
        {"stdin": "007", "expected_output": "3"},
        {"stdin": "new", "expected_output": "case"}])
    pi.record_action(batch, question,
                     RemediationAction.CLASS_EXPECTED_OUTPUT_REPAIR, actor)

    pi.rollback(batch, actor)
    question.refresh_from_db()
    assert question.hidden_test_cases == original


@pytest.mark.django_db
def test_a_contract_change_rolls_back(batch, question, actor):
    captured(batch, question, actor)
    remediate(question, execution_contract_version="v3")
    pi.record_action(batch, question, RemediationAction.CLASS_CONTRACT_REPAIR,
                     actor)
    pi.rollback(batch, actor)
    question.refresh_from_db()
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_multiple_questions_roll_back_together(batch, topic, actor):
    questions = [make_question(topic, 7200 + n) for n in range(3)]
    for q in questions:
        pi.capture(batch, q, actor)
    pi.freeze(batch, actor)

    for q in questions:
        remediate(q, content=f"remediated {q.pk}")
        pi.record_action(batch, q, RemediationAction.CLASS_STATEMENT_REPAIR, actor)

    pi.rollback(batch, actor)
    for q in questions:
        q.refresh_from_db()
        assert q.content == "Return the input."


@pytest.mark.django_db
def test_repeated_remediation_of_one_question_still_rolls_back_to_the_original(
        batch, question, actor):
    """Two actions, one pre-image: the destination is the ORIGINAL state."""
    captured(batch, question, actor)

    remediate(question, content="first pass")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR, actor)
    remediate(question, content="second pass", execution_contract_version="v3")
    pi.record_action(batch, question, RemediationAction.CLASS_CONTRACT_REPAIR, actor)

    pi.rollback(batch, actor)
    question.refresh_from_db()
    assert question.content == "Return the input."
    assert question.execution_contract_version == "v1"


@pytest.mark.django_db
def test_rollback_is_atomic_across_the_batch(batch, topic, actor, monkeypatch):
    """
    All or nothing. The second restore is made to fail; the first must not
    survive it.
    """
    questions = [make_question(topic, 7300 + n) for n in range(3)]
    for q in questions:
        pi.capture(batch, q, actor)
    pi.freeze(batch, actor)
    for q in questions:
        remediate(q, content=f"remediated {q.pk}")
        pi.record_action(batch, q, RemediationAction.CLASS_STATEMENT_REPAIR, actor)

    real_digest = pi.live_digest
    calls = {"n": 0}

    def failing_digest(question):
        calls["n"] += 1
        # Corrupt the verification of the second restore only.
        if calls["n"] > 4:
            return "0" * 64
        return real_digest(question)

    monkeypatch.setattr(pi, "live_digest", failing_digest)

    with pytest.raises(pi.DigestMismatch):
        pi.rollback(batch, actor)

    monkeypatch.setattr(pi, "live_digest", real_digest)
    for q in questions:
        q.refresh_from_db()
        assert q.content == f"remediated {q.pk}", (
            "a partial rollback survived; restoration must be all-or-nothing")


@pytest.mark.django_db
def test_rollback_detects_divergence_rather_than_overwriting(batch, question,
                                                             actor):
    """
    Someone edited the question after the remediation. Restoring blindly would
    discard their change without saying so.
    """
    captured(batch, question, actor)
    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)

    remediate(question, content="edited by someone else afterwards")

    with pytest.raises(pi.DigestMismatch) as caught:
        pi.rollback(batch, actor)
    assert "changed since" in str(caught.value)

    question.refresh_from_db()
    assert question.content == "edited by someone else afterwards"


@pytest.mark.django_db
def test_divergence_can_be_overridden_deliberately_and_is_recorded(batch,
                                                                   question,
                                                                   actor):
    captured(batch, question, actor)
    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)
    remediate(question, content="edited afterwards")

    pi.rollback(batch, actor, allow_divergence=True)
    question.refresh_from_db()
    assert question.content == "Return the input."

    rollback_action = RemediationAction.objects.filter(
        batch=batch, action_class=RemediationAction.CLASS_ROLLBACK).first()
    assert "divergence overridden" in rollback_action.detail


@pytest.mark.django_db
def test_rollback_refuses_a_corrupt_pre_image(batch, question, actor):
    captured(batch, question, actor)
    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)

    pre = QuestionPreImage.objects.get(batch=batch, question=question)
    QuestionPreImage.objects.filter(pk=pre.pk).update(content="corrupted")

    with pytest.raises(pi.DigestMismatch):
        pi.rollback(batch, actor)
    question.refresh_from_db()
    assert question.content == "remediated", "nothing may be restored from a "\
                                             "pre-image that does not verify"


@pytest.mark.django_db
def test_corruption_is_not_overridable_by_allow_divergence(batch, question, actor):
    """`allow_divergence` is not a force flag for a broken pre-image."""
    captured(batch, question, actor)
    pre = QuestionPreImage.objects.get(batch=batch, question=question)
    QuestionPreImage.objects.filter(pk=pre.pk).update(content="corrupted")

    with pytest.raises(pi.DigestMismatch):
        pi.rollback(batch, actor, allow_divergence=True)


@pytest.mark.django_db
def test_rollback_of_an_unfrozen_batch_is_refused(batch, question, actor):
    pi.capture(batch, question, actor)
    with pytest.raises(pi.CaptureIncomplete):
        pi.rollback(batch, actor)


@pytest.mark.django_db
def test_rollback_touches_no_unrelated_question(batch, topic, question, actor):
    outsider = make_question(topic, 7400)
    before = pi.live_digest(outsider)

    captured(batch, question, actor)
    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)
    pi.rollback(batch, actor)

    outsider.refresh_from_db()
    assert pi.live_digest(outsider) == before


@pytest.mark.django_db
def test_a_subset_can_be_restored_without_the_rest(batch, topic, actor):
    questions = [make_question(topic, 7500 + n) for n in range(3)]
    for q in questions:
        pi.capture(batch, q, actor)
    pi.freeze(batch, actor)
    for q in questions:
        remediate(q, content=f"remediated {q.pk}")
        pi.record_action(batch, q, RemediationAction.CLASS_STATEMENT_REPAIR, actor)

    pi.rollback(batch, actor, questions=[questions[1]])

    questions[0].refresh_from_db()
    questions[1].refresh_from_db()
    assert questions[0].content == f"remediated {questions[0].pk}"
    assert questions[1].content == "Return the input."


@pytest.mark.django_db
def test_restoring_an_unknown_question_is_refused(batch, topic, question, actor):
    captured(batch, question, actor)
    with pytest.raises(pi.CaptureIncomplete):
        pi.rollback(batch, actor, questions=[make_question(topic, 7600)])


# ═════════════════════════════════════════════════════════════
# 6. Rollback safety: provenance and learner state
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_rollback_scope_is_the_question_alone():
    """
    Provenance is evidence, and evidence is not reversible. An oracle run
    happened; a person approved. Rollback restores the question and leaves the
    record of what people did intact.
    """
    assert pi.ROLLBACK_SCOPE == ("groups_question",)


@pytest.mark.django_db
def test_rollback_writes_only_question_rows(batch, question, actor):
    """
    Structural: the restore path may not touch a reference, an execution, an
    approval or a submission. By AST, over real Call nodes — the module's
    docstring names those models, so a text search would pass for the wrong
    reason.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(pi.rollback)))
    written = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
                "objects", "save", "delete", "update"):
            target = node.value
            if isinstance(target, ast.Name):
                written.add(target.id)
    forbidden = {"ReferenceSolution", "OracleExecution", "QuestionApproval",
                 "CodeSubmission", "UserTopicMastery", "UserCodingProfile"}
    assert not (written & forbidden), written & forbidden


@pytest.mark.django_db
def test_rollback_creates_an_append_only_audit_record(batch, question, actor):
    captured(batch, question, actor)
    remediate(question, content="remediated")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR,
                     actor)
    pi.rollback(batch, actor)

    rollbacks = RemediationAction.objects.filter(
        batch=batch, action_class=RemediationAction.CLASS_ROLLBACK)
    assert rollbacks.count() == 1
    assert rollbacks.first().applied_by_id == actor.id

    # The PRIOR action must survive. Rolling back the data does not un-happen
    # the work, and deleting the record of it would destroy the audit trail
    # that explains why the question was changed at all.
    assert RemediationAction.objects.filter(
        batch=batch,
        action_class=RemediationAction.CLASS_STATEMENT_REPAIR).count() == 1


@pytest.mark.django_db
def test_rollback_preserves_every_earlier_action_record(batch, question, actor):
    """Append-only across a full remediate-remediate-rollback sequence."""
    captured(batch, question, actor)
    remediate(question, content="first")
    pi.record_action(batch, question, RemediationAction.CLASS_STATEMENT_REPAIR, actor)
    remediate(question, execution_contract_version="v3")
    pi.record_action(batch, question, RemediationAction.CLASS_CONTRACT_REPAIR, actor)

    before = set(RemediationAction.objects.values_list("pk", flat=True))
    pi.rollback(batch, actor)
    after = set(RemediationAction.objects.values_list("pk", flat=True))

    assert before <= after, "rollback destroyed audit evidence"
    assert len(after) == len(before) + 1


@pytest.mark.django_db
def test_rollback_does_not_fabricate_a_verified_trust_state(batch, topic, actor):
    """
    Restoring must reproduce the captured trust state exactly — never a more
    trusting one. A rollback that promoted a question would manufacture trust
    nobody granted.
    """
    q = make_question(topic, 7700, status=Question.STATUS_DRAFT,
                      trust_state=Question.TRUST_UNVERIFIED)
    captured(batch, q, actor)

    remediate(q, status=Question.STATUS_PUBLISHED,
              trust_state=Question.TRUST_ORACLE_VERIFIED)
    pi.record_action(batch, q, RemediationAction.CLASS_MANUAL_REVIEW, actor)

    pi.rollback(batch, actor)
    q.refresh_from_db()
    assert q.status == Question.STATUS_DRAFT
    assert q.trust_state == Question.TRUST_UNVERIFIED
    assert q.is_adaptive_eligible is False


# ═════════════════════════════════════════════════════════════
# 7. The q264 control invariant
# ═════════════════════════════════════════════════════════════

#: The SAFE control in the 7-question pilot. It exists to prove the workflow
#: leaves a healthy question alone, which it can only do if nothing touches it.
CONTROL_QUESTION_ID = 264


@pytest.mark.django_db
def test_the_control_question_is_captured_like_any_other(batch, topic, actor):
    control = make_question(topic, CONTROL_QUESTION_ID)
    pre = pi.capture(batch, control, actor)
    pi.verify(pre)
    assert pre.question_id == CONTROL_QUESTION_ID


@pytest.mark.django_db
def test_the_control_question_must_survive_a_batch_byte_identical(
        batch, topic, actor):
    """
    The hard pilot invariant: q264 goes in and comes out unchanged, byte for
    byte, while everything around it is remediated and rolled back.
    """
    control = make_question(topic, CONTROL_QUESTION_ID)
    subject = make_question(topic, 7800)
    control_digest = pi.live_digest(control)

    for q in (control, subject):
        pi.capture(batch, q, actor)
    pi.freeze(batch, actor)

    remediate(subject, content="remediated", execution_contract_version="v3")
    pi.record_action(batch, subject, RemediationAction.CLASS_CONTRACT_REPAIR, actor)

    control.refresh_from_db()
    assert pi.live_digest(control) == control_digest, "the control moved"

    pi.rollback(batch, actor)
    control.refresh_from_db()
    assert pi.live_digest(control) == control_digest, (
        "the control moved during rollback")


@pytest.mark.django_db
def test_a_changed_control_question_is_detected(batch, topic, actor):
    """The invariant must be enforceable, not merely stated."""
    control = make_question(topic, CONTROL_QUESTION_ID)
    before = pi.live_digest(control)
    remediate(control, content="someone touched the control")
    assert pi.live_digest(control) != before
