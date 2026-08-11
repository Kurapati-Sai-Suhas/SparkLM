"""recompute_mastery: repairs drifted accuracy/reviews from real history."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from groups.models import CodingPortal, CodeSubmission, Question, Topic, UserTopicMastery


@pytest.fixture
def drifted_setup(db):
    user = get_user_model().objects.create_user(
        username="drifted", password="pw-not-relevant", email="drift@test.com"
    )
    portal = CodingPortal.objects.create(name="Repair Portal")
    topic = Topic.objects.create(name="RepairArray", structure_type="hierarchical", portal=portal)
    question = Question.objects.create(
        topic=topic, title="Repair Q", content="c", base_difficulty=1200.0
    )
    # Ground truth: 4 submissions, 1 accepted -> real accuracy 0.25, reviews 4.
    for status in ["wrong_answer", "wrong_answer", "wrong_answer", "accepted"]:
        CodeSubmission.objects.create(
            user=user, question=question, language="python", code="x", status=status,
            # The rebuild reads only adaptive-eligible rows (M2 P2.7c): an
            # unverified verdict must not be reconstructed into mastery.
            adaptive_eligible=True,
        )
    # Drifted stored state, as if an earlier bug/session corrupted it.
    mastery = UserTopicMastery.objects.create(
        user=user, topic=topic, accuracy=0.0, reviews=1, hlr_halflife=3.5
    )
    return user, topic, mastery


@pytest.mark.django_db
def test_recompute_repairs_drifted_row(drifted_setup):
    user, topic, mastery = drifted_setup
    call_command("recompute_mastery")

    mastery.refresh_from_db()
    assert mastery.reviews == 4
    assert mastery.accuracy == pytest.approx(0.25)
    assert mastery.hlr_halflife == 3.5  # untouched — not reconstructable from history


@pytest.mark.django_db
def test_recompute_dry_run_changes_nothing(drifted_setup):
    user, topic, mastery = drifted_setup
    call_command("recompute_mastery", dry_run=True)

    mastery.refresh_from_db()
    assert mastery.reviews == 1
    assert mastery.accuracy == 0.0


@pytest.mark.django_db
def test_recompute_is_idempotent(drifted_setup):
    user, topic, mastery = drifted_setup
    call_command("recompute_mastery")
    call_command("recompute_mastery")  # second run: nothing left to change

    mastery.refresh_from_db()
    assert mastery.reviews == 4
    assert mastery.accuracy == pytest.approx(0.25)


@pytest.mark.django_db
def test_recompute_can_target_one_user(drifted_setup):
    user, topic, mastery = drifted_setup
    other = get_user_model().objects.create_user(
        username="untouched", password="pw-not-relevant", email="other@test.com"
    )
    other_mastery = UserTopicMastery.objects.create(
        user=other, topic=topic, accuracy=0.0, reviews=1
    )

    call_command("recompute_mastery", username="drifted")

    mastery.refresh_from_db()
    other_mastery.refresh_from_db()
    assert mastery.reviews == 4          # repaired
    assert other_mastery.reviews == 1    # untouched (no submissions, and not targeted)
