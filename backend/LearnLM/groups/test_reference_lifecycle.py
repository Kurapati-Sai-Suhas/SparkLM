"""
The reference-solution lifecycle (M2 P2.7d).

The invariant this file exists to prove:

    Nothing may act as grading truth until a human has approved it, and an
    approval refers to the exact source that was read.

Before this phase `ReferenceSolution` had one lifecycle field, `is_active`,
and it defaulted to True. A reference created by any tooling was therefore
canonical the instant it existed, and the oracle would have executed it as the
definition of the right answer without anybody having read a line. There was
no state in which a reference existed but was not yet trusted — which is the
same shape of defect as a question being servable and trusted at once, fixed
in P2.7c.

Two fields, deliberately independent:

    review_state  "has a human approved this implementation?"
    is_active     "is this the canonical reference selected for execution?"

APPROVED + inactive is a legitimate resting state — a reviewed implementation
that has been superseded, or approved in a language that is not this problem's
canonical oracle language. UNAPPROVED + active is not, and is unwritable.

Enforcement is at the DATABASE, not in `save()`. `save()` is bypassed by
`QuerySet.update()`, `bulk_update`, `loaddata` and raw SQL — every one of
which could otherwise rewrite an approved reference's source while leaving the
approval intact. That is silent corruption of grading truth, so PostgreSQL
recomputes the digest itself.
"""

import hashlib

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from groups import coding_views
from groups.conftest import approved_reference
from groups.models import (
    CodeSubmission, CodingPortal, Question, ReferenceSolution, Topic,
    compute_source_hash,
)
from groups.oracle import (
    OracleFailed, OracleNondeterministic, OracleService, OracleUnapproved,
    OracleUnavailable, canonical_reference, canonical_reference_problem,
)
from groups.serializers import CodeSubmitSerializer

User = get_user_model()

SOURCE = "print(input())"


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Lifecycle Portal")
    topic, _ = Topic.objects.get_or_create(
        name="LifecycleTopic", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        title="Lifecycle Problem", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "class Solution: pass"}, hidden_wrapper_code={})


@pytest.fixture
def approver(db):
    return User.objects.create_user(
        username="reviewer", password="Review#2026x", email="rev@t.com")


def draft(question, source_code=SOURCE, language="python"):
    """A brand-new reference — no transitions applied."""
    return ReferenceSolution.objects.create(
        question=question, language=language, source_code=source_code)


def accepting(stdout):
    """A runner that always succeeds, recording every call it receives."""
    calls = []

    def runner(source, language, stdin):
        calls.append((language, stdin))
        value = stdout(stdin) if callable(stdout) else stdout
        return {"status": "Accepted", "status_id": 3, "stdout": value,
                "stderr": "", "compile_output": "", "time": "0.01", "memory": 1000}

    runner.calls = calls
    return runner


# ═════════════════════════════════════════════════════════════
# A. Default lifecycle — nothing is trusted by omission
# ═════════════════════════════════════════════════════════════

def test_a_new_reference_is_draft(question):
    assert draft(question).review_state == ReferenceSolution.REVIEW_DRAFT


def test_a_new_reference_is_not_active(question):
    """
    The default flipped from True in P2.7d. This is the single line that
    decided whether every historical reference was implicitly canonical.
    """
    assert draft(question).is_active is False


def test_a_new_reference_carries_no_approval_metadata(question):
    reference = draft(question)

    assert reference.approved_by is None
    assert reference.approved_at is None
    assert reference.source_hash is None


def test_a_new_reference_is_neither_provenanced_nor_canonical(question):
    reference = draft(question)

    assert reference.has_valid_approval_provenance is False
    assert reference.is_canonical is False


def test_bulk_create_also_gets_the_safe_defaults(question):
    """`bulk_create` bypasses save() and signals — the likeliest escape path."""
    ReferenceSolution.objects.bulk_create([
        ReferenceSolution(question=question, language=lang, source_code="x")
        for lang in ("python", "cpp", "java")
    ])

    assert ReferenceSolution.objects.filter(
        review_state=ReferenceSolution.REVIEW_DRAFT).count() == 3
    assert ReferenceSolution.objects.filter(is_active=True).count() == 0


# ═════════════════════════════════════════════════════════════
# B. Valid transitions
# ═════════════════════════════════════════════════════════════

def test_draft_moves_to_in_review(question):
    reference = draft(question)

    reference.submit_for_review()

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_IN_REVIEW


def test_in_review_moves_to_approved(question, approver):
    reference = draft(question)
    reference.submit_for_review()

    reference.approve(by=approver)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED


def test_in_review_moves_to_rejected(question):
    reference = draft(question)
    reference.submit_for_review()

    reference.reject()

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_REJECTED


def test_a_draft_cannot_be_approved_without_review(question, approver):
    """Skipping review is the whole thing this lifecycle exists to prevent."""
    with pytest.raises(ValidationError):
        draft(question).approve(by=approver)


def test_an_approved_reference_cannot_be_approved_again(question, approver):
    reference = approved_reference(question, active=False, approver=approver)

    with pytest.raises(ValidationError):
        reference.approve(by=approver)


def test_a_rejected_reference_cannot_be_approved(question, approver):
    reference = draft(question)
    reference.submit_for_review()
    reference.reject()

    with pytest.raises(ValidationError):
        reference.approve(by=approver)


def test_there_is_no_transition_back_out_of_rejected(question):
    """
    Documented decision, asserted so it stays a decision rather than drifting
    into an accident. Reopening a rejected reference is only useful in order
    to edit its source, and this model's contract is that references are
    superseded, not edited — losing which version produced the stored expected
    outputs makes a later mismatch impossible to explain. The sanctioned path
    is a new reference; the rejected row remains the record of why the old one
    was not used.
    """
    assert not hasattr(ReferenceSolution, "reopen")
    assert not hasattr(ReferenceSolution, "return_to_draft")


def test_approval_requires_a_persisted_approver(question):
    reference = draft(question)
    reference.submit_for_review()

    with pytest.raises(ValidationError):
        reference.approve(by=None)
    with pytest.raises(ValidationError):
        reference.approve(by=User(username="ghost"))


# ═════════════════════════════════════════════════════════════
# C. Invalid active states — refused by the database
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state", [
    ReferenceSolution.REVIEW_DRAFT,
    ReferenceSolution.REVIEW_IN_REVIEW,
    ReferenceSolution.REVIEW_REJECTED,
])
def test_an_unapproved_reference_cannot_be_active(question, state):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.create(
                question=question, language="python", source_code=SOURCE,
                review_state=state, is_active=True)


@pytest.mark.parametrize("state", [
    ReferenceSolution.REVIEW_DRAFT,
    ReferenceSolution.REVIEW_IN_REVIEW,
    ReferenceSolution.REVIEW_REJECTED,
])
def test_an_unapproved_reference_cannot_be_activated_by_update(question, state):
    """`QuerySet.update()` bypasses save(); the constraint does not."""
    reference = draft(question)
    ReferenceSolution.objects.filter(pk=reference.pk).update(review_state=state)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.filter(pk=reference.pk).update(is_active=True)


def test_an_approved_reference_may_be_active(question, approver):
    """Positive control — the constraint is not simply blocking everything."""
    reference = approved_reference(question, approver=approver)

    reference.refresh_from_db()
    assert reference.is_active is True
    assert reference.is_canonical is True


def test_an_approved_reference_may_also_be_inactive(question, approver):
    """
    APPROVED + is_active=False is legitimate, not a half-finished state: a
    superseded implementation, or one approved in a non-canonical language.
    """
    reference = approved_reference(question, active=False, approver=approver)

    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.is_active is False
    assert reference.has_valid_approval_provenance is True
    assert reference.is_canonical is False


# ═════════════════════════════════════════════════════════════
# D. Approval provenance
# ═════════════════════════════════════════════════════════════

def test_approving_stamps_who_when_and_what(question, approver):
    before = timezone.now()
    reference = approved_reference(question, approver=approver, active=False)

    assert reference.approved_by == approver
    assert reference.approved_at >= before
    assert reference.source_hash == compute_source_hash(SOURCE)


@pytest.mark.parametrize("omit", ["approved_by", "approved_at", "source_hash"])
def test_approved_without_full_provenance_is_refused(question, approver, omit):
    fields = {
        "approved_by": approver,
        "approved_at": timezone.now(),
        "source_hash": compute_source_hash(SOURCE),
    }
    fields[omit] = None

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.create(
                question=question, language="python", source_code=SOURCE,
                review_state=ReferenceSolution.REVIEW_APPROVED, **fields)


@pytest.mark.parametrize("state", [
    ReferenceSolution.REVIEW_DRAFT,
    ReferenceSolution.REVIEW_IN_REVIEW,
    ReferenceSolution.REVIEW_REJECTED,
])
@pytest.mark.parametrize("field", ["approved_by", "approved_at", "source_hash"])
def test_a_non_approved_reference_cannot_hold_approval_metadata(
        question, approver, state, field):
    """
    The symmetric half. Without it, rejecting a reference would leave behind
    "approved by Alice at 14:02" on a row nobody approved.
    """
    value = {"approved_by": approver, "approved_at": timezone.now(),
             "source_hash": compute_source_hash(SOURCE)}[field]

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.create(
                question=question, language="python", source_code=SOURCE,
                review_state=state, **{field: value})


def test_approval_metadata_is_readable_back(question, approver):
    """Positive control for the pair above."""
    reference = approved_reference(question, approver=approver, active=False)

    stored = ReferenceSolution.objects.get(pk=reference.pk)
    assert stored.approved_by_id == approver.pk
    assert stored.approved_at is not None
    assert stored.source_hash is not None


# ═════════════════════════════════════════════════════════════
# E. Activation
# ═════════════════════════════════════════════════════════════

def test_activate_refuses_a_draft(question):
    with pytest.raises(ValidationError):
        draft(question).activate()


def test_activate_refuses_a_rejected_reference(question):
    reference = draft(question)
    reference.submit_for_review()
    reference.reject()

    with pytest.raises(ValidationError):
        reference.activate()


def test_activate_accepts_an_approved_reference(question, approver):
    reference = approved_reference(question, active=False, approver=approver)

    reference.activate()

    reference.refresh_from_db()
    assert reference.is_active is True


def test_approving_does_not_activate(question, approver):
    """
    Two decisions, made separately: "is this implementation correct?" and
    "is this the oracle we run?". A problem may have several approved
    implementations and only one canonical one.
    """
    reference = draft(question)
    reference.submit_for_review()
    reference.approve(by=approver)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.is_active is False


def test_one_active_reference_per_language_still_holds(question, approver):
    """The pre-existing uniqueness guarantee, unweakened by the new states."""
    approved_reference(question, language="python", source_code="a",
                       approver=approver)
    second = approved_reference(question, language="python", source_code="b",
                                active=False, approver=approver)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            second.activate()


def test_two_languages_may_both_be_active(question, approver):
    """Positive control — the constraint is per language, not per question."""
    approved_reference(question, language="python", source_code="p",
                       approver=approver)
    approved_reference(question, language="cpp", source_code="int main(){}",
                       approver=approver)

    assert ReferenceSolution.objects.filter(
        question=question, is_active=True).count() == 2


def test_superseding_deactivates_rather_than_editing(question, approver):
    old = approved_reference(question, source_code="old", approver=approver)
    old.deactivate()
    new = approved_reference(question, source_code="new", approver=approver)

    old.refresh_from_db()
    assert old.source_code == "old", "history was edited instead of superseded"
    assert old.is_active is False
    assert old.review_state == ReferenceSolution.REVIEW_APPROVED
    assert new.is_active is True


# ═════════════════════════════════════════════════════════════
# F. Source hash
# ═════════════════════════════════════════════════════════════

def test_the_hash_is_deterministic():
    assert compute_source_hash(SOURCE) == compute_source_hash(SOURCE)


def test_the_hash_is_sha256_of_the_utf8_source():
    """Pinned explicitly: the database constraint computes the same digest."""
    assert compute_source_hash(SOURCE) == hashlib.sha256(
        SOURCE.encode("utf-8")).hexdigest()


def test_the_hash_changes_when_the_source_changes():
    assert compute_source_hash("a") != compute_source_hash("b")


def test_the_hash_ignores_nothing_about_the_source():
    """Whitespace is part of the program; a reformat is a different source."""
    assert compute_source_hash("x = 1") != compute_source_hash("x  =  1")


#: Source shapes a real reference implementation actually has. Every previous
#: test used whitespace-clean source, which let a `compute_source_hash(
#: source.strip())` mutant survive — and real files end with a newline, so the
#: stripped case was the common one, not the exotic one.
REALISTIC_SOURCES = [
    ("normal", "class Solution:\n    def solve(self, n):\n        return n"),
    ("trailing newline", "class Solution:\n    pass\n"),
    ("leading blank line", "\nclass Solution:\n    pass"),
    ("CRLF", "class Solution:\r\n    pass\r\n"),
    ("trailing spaces", "class Solution:    \n    pass   "),
    ("tabs", "class Solution:\n\tpass\n"),
    ("unicode identifiers", "# \u00e9\u00e0\u4e2d\u6587\nclass Solution:\n    pass\n"),
    ("emoji", "# \U0001f600\nclass Solution:\n    pass\n"),
    ("empty string", ""),
    ("quotes", "s = \"it's\" + 'a \"quote\"'\n"),
    ("backslashes", "p = 'C:\\\\tmp\\\\x'\n"),
    ("byte literals", "b = b'\\x01\\x02'\n"),
]


@pytest.mark.parametrize("label,source",
                         REALISTIC_SOURCES, ids=[s[0] for s in REALISTIC_SOURCES])
def test_python_and_postgres_compute_the_same_digest(db, label, source):
    """
    The two implementations of this hash must agree byte for byte.

    `compute_source_hash` runs in Python; the
    `reference_approved_source_unmodified` constraint runs
    `encode(sha256(convert_to(source_code,'UTF8')),'hex')` inside PostgreSQL.
    If they ever diverge, approving a reference becomes impossible — the
    constraint rejects a digest the model just computed.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT encode(sha256(convert_to(%s, 'UTF8')), 'hex')", [source])
        postgres_digest = cursor.fetchone()[0]

    assert compute_source_hash(source) == postgres_digest, (
        f"Python and PostgreSQL disagree on the digest of {label!r} source")


@pytest.mark.parametrize("label,source",
                         REALISTIC_SOURCES, ids=[s[0] for s in REALISTIC_SOURCES])
def test_realistic_sources_can_actually_be_approved(question, approver,
                                                    label, source):
    """
    Agreement is necessary but not sufficient — the end-to-end check is that
    the row reaches APPROVED + ACTIVE without the constraint rejecting it.
    """
    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=source)
    reference.submit_for_review()
    reference.approve(by=approver)
    reference.activate()

    reference.refresh_from_db()
    assert reference.is_canonical is True, f"{label!r} source could not be approved"
    assert reference.source_code == source, "the stored source was altered"


def test_an_approved_source_cannot_be_rewritten_by_update(question, approver):
    """
    The reason this is a database constraint. `QuerySet.update()` never calls
    save(), so a Python-side guard would not see this write at all.
    """
    reference = approved_reference(question, approver=approver)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.filter(pk=reference.pk).update(
                source_code="a completely different implementation")


def test_an_approved_source_cannot_be_rewritten_by_save(question, approver):
    reference = approved_reference(question, approver=approver)
    reference.source_code = "a completely different implementation"

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            reference.save(update_fields=["source_code"])


def test_a_draft_source_may_still_be_edited(question):
    """
    Positive control. Nothing is frozen before approval — there is no
    provenance yet to invalidate, and forbidding it would make authoring
    impossible.
    """
    reference = draft(question)
    reference.source_code = "revised while still a draft"
    reference.save()

    reference.refresh_from_db()
    assert reference.source_code == "revised while still a draft"


def test_in_memory_source_mutation_invalidates_provenance(question, approver):
    """
    The database cannot see an unsaved attribute assignment — but the oracle
    receives model INSTANCES, not rows, so this is a real path and the
    derived property must close it.
    """
    reference = approved_reference(question, approver=approver)
    assert reference.has_valid_approval_provenance is True

    reference.source_code = "swapped in memory only"

    assert reference.has_valid_approval_provenance is False
    assert reference.is_canonical is False


# ═════════════════════════════════════════════════════════════
# G. Oracle boundary
# ═════════════════════════════════════════════════════════════

def test_the_oracle_refuses_an_unapproved_reference(question):
    reference = draft(question)
    runner = accepting("1")

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run(question, reference, "1")

    assert runner.calls == [], "an unapproved reference reached execution"


def test_the_oracle_refuses_an_inactive_reference(question, approver):
    reference = approved_reference(question, active=False, approver=approver)
    runner = accepting("1")

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run(question, reference, "1")

    assert runner.calls == []


def test_the_oracle_refuses_a_reference_mutated_after_approval(question, approver):
    reference = approved_reference(question, approver=approver)
    reference.source_code = "swapped after approval"
    runner = accepting("1")

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run(question, reference, "1")

    assert runner.calls == []


def test_the_oracle_runs_a_canonical_reference(question, approver):
    """Positive control — the guard is a gate, not a wall."""
    reference = approved_reference(question, approver=approver)
    runner = accepting("42")

    assert OracleService(runner).run(question, reference, "1") == "42"
    assert len(runner.calls) == 2, "determinism verification should run twice"


def test_run_many_refuses_a_reference_that_is_never_canonical(question, approver):
    reference = approved_reference(question, active=False, approver=approver)
    runner = accepting("1")

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run_many(question, reference, ["1", "2", "3"])

    assert runner.calls == []


def test_run_many_re_checks_the_reference_between_inputs(question, approver):
    """
    The gate is per INPUT, not per batch.

    An earlier version of this test passed an already-inactive reference, so
    the first input raised and inputs 2 and 3 were never reached — it proved
    the first call was gated and could not distinguish that from a guard
    hoisted out of the loop. Mutation testing caught it: moving the check to
    "once per batch" left the test green.

    This version makes the reference stop being canonical PART-WAY THROUGH the
    batch — an operator deactivating it while a long reconciliation runs, which
    is a real race on a suite of twelve inputs against a single-worker Judge0.
    Input 1 must complete and input 2 must be refused, which is only possible
    if the guard runs again between them.
    """
    reference = approved_reference(question, approver=approver)
    seen = []

    def runner(source, language, stdin):
        seen.append(stdin)
        # Input 1 is verified for determinism, so it costs two calls. After the
        # second, the reference stops being canonical.
        if len(seen) == 2:
            reference.is_active = False
        return {"status": "Accepted", "status_id": 3, "stdout": "out",
                "stderr": "", "compile_output": "", "time": "0.01", "memory": 1}

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run_many(question, reference, ["a", "b", "c"])

    assert seen == ["a", "a"], (
        f"expected only input 'a' to execute (twice, for determinism); the "
        f"runner saw {seen}. Inputs 'b' and 'c' reaching it means the "
        f"reference was checked once for the whole batch."
    )


def test_run_many_executes_every_input_while_the_reference_stays_canonical(
        question, approver):
    """Positive control for the test above — the gate is not blocking the batch."""
    reference = approved_reference(question, approver=approver)
    runner = accepting("out")

    pairs = OracleService(runner).run_many(question, reference, ["a", "b", "c"])

    assert [stdin for stdin, _ in pairs] == ["a", "b", "c"]
    assert runner.calls == [("python", s) for s in ("a", "a", "b", "b", "c", "c")]


# ── Ownership: a reference may only answer for its own question ──────────

def test_the_oracle_refuses_a_reference_from_another_question(question, approver):
    """
    F1. `canonical_reference(question)` reads the related manager and cannot
    return a foreign row, but `OracleService.run` is public API and does not
    require that caller. Question A's wrapper around question B's approved
    reference produces a well-formed, authoritative-looking answer — the
    confidently-wrong answer key this milestone exists to prevent.
    """
    other = Question.objects.create(
        title="Other Problem", content="c", topic=question.topic,
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "class Solution: pass"}, hidden_wrapper_code={})
    foreign = approved_reference(other, source_code="print('other')",
                                 approver=approver)
    runner = accepting("output-from-the-wrong-problem")

    with pytest.raises(OracleUnapproved) as exc:
        OracleService(runner).run(question, foreign, "1")

    assert runner.calls == [], "a foreign reference reached execution"
    assert str(other.pk) in str(exc.value) and str(question.pk) in str(exc.value)


def test_run_many_refuses_a_reference_from_another_question(question, approver):
    other = Question.objects.create(
        title="Other Problem 2", content="c", topic=question.topic,
        base_difficulty=1200.0, hidden_test_cases=[],
        boilerplate_code={}, hidden_wrapper_code={})
    foreign = approved_reference(other, source_code="print('other')",
                                 approver=approver)
    runner = accepting("x")

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run_many(question, foreign, ["1", "2", "3"])

    assert runner.calls == []


def test_ownership_is_checked_before_canonicality(question, approver):
    """
    Order matters for the operator reading the report. A foreign reference that
    is ALSO a draft must be reported as the wrong problem's reference — telling
    them to get it approved would send them to fix the wrong thing.
    """
    other = Question.objects.create(
        title="Other Problem 3", content="c", topic=question.topic,
        base_difficulty=1200.0, hidden_test_cases=[],
        boilerplate_code={}, hidden_wrapper_code={})
    foreign_draft = draft(other)
    runner = accepting("x")

    with pytest.raises(OracleUnapproved) as exc:
        OracleService(runner).run(question, foreign_draft, "1")

    assert "belongs to question" in str(exc.value)
    assert runner.calls == []


def test_a_reference_still_answers_for_its_own_question(question, approver):
    """Positive control — ownership is a gate, not a wall."""
    reference = approved_reference(question, approver=approver)
    runner = accepting("42")

    assert OracleService(runner).run(question, reference, "1") == "42"
    assert len(runner.calls) == 2


def test_an_unapproved_reference_is_not_canonical(question):
    draft(question)

    assert canonical_reference(question) is None
    assert "DRAFT" in canonical_reference_problem(question)


def test_ambiguous_active_references_are_still_refused(question, approver):
    approved_reference(question, language="python", approver=approver)
    approved_reference(question, language="cpp", source_code="int main(){}",
                       approver=approver)

    assert canonical_reference(question) is None
    assert "exactly one canonical oracle" in canonical_reference_problem(question)


def test_a_single_canonical_reference_is_selected(question, approver):
    """Positive control for the two refusals above."""
    approved_reference(question, language="python", approver=approver)

    assert canonical_reference(question).language == "python"
    assert canonical_reference_problem(question) is None


@pytest.mark.parametrize("verdict,expected", [
    ({"status": "Compilation Error", "status_id": 6, "stdout": "",
      "compile_output": "error: expected ';'"}, OracleFailed),
    ({"status": "Runtime Error (NZEC)", "status_id": 11, "stdout": "",
      "stderr": "IndexError"}, OracleFailed),
    ({"status": "Time Limit Exceeded", "status_id": 5, "stdout": ""}, OracleFailed),
    ({"error": "judge0 unreachable"}, OracleUnavailable),
])
def test_a_broken_reference_still_raises_rather_than_returning(
        question, approver, verdict, expected):
    """
    P2.7d must not have loosened any pre-existing refusal while adding a new
    one. Every failure mode still raises.
    """
    reference = approved_reference(question, approver=approver)

    with pytest.raises(expected):
        OracleService(lambda *a: verdict).run(question, reference, "1")


def test_nondeterministic_output_still_raises(question, approver):
    reference = approved_reference(question, approver=approver)
    outputs = iter(["1", "2"])

    def runner(source, language, stdin):
        return {"status": "Accepted", "status_id": 3, "stdout": next(outputs),
                "stderr": "", "compile_output": "", "time": "0.01", "memory": 1}

    with pytest.raises(OracleNondeterministic):
        OracleService(runner).run(question, reference, "1")


def test_a_refused_oracle_never_falls_back_to_the_stored_expected_output(
        question, approver):
    """
    The failure mode this whole milestone exists to prevent. `expected_output`
    for this question is "1"; a refusal must propagate, never quietly return
    the value we were trying to verify.
    """
    stored = "STORED_ANSWER_KEY_6104"
    question.hidden_test_cases = [{"stdin": "1", "expected_output": stored}]
    question.save(update_fields=["hidden_test_cases"])
    reference = draft(question)
    runner = accepting(stored)

    with pytest.raises(OracleUnapproved) as exc:
        OracleService(runner).run(question, reference, "1")

    assert stored not in str(exc.value)
    assert runner.calls == []


def test_determinism_verification_defaults_to_on(question, approver):
    """
    P2.7d pins the default so that a future trust-promotion caller which
    simply omits the argument is correct by construction. A caller that
    passes False explicitly is making a claim P2.7g must refuse — that
    enforcement belongs to P2.7g, which has no code yet to constrain.
    """
    import inspect

    signature = inspect.signature(OracleService.run)
    assert signature.parameters["verify_determinism"].default is True

    signature = inspect.signature(OracleService.run_many)
    assert signature.parameters["verify_determinism"].default is True


# ═════════════════════════════════════════════════════════════
# H. Existing security guarantees are unchanged
# ═════════════════════════════════════════════════════════════

def test_the_submit_serializer_is_unchanged(question):
    assert set(CodeSubmitSerializer().fields) == {"problem_id", "code", "language"}


@pytest.mark.parametrize("field,value", [
    ("review_state", "APPROVED"),
    ("is_active", True),
    ("approved_by", 1),
    ("source_hash", "0" * 64),
])
def test_a_learner_cannot_touch_the_lifecycle_through_submit(
        question, approver, monkeypatch, field, value):
    learner = User.objects.create_user(
        username="lifecycle-learner", password="Learn#2026x", email="ll@t.com")
    reference = approved_reference(question, source_code="untouched",
                                   active=False, approver=approver)
    monkeypatch.setattr(coding_views, "_run_on_judge0", accepting("1"))

    from rest_framework.test import APIClient
    from django.urls import reverse
    client = APIClient()
    client.force_authenticate(user=learner)
    client.post(reverse("code-submit"), {
        "problem_id": question.pk, "code": "print(1)", "language": "python",
        field: value,
    }, format="json")

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.is_active is False
    assert reference.source_code == "untouched"
    assert reference.approved_by_id == approver.pk


def test_submitting_still_works(question, approver, monkeypatch):
    """Positive control: the assertions above are not passing because the
    request 400s before reaching anything."""
    learner = User.objects.create_user(
        username="ok-learner", password="Learn#2026x", email="ok@t.com")
    monkeypatch.setattr(coding_views, "_run_on_judge0", accepting("1"))

    from rest_framework.test import APIClient
    from django.urls import reverse
    client = APIClient()
    client.force_authenticate(user=learner)
    response = client.post(reverse("code-submit"), {
        "problem_id": question.pk, "code": "print(1)", "language": "python",
    }, format="json")

    assert response.status_code == 200
    assert CodeSubmission.objects.filter(user=learner).count() == 1


def test_the_new_fields_are_not_serialised_anywhere(question):
    """
    `test_reference_solution_secrecy` proves ReferenceSolution has no
    serializer at all. This narrower check pins the specific risk P2.7d
    introduces: an approver's identity is user data attached to grading truth.
    """
    import inspect
    from rest_framework import serializers as drf

    from groups import serializers as groups_serializers

    for _, obj in inspect.getmembers(groups_serializers, inspect.isclass):
        if not issubclass(obj, drf.BaseSerializer):
            continue
        declared = set(getattr(obj, "_declared_fields", {}))
        meta_fields = set(getattr(getattr(obj, "Meta", None), "fields", None) or [])
        assert not ({"review_state", "approved_by", "approved_at", "source_hash"}
                    & (declared | meta_fields)), (
            f"{obj.__name__} exposes reference-lifecycle fields")


def test_reference_solution_is_still_absent_from_admin():
    from django.contrib import admin

    assert ReferenceSolution not in admin.site._registry


# ═════════════════════════════════════════════════════════════
# J. Test-infrastructure integrity
# ═════════════════════════════════════════════════════════════

def test_the_shared_factory_walks_the_real_lifecycle(question, approver):
    """
    F4. Protects the tests, not production.

    `approved_reference()` is used by every suite that needs a usable oracle.
    Mutation testing showed it could be replaced with a single
    `objects.create(review_state=APPROVED, is_active=True, ...)` and all 174
    tests would still pass — after which nothing in the repository would be
    exercising the transitions at all, while appearing to.

    Watching `post_save` is the evidence, because it records the sequence of
    states the row actually passed through rather than the state it ended in.
    A shortcut produces one INSERT; the real lifecycle produces four writes.
    """
    from django.db.models.signals import post_save

    observed = []

    def spy(sender, instance, created, **kwargs):
        observed.append((instance.review_state, instance.is_active, created))

    post_save.connect(spy, sender=ReferenceSolution,
                      dispatch_uid="p27d-factory-integrity")
    try:
        reference = approved_reference(question, approver=approver)
    finally:
        post_save.disconnect(sender=ReferenceSolution,
                             dispatch_uid="p27d-factory-integrity")

    assert observed == [
        (ReferenceSolution.REVIEW_DRAFT, False, True),       # created
        (ReferenceSolution.REVIEW_IN_REVIEW, False, False),  # submit_for_review
        (ReferenceSolution.REVIEW_APPROVED, False, False),   # approve
        (ReferenceSolution.REVIEW_APPROVED, True, False),    # activate
    ], (
        f"the factory did not walk DRAFT -> IN_REVIEW -> APPROVED -> ACTIVE; "
        f"observed {observed}"
    )

    # Approval metadata was produced BY approve(), not handed to create().
    assert reference.approved_by == approver
    assert reference.approved_at >= reference.created_at
    assert reference.source_hash == compute_source_hash(reference.source_code)


def test_the_shared_factory_can_stop_before_activation(question, approver):
    """The `active=False` branch must walk the same path minus the last step."""
    from django.db.models.signals import post_save

    observed = []

    def spy(sender, instance, created, **kwargs):
        observed.append((instance.review_state, instance.is_active, created))

    post_save.connect(spy, sender=ReferenceSolution,
                      dispatch_uid="p27d-factory-integrity-inactive")
    try:
        approved_reference(question, active=False, approver=approver)
    finally:
        post_save.disconnect(sender=ReferenceSolution,
                             dispatch_uid="p27d-factory-integrity-inactive")

    assert observed == [
        (ReferenceSolution.REVIEW_DRAFT, False, True),
        (ReferenceSolution.REVIEW_IN_REVIEW, False, False),
        (ReferenceSolution.REVIEW_APPROVED, False, False),
    ], f"observed {observed}"


# ═════════════════════════════════════════════════════════════
# I. Migration
# ═════════════════════════════════════════════════════════════

def test_the_schema_matches_the_models(db):
    """`makemigrations --check` as a test, so drift fails in CI, not on deploy."""
    from io import StringIO

    from django.core.management import call_command

    call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())


MIGRATION_0039 = "0039_reference_solution_lifecycle.py"


def _migration_code():
    """
    The migration's EXECUTABLE source — comments and docstrings removed.

    Prose is not behaviour. This file's own docstring says the migration never
    calls Judge0, and a raw text search would read that sentence as evidence
    of the thing it denies.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parent / "migrations" / MIGRATION_0039
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        # `dependencies` names the PREVIOUS migration, and 0038's filename
        # contains "adaptive_eligible". A pointer at an earlier migration is
        # not a write, so it must not read as one.
        node.body = [
            statement for statement in body
            if not (isinstance(statement, ast.Assign)
                    and any(getattr(t, "id", None) == "dependencies"
                            for t in statement.targets))
        ] or [ast.Pass()]

    return ast.unparse(tree)


@pytest.mark.parametrize("forbidden", [
    "Question.objects", "CodeSubmission.objects", "hidden_test_cases",
    "expected_output", "trust_state", "adaptive_eligible",
    "judge0", "Judge0",
])
def test_the_migration_touches_no_grading_data(forbidden):
    """
    P2.7d changes SHAPE. Its one data operation DEACTIVATES; it must never
    reach Question, CodeSubmission, hidden tests or expected outputs.

    Asserted against the migration's own text because a migration is applied
    once, in an environment nobody is watching — there is no later moment at
    which its behaviour can be observed and corrected.
    """
    assert forbidden not in _migration_code(), (
        f"migration 0039 references {forbidden!r}")


def test_the_migration_never_approves_or_activates():
    """
    The one data operation must only ever demote. Positive control included:
    the demotion is asserted PRESENT, so this test cannot pass by the
    operation having been deleted.
    """
    demotion = _migration_code().split("class Migration")[0]

    assert "is_active=False" in demotion, "the safety demotion is missing"
    assert "is_active=True" not in demotion.replace(
        "filter(is_active=True)", ""), "migration 0039 activates a reference"
    assert "review_state=" not in demotion, "migration 0039 writes a review state"
    assert "approved_by=" not in demotion, "migration 0039 writes provenance"


def test_the_migration_creates_no_reference_rows(db):
    """
    The test database is built by replaying every migration. If 0039 created
    or activated anything, it would be visible here.
    """
    assert ReferenceSolution.objects.count() == 0
