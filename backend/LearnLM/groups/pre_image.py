"""
Pre-image capture and rollback (M2 P2.7, blocker J8).

Nothing may write production grading truth until the prior state can be
restored exactly. This module is that prerequisite: it captures a complete,
immutable, digest-verified copy of everything a remediation can touch, and
restores it atomically or not at all.

── Why a full copy and not a diff ──────────────────────────────────────────

A diff is only sufficient for restoration if the base it was computed against
is still available and still identical. Both assumptions fail exactly when
rollback matters — after a second remediation, after a partial failure, after
a manual edit. `hidden_test_cases` is also a nested JSON document where a
positional diff is meaningless the moment a case is inserted or reordered.

So the pre-image stores the WHOLE prior value of every mutable field. It costs
a few kilobytes per question and removes an entire class of "we can almost
restore it" outcomes.

── Identity is borrowed, never reinvented ──────────────────────────────────

Case identity is `provenance.case_identity` — the same digest
`OracleExecution`, `question_artifact` and the quality gate already use. A
second scheme would mean a case could be "the same case" for provenance and a
different one for rollback, which is how a restore silently reattaches the
wrong answer to the wrong input.

The digest encoding is `question_artifact`'s length-prefixed framing, for the
same reason: one canonical encoding in the system, not two.

── What this module does NOT do ────────────────────────────────────────────

It does not remediate. It captures, verifies and restores. It never writes
`ReferenceSolution`, `OracleExecution`, `QuestionApproval`, `CodeSubmission`
or any learner state — rollback restores the QUESTION, and leaves the record
of what people did untouched, because that record is evidence and evidence is
not reversible. See `ROLLBACK_SCOPE`.
"""

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils import timezone

from groups import provenance
from groups.models import (
    PRE_IMAGE_SCHEMA_VERSION, Question, QuestionPreImage, RemediationAction,
    RemediationBatch,
)
from groups.question_artifact import _frame, digest_of

#: Every Question field a remediation is permitted to modify, and therefore
#: every field the pre-image must carry. Adding a remediable field WITHOUT
#: adding it here would produce a pre-image that cannot restore the prior
#: state — so `test_pre_image.py` asserts this list against the model.
CAPTURED_FIELDS = (
    "content",
    "status",
    "trust_state",
    "execution_contract_version",
    "boilerplate_code",
    "hidden_wrapper_code",
    "hidden_test_cases",
)

#: What rollback is allowed to touch. Deliberately only the question.
#:
#: An oracle execution happened; an approval was made by a person. Undoing the
#: question does not un-happen either, and deleting them would destroy the
#: audit trail that explains why the question was changed in the first place.
#: They are marked superseded by an append-only `RemediationAction`, never
#: edited or removed.
ROLLBACK_SCOPE = ("groups_question",)


def alias_of(instance):
    """
    The database alias an instance came from.

    Every write below is routed with `.using(alias_of(batch))` rather than the
    default manager. Without it, `preimage_capture --alias preimage` creates
    the BATCH on the capture connection and the PRE-IMAGES on `default` — two
    different roles, and in the worst case two different databases, with the
    batch pointing at rows that are not there. Found by running the real
    command against the real capture role, which could not read
    `groups_question` from the alias it was actually using.
    """
    return instance._state.db or DEFAULT_DB_ALIAS


class PreImageError(Exception):
    """Capture or restore could not proceed safely."""


class CaptureIncomplete(PreImageError):
    """A batch was asked to modify something it had not captured."""


class DigestMismatch(PreImageError):
    """Stored bytes do not match their recorded digest, or live state moved."""


# ── Digesting ───────────────────────────────────────────────────────────────

def question_state(question):
    """The captured fields of a question, as a plain dict."""
    return {name: getattr(question, name) for name in CAPTURED_FIELDS}


def suite_case_identities(hidden_test_cases):
    """
    [(case_digest, input_digest, expected_digest)] for a stored suite.

    Uses `provenance.case_identity` so a case has ONE identity across the
    pre-image, the oracle's execution records and the approved artifact. A
    malformed case still gets an identity — rollback must be able to restore
    broken data exactly as it found it, since "broken" is what 48 production
    questions actually are.
    """
    identities = []
    for case in (hidden_test_cases if isinstance(hidden_test_cases, list) else []):
        stdin = case.get("stdin") if isinstance(case, dict) else None
        expected = case.get("expected_output") if isinstance(case, dict) else None
        stdin_text = stdin if isinstance(stdin, str) else _repr_for_identity(stdin)
        expected_text = (expected if isinstance(expected, str)
                         else _repr_for_identity(expected))
        identities.append((
            provenance.case_identity(stdin_text),
            provenance.input_identity(stdin_text),
            provenance.output_identity(expected_text),
        ))
    return identities


def _repr_for_identity(value):
    """
    A stable text form for a NON-string stored field.

    `case_identity` expects text, and 48 production questions store a list
    where text belongs. Refusing to digest them would make exactly the
    questions most in need of repair the ones that cannot be captured.
    """
    import json
    if value is None:
        return ""
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), default=str)


def state_digest(question_id, state):
    """
    Deterministic digest of a captured state.

    Length-prefixed frames from `question_artifact` — one canonical encoding in
    the system. The schema version is emitted FIRST so a digest computed under
    one field set can never collide with one computed under another.
    """
    frames = [_frame("pre_image_schema", PRE_IMAGE_SCHEMA_VERSION),
              _frame("question_id", question_id)]
    for name in CAPTURED_FIELDS:
        frames.append(_frame(name, state.get(name)))

    # Case identities are digested too, so a suite edit that somehow preserved
    # the JSON encoding would still move the digest.
    identities = suite_case_identities(state.get("hidden_test_cases"))
    frames.append(_frame("case_count", len(identities)))
    for case_digest, input_digest, expected_digest in identities:
        frames.append(_frame("case", case_digest))
        frames.append(_frame("case_input", input_digest))
        frames.append(_frame("case_expected", expected_digest))
    return digest_of(frames)


def live_digest(question):
    """The digest of a question's CURRENT state, for divergence detection."""
    return state_digest(question.pk, question_state(question))


# ── Phase A: capture ────────────────────────────────────────────────────────

def capture(batch, question, actor):
    """
    Capture one question's complete prior state into `batch`.

    Refuses if the batch is frozen: membership is fixed at freeze time so a
    later remediation cannot quietly enlarge the set it is allowed to touch.
    Capturing the same question twice is idempotent — the FIRST pre-image
    wins and is returned unchanged, because that is the state rollback must
    return to. A second remediation must not be able to overwrite the record
    of what the first one found.
    """
    using = alias_of(batch)
    with transaction.atomic(using=using):
        return _capture(batch, question, actor, using)


def _capture(batch, question, actor, using):
    if batch.frozen_at is not None:
        raise PreImageError(
            f"batch {batch.batch_key} is frozen; membership cannot change")

    existing = QuestionPreImage.objects.using(using).filter(
        batch=batch, question=question).first()
    if existing is not None:
        return existing

    state = question_state(question)
    identities = suite_case_identities(state.get("hidden_test_cases"))

    return QuestionPreImage.objects.using(using).create(
        batch=batch,
        question=question,
        schema_version=PRE_IMAGE_SCHEMA_VERSION,
        content=state["content"] or "",
        status=state["status"],
        trust_state=state["trust_state"],
        execution_contract_version=state["execution_contract_version"] or "",
        boilerplate_code=state["boilerplate_code"] or {},
        hidden_wrapper_code=state["hidden_wrapper_code"] or {},
        hidden_test_cases=state["hidden_test_cases"] or [],
        case_identities=[
            {"case": c, "input": i, "expected": e} for c, i, e in identities],
        # Derived, not stored on Question — recorded so a restore can be
        # cross-checked against what the trust boundary computed at capture.
        was_adaptive_eligible=question.is_adaptive_eligible,
        state_digest=state_digest(question.pk, state),
        # FK by ID, not by object. `default` and `preimage` are two ROLES on
        # the SAME database, but Django's router models them as different
        # databases and refuses to relate objects across them. Assigning the id
        # states the same fact without asking the router's opinion — and it
        # means the capture role needs no read privilege on the user table.
        captured_by_id=actor.pk,
        captured_at=timezone.now(),
    )


def verify(pre_image):
    """
    Recompute a pre-image's digest from its own stored bytes.

    Catches corruption and any edit that bypassed the append-only guard. A
    pre-image that cannot verify is NOT a fallback — it is the absence of one.
    """
    recomputed = state_digest(pre_image.question_id, pre_image.captured_state())
    if recomputed != pre_image.state_digest:
        raise DigestMismatch(
            f"pre-image {pre_image.pk} for question {pre_image.question_id} "
            f"does not match its recorded digest "
            f"(stored {pre_image.state_digest[:12]}, "
            f"recomputed {recomputed[:12]}); refusing to treat it as a "
            f"restorable copy")
    return True


def freeze(batch, actor):
    """
    Close a batch to new members, after verifying every capture in it.

    The last point at which a batch can still be abandoned harmlessly. After
    this the set is fixed and Phase B may begin.
    """
    using = alias_of(batch)
    with transaction.atomic(using=using):
        return _freeze(batch, actor, using)


def _freeze(batch, actor, using):
    if batch.frozen_at is not None:
        raise PreImageError(f"batch {batch.batch_key} is already frozen")

    pre_images = list(batch.pre_images.using(using).all())
    if not pre_images:
        raise CaptureIncomplete(
            f"batch {batch.batch_key} has no captures; refusing to freeze an "
            f"empty batch, which would authorise modifying nothing while "
            f"looking like a completed capture phase")

    for pre_image in pre_images:
        verify(pre_image)

    batch.frozen_at = timezone.now()
    batch.frozen_by_id = actor.pk
    batch.state = RemediationBatch.STATE_CAPTURED
    batch.save(using=using, update_fields=["frozen_at", "frozen_by", "state"])
    return batch


# ── Phase B: the write-ahead guard ──────────────────────────────────────────

def require_pre_image(batch, question):
    """
    The write-ahead rule: no modification without a verified pre-image.

    Called by every remediation before it touches anything. Raises rather than
    returning a falsy value so a caller cannot proceed by forgetting to check
    the result.
    """
    if batch.frozen_at is None:
        raise CaptureIncomplete(
            f"batch {batch.batch_key} is not frozen; capture must complete "
            f"before any modification")

    pre_image = QuestionPreImage.objects.using(alias_of(batch)).filter(
        batch=batch, question=question).first()
    if pre_image is None:
        raise CaptureIncomplete(
            f"question {question.pk} has no pre-image in batch "
            f"{batch.batch_key}; refusing to modify state that cannot be "
            f"restored")

    verify(pre_image)
    return pre_image


def record_action(batch, question, action_class, actor, detail=""):
    """
    Append-only record of one applied remediation, with the resulting digest.

    The post-image digest is what rollback compares live state against: if the
    question has moved since the remediation, someone or something else edited
    it, and blind restoration would silently discard their change.
    """
    using = alias_of(batch)
    with transaction.atomic(using=using):
        return _record_action(batch, question, action_class, actor, detail, using)


def _record_action(batch, question, action_class, actor, detail, using):
    pre_image = require_pre_image(batch, question)
    question.refresh_from_db(using=using)
    return RemediationAction.objects.using(using).create(
        batch=batch,
        question=question,
        pre_image=pre_image,
        action_class=action_class,
        detail=detail,
        post_digest=live_digest(question),
        applied_by_id=actor.pk,
        applied_at=timezone.now(),
    )


# ── Rollback ────────────────────────────────────────────────────────────────

def differing_fields(record, question):
    """
    The captured fields whose live value differs from the pre-image.

    This is what a restore actually has to write. Writing the whole captured
    set instead was a real defect: it demanded UPDATE on all seven columns, and
    the least-privilege roles that make each repair safe hold exactly one each —
    so no connection in the system could perform a rollback, and nobody noticed
    because rollback had never run outside a test database owned by a role that
    holds everything.

    Restoring only what differs means a rollback needs precisely the privileges
    of the repairs it is undoing. The guarantee does not weaken: the WHOLE
    captured state is still re-read and digested afterwards.
    """
    captured = record.captured_state()
    live = question_state(question)
    return tuple(name for name in CAPTURED_FIELDS
                 if captured[name] != live[name])


class RollbackTarget:
    """One question's restore plan: what would be written, and what stops it."""

    __slots__ = ("record", "question", "fields", "live_digest", "diverged",
                 "corrupt")

    def __init__(self, record, question, fields, live, diverged, corrupt):
        self.record = record
        self.question = question
        self.fields = fields
        self.live_digest = live
        self.diverged = diverged
        self.corrupt = corrupt

    @property
    def already_restored(self):
        return not self.fields

    def __repr__(self):
        return (f"RollbackTarget(q{self.record.question_id}, "
                f"fields={self.fields!r}, diverged={self.diverged})")


def rollback_plan(batch, questions=None):
    """
    What a rollback would do, WITHOUT writing anything.

    Separated from `rollback` so the operator command can authorise the exact
    column writes before a transaction is opened: the gate needs to know which
    fields differ, and finding that out must not require the privilege to
    change them.
    """
    using = alias_of(batch)
    records = list(batch.pre_images.using(using).select_related("question"))
    if questions is not None:
        wanted = {q.pk if hasattr(q, "pk") else int(q) for q in questions}
        records = [record for record in records if record.question_id in wanted]
        missing = wanted - {record.question_id for record in records}
        if missing:
            raise CaptureIncomplete(
                f"no pre-image in batch {batch.batch_key} for question(s) "
                f"{sorted(missing)}")
    if not records:
        raise CaptureIncomplete(f"batch {batch.batch_key} has nothing to restore")

    targets = []
    for record in records:
        question = Question.objects.using(using).get(pk=record.question_id)
        corrupt = False
        try:
            verify(record)
        except DigestMismatch:
            corrupt = True
        live = live_digest(question)
        latest = (RemediationAction.objects.using(using)
                  .filter(batch=batch, question_id=question.pk)
                  .order_by("-applied_at", "-pk").first())
        diverged = latest is not None and live != latest.post_digest
        targets.append(RollbackTarget(
            record, question, differing_fields(record, question), live,
            diverged, corrupt))
    return targets


def required_column_writes(targets):
    """
    [(table, column, 'UPDATE')] for exactly the fields a restore would write.

    The privilege contract for a rollback is therefore derived from the data,
    never from a fixed list — undoing a statement repair needs `content`, and
    undoing a key repair needs `hidden_test_cases`, and neither needs the other.
    """
    fields = sorted({name for target in targets for name in target.fields})
    return tuple(("groups_question", name, "UPDATE") for name in fields)


def rollback(batch, actor, questions=None, allow_divergence=False):
    """
    Restore a batch — atomically, or not at all.

    Every pre-image is verified and every live state is checked for divergence
    BEFORE a single row is written. A partial restore would leave the bank in a
    state that never existed: some questions at their prior version, some at
    the remediated one, and no record of which is which.

    `questions` restores a subset (one question, or one action's question); the
    all-or-nothing rule applies to whatever subset was selected.

    `allow_divergence` is NOT a force flag for corruption — `verify` still
    raises regardless. It only permits restoring over a question that changed
    after the remediation, and it is recorded on the action so the override is
    visible afterwards.
    """
    using = alias_of(batch)
    # Alias-scoped, so the all-or-nothing guarantee holds on the connection the
    # writes actually go to. A bare @transaction.atomic would open the
    # transaction on `default` while the writes went elsewhere.
    with transaction.atomic(using=using):
        return _rollback(batch, actor, questions, allow_divergence, using)


def _rollback(batch, actor, questions, allow_divergence, using):
    if batch.frozen_at is None:
        raise CaptureIncomplete(
            f"batch {batch.batch_key} was never frozen; there is no captured "
            f"state to roll back to")

    pre_images = list(batch.pre_images.using(using).select_related("question"))
    if questions is not None:
        wanted = {q.pk if hasattr(q, "pk") else int(q) for q in questions}
        pre_images = [p for p in pre_images if p.question_id in wanted]
        missing = wanted - {p.question_id for p in pre_images}
        if missing:
            raise CaptureIncomplete(
                f"no pre-image in batch {batch.batch_key} for question(s) "
                f"{sorted(missing)}")

    if not pre_images:
        raise CaptureIncomplete(f"batch {batch.batch_key} has nothing to restore")

    # ── Verify EVERYTHING first. Nothing is written until every check passes.
    divergent = []
    for pre_image in pre_images:
        verify(pre_image)
        question = Question.objects.using(using).select_for_update().get(
            pk=pre_image.question_id)
        latest = (RemediationAction.objects.using(using)
                  .filter(batch=batch, question_id=question.pk)
                  .order_by("-applied_at", "-pk").first())
        if latest is not None and live_digest(question) != latest.post_digest:
            divergent.append(question.pk)

    if divergent and not allow_divergence:
        raise DigestMismatch(
            f"question(s) {sorted(divergent)} have changed since this batch "
            f"was applied; their current state is not what this batch "
            f"produced. Restoring would silently discard that change. Inspect "
            f"them, then re-run with allow_divergence=True if the pre-image "
            f"really is the state you want.")

    # ── Apply. One transaction, all or nothing.
    restored = []
    for pre_image in pre_images:
        question = Question.objects.using(using).select_for_update().get(
            pk=pre_image.question_id)
        fields = differing_fields(pre_image, question)

        if fields:
            for name in fields:
                setattr(question, name, pre_image.captured_state()[name])
            # ONLY what differs. See `differing_fields`: writing the whole
            # captured set made a rollback demand privileges no role holds.
            question.save(using=using, update_fields=list(fields))

        after = live_digest(question)
        if after != pre_image.state_digest:
            # Cannot happen if the encoding is sound — which is exactly why it
            # is checked. The digest covers the WHOLE captured state, so a
            # narrowed write is still proved to have reproduced all of it.
            # Raising inside the atomic block reverts every restore in this
            # batch.
            raise DigestMismatch(
                f"restore of question {question.pk} did not reproduce the "
                f"captured digest; the whole rollback has been reverted")
        restored.append(question.pk)

        # ONE action per question. A single record naming several questions
        # could carry only one post-digest — the first pre-image's — which
        # reads as a claim about all of them and is true of one.
        restored_fields = (", ".join(fields) if fields
                           else "nothing (already at the captured state)")
        detail = f"restored {restored_fields}"
        if question.pk in divergent:
            detail += "; divergence overridden"
        RemediationAction.objects.using(using).create(
            batch=batch,
            question=pre_image.question,
            pre_image=pre_image,
            action_class=RemediationAction.CLASS_ROLLBACK,
            detail=detail,
            post_digest=pre_image.state_digest,
            applied_by_id=actor.pk,
            applied_at=timezone.now(),
        )

    # The batch is only ROLLED_BACK when every member is back at its capture.
    # Flipping the label after restoring one question of seven said the batch
    # had been undone while nine of ten actions still stood — and no command
    # could set it back.
    outstanding = [record for record
                   in batch.pre_images.using(using).select_related("question")
                   if differing_fields(
                       record,
                       Question.objects.using(using).get(pk=record.question_id))]
    if not outstanding:
        batch.state = RemediationBatch.STATE_ROLLED_BACK
        batch.save(using=using, update_fields=["state"])
    return restored
