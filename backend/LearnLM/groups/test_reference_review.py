"""
The reference approval interface (M2 P2.7d-2).

P2.7d built the lifecycle and its database constraints but exposed it only as
model methods, so no human could walk it — which meant no reference could ever
be approved and every phase downstream of grading truth was unreachable. This
suite covers the operator workflow that closes that gap, and the security
properties it must not cost.

Two invariants above all:

    Approving is a HUMAN act by a named, authorised, accountable account.
    Reference source code never acquires an HTTP surface.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.urls import reverse
from rest_framework.test import APIClient

from groups.models import (
    CodingPortal, Question, ReferenceSolution, Topic, compute_source_hash,
)

User = get_user_model()

SECRET = "SECRET_REFERENCE_BODY_7741"
SOURCE = f"# {SECRET}\nclass Solution:\n    def solve(self, n):\n        return n\n"


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Review Portal")


@pytest.fixture
def question(portal):
    topic = Topic.objects.create(name="ReviewTopic", structure_type="flat",
                                 portal=portal)
    return Question.objects.create(
        title="Review Problem", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "x"}, hidden_wrapper_code={})


@pytest.fixture
def operator(db):
    return User.objects.create_user(
        username="operator", email="op@t.com", password="Op#2026xyz",
        is_staff=True)


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="plain-learner", email="pl@t.com", password="Pl#2026xyz")


@pytest.fixture
def reference(question):
    return ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOURCE)


def run(*args, **kwargs):
    call_command("reference_review", *args, **kwargs)


def out(capsys):
    return capsys.readouterr().out


# ═════════════════════════════════════════════════════════════
# Authorisation
# ═════════════════════════════════════════════════════════════

def test_a_non_staff_user_cannot_approve(reference, learner):
    reference.submit_for_review()

    with pytest.raises(CommandError, match="not staff"):
        run("approve", reference.pk, operator="plain-learner", confirm=True)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_IN_REVIEW


def test_a_staff_operator_can_approve(reference, operator):
    """Positive control — the guard is a gate, not a wall."""
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.approved_by == operator


def test_a_disabled_staff_account_cannot_approve(reference, operator):
    """A revoked operator must lose the ability to define grading truth."""
    reference.submit_for_review()
    operator.is_active = False
    operator.save(update_fields=["is_active"])

    with pytest.raises(CommandError, match="disabled"):
        run("approve", reference.pk, operator="operator", confirm=True)


def test_an_unknown_operator_is_refused(reference):
    reference.submit_for_review()

    with pytest.raises(CommandError, match="No user named"):
        run("approve", reference.pk, operator="ghost", confirm=True)


def test_approval_without_an_operator_is_refused(reference):
    """There is no anonymous path — provenance names an accountable account."""
    reference.submit_for_review()

    with pytest.raises(CommandError, match="requires --operator"):
        run("approve", reference.pk, confirm=True)


@pytest.mark.parametrize("action", ["submit", "approve", "reject",
                                    "activate", "deactivate", "inspect"])
def test_every_non_list_action_requires_an_operator(reference, action):
    with pytest.raises(CommandError, match="requires --operator"):
        run(action, reference.pk, confirm=True)


def test_inspection_requires_operator_authorisation(reference, learner):
    with pytest.raises(CommandError, match="not staff"):
        run("inspect", reference.pk, operator="plain-learner", show_source=True)


# ═════════════════════════════════════════════════════════════
# Lifecycle — the real methods, in the real order
# ═════════════════════════════════════════════════════════════

def test_a_draft_cannot_jump_straight_to_approved(reference, operator):
    """
    Skipping review is the thing the lifecycle exists to prevent, and the
    command must not offer a shortcut around it.
    """
    assert reference.review_state == ReferenceSolution.REVIEW_DRAFT

    with pytest.raises(CommandError, match="IN_REVIEW"):
        run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_DRAFT


def test_the_full_path_walks_every_state(reference, operator):
    run("submit", reference.pk, operator="operator")
    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_IN_REVIEW

    run("approve", reference.pk, operator="operator", confirm=True)
    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.is_active is False, "approval silently activated"

    run("activate", reference.pk, operator="operator")
    reference.refresh_from_db()
    assert reference.is_canonical is True


def test_approval_does_not_activate(reference, operator):
    """
    Two decisions, kept separate: "is this implementation correct?" and "is
    this the oracle we run?".
    """
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.is_active is False


def test_an_unapproved_reference_cannot_be_activated(reference, operator):
    with pytest.raises(CommandError, match="APPROVED"):
        run("activate", reference.pk, operator="operator")

    reference.refresh_from_db()
    assert reference.is_active is False


def test_an_approved_reference_cannot_be_approved_again(reference, operator):
    reference.submit_for_review()
    run("approve", reference.pk, operator="operator", confirm=True)

    with pytest.raises(CommandError, match="IN_REVIEW"):
        run("approve", reference.pk, operator="operator", confirm=True)


def test_a_rejected_reference_stays_terminal(reference, operator):
    reference.submit_for_review()
    run("reject", reference.pk, operator="operator", confirm=True)
    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_REJECTED

    with pytest.raises(CommandError):
        run("approve", reference.pk, operator="operator", confirm=True)
    with pytest.raises(CommandError):
        run("activate", reference.pk, operator="operator")


def test_an_irreversible_action_requires_confirmation(reference, operator):
    reference.submit_for_review()

    with pytest.raises(CommandError, match="irreversible"):
        run("approve", reference.pk, operator="operator")

    reference.refresh_from_db()
    assert reference.review_state == ReferenceSolution.REVIEW_IN_REVIEW


def test_reject_also_requires_confirmation(reference, operator):
    reference.submit_for_review()

    with pytest.raises(CommandError, match="irreversible"):
        run("reject", reference.pk, operator="operator")


def test_deactivate_supersedes_without_editing_history(reference, operator):
    reference.submit_for_review()
    run("approve", reference.pk, operator="operator", confirm=True)
    run("activate", reference.pk, operator="operator")

    run("deactivate", reference.pk, operator="operator")

    reference.refresh_from_db()
    assert reference.is_active is False
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.source_code == SOURCE


# ═════════════════════════════════════════════════════════════
# Provenance
# ═════════════════════════════════════════════════════════════

def test_approval_records_who(reference, operator):
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.approved_by_id == operator.pk


def test_approval_records_when(reference, operator):
    reference.submit_for_review()
    before = reference.created_at

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.approved_at is not None
    assert reference.approved_at >= before


def test_approval_records_the_source_hash(reference, operator):
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.source_hash == compute_source_hash(SOURCE)
    assert reference.has_valid_approval_provenance is True


def test_approval_does_not_modify_the_source(reference, operator):
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)

    reference.refresh_from_db()
    assert reference.source_code == SOURCE


def test_an_approved_source_stays_frozen_afterwards(reference, operator):
    """The P2.7d database constraint must still be the backstop."""
    from django.db import IntegrityError, transaction

    reference.submit_for_review()
    run("approve", reference.pk, operator="operator", confirm=True)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.filter(pk=reference.pk).update(
                source_code="swapped after approval")


def test_one_active_reference_per_language_still_holds(question, operator):
    from django.db import IntegrityError, transaction

    first = ReferenceSolution.objects.create(
        question=question, language="python", source_code="a")
    second = ReferenceSolution.objects.create(
        question=question, language="python", source_code="b")
    for ref in (first, second):
        run("submit", ref.pk, operator="operator")
        run("approve", ref.pk, operator="operator", confirm=True)
    run("activate", first.pk, operator="operator")

    with pytest.raises((CommandError, IntegrityError)):
        with transaction.atomic():
            run("activate", second.pk, operator="operator")


# ═════════════════════════════════════════════════════════════
# Secrecy
# ═════════════════════════════════════════════════════════════

def test_the_list_output_never_contains_source(reference, operator, capsys):
    reference.submit_for_review()
    run("approve", reference.pk, operator="operator", confirm=True)
    capsys.readouterr()

    run("list")

    assert SECRET not in out(capsys)


def test_inspect_withholds_source_unless_asked(reference, operator, capsys):
    capsys.readouterr()

    run("inspect", reference.pk, operator="operator")

    printed = out(capsys)
    assert SECRET not in printed
    assert "Source withheld" in printed


def test_inspect_shows_source_when_explicitly_requested(reference, operator, capsys):
    """Positive control — a reviewer must be able to read what they approve."""
    capsys.readouterr()

    run("inspect", reference.pk, operator="operator", show_source=True)

    assert SECRET in out(capsys)


def test_the_command_builds_no_http_response():
    """
    The structural guarantee. `test_reference_solution_secrecy` fails any
    module that both reads `source_code` and builds a response; this command
    reads it, so it must never gain one.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent / "management" / "commands"
              / "reference_review.py").read_text(encoding="utf-8")

    assert "Response(" not in source
    assert "JsonResponse" not in source
    assert "APIView" not in source


def test_reference_solution_is_still_absent_from_admin():
    from django.contrib import admin

    assert ReferenceSolution not in admin.site._registry


def test_no_learner_api_exposes_the_reference(question, reference, learner):
    """The learner-facing surface must be unchanged by this phase."""
    reference.submit_for_review()

    client = APIClient()
    client.force_authenticate(user=learner)
    responses = [
        client.get(reverse("code-next-problem"), {"topic": "ReviewTopic"}),
        client.post(reverse("code-run"), {
            "code": "print(1)", "language": "python",
            "problem_id": question.pk}, format="json"),
    ]

    for response in responses:
        assert SECRET not in response.content.decode()


def test_the_submit_serializer_is_unchanged():
    from groups.serializers import CodeSubmitSerializer

    assert set(CodeSubmitSerializer().fields) == {"problem_id", "code", "language"}


# ═════════════════════════════════════════════════════════════
# Blast radius — nothing else may move
# ═════════════════════════════════════════════════════════════

def test_approval_changes_no_grading_data(question, reference, operator):
    before = (question.hidden_test_cases, question.status,
              question.trust_state, question.base_difficulty,
              question.boilerplate_code)
    reference.submit_for_review()

    run("approve", reference.pk, operator="operator", confirm=True)
    run("activate", reference.pk, operator="operator")

    question.refresh_from_db()
    assert (question.hidden_test_cases, question.status, question.trust_state,
            question.base_difficulty, question.boilerplate_code) == before


def test_approval_does_not_promote_the_question(question, reference, operator):
    """
    Approving a reference says the IMPLEMENTATION was reviewed. It says
    nothing about the question's answer key, which still needs oracle
    execution (P2.7g) before trust_state may move.
    """
    reference.submit_for_review()
    run("approve", reference.pk, operator="operator", confirm=True)
    run("activate", reference.pk, operator="operator")

    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


def test_the_command_creates_no_references(question, operator):
    """It reviews what exists; authoring is somebody else's job."""
    before = ReferenceSolution.objects.count()

    run("list")

    assert ReferenceSolution.objects.count() == before


def test_a_cross_question_reference_is_still_impossible(question, portal, operator):
    """
    P2.7d's F1 remediation must survive: an approved reference may only ever
    answer for its OWN question.
    """
    from groups.oracle import OracleService, OracleUnapproved

    other = Question.objects.create(
        title="Other", content="c", topic=question.topic,
        base_difficulty=1200.0, hidden_test_cases=[],
        boilerplate_code={}, hidden_wrapper_code={})
    reference = ReferenceSolution.objects.create(
        question=other, language="python", source_code=SOURCE)
    run("submit", reference.pk, operator="operator")
    run("approve", reference.pk, operator="operator", confirm=True)
    run("activate", reference.pk, operator="operator")

    calls = []

    def runner(*args, **kwargs):
        calls.append(args)
        return {"status": "Accepted", "status_id": 3, "stdout": "1",
                "stderr": "", "compile_output": "", "time": "0", "memory": 1}

    with pytest.raises(OracleUnapproved):
        OracleService(runner).run(question, reference, "1")
    assert calls == []


def test_listing_is_read_only(reference, operator):
    before = (reference.review_state, reference.is_active,
              reference.approved_by_id, reference.source_code)

    run("list")
    run("list", state="DRAFT")

    reference.refresh_from_db()
    assert (reference.review_state, reference.is_active,
            reference.approved_by_id, reference.source_code) == before


def test_listing_with_no_references_explains_the_consequence(db, capsys):
    run("list")

    assert "ORACLE_VERIFIED" in out(capsys)
