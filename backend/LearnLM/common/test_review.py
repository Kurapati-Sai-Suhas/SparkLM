"""
M7 Review Queue + staged curriculum gate tests.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from groups.models import (
    CodingPortal, Topic, TopicPrerequisite, UserCodingProfile, UserTopicMastery,
)


@pytest.fixture
def review_user(db):
    return get_user_model().objects.create_user(
        username="reviewer", password="pw-not-relevant", email="rev@test.com"
    )


@pytest.fixture
def topics(db):
    portal = CodingPortal.objects.create(name="Review Portal")
    a = Topic.objects.create(name="ReviewArray", structure_type="hierarchical", portal=portal)
    b = Topic.objects.create(name="ReviewTree", structure_type="hierarchical", portal=portal)
    TopicPrerequisite.objects.create(topic=b, prerequisite=a)
    return a, b


@pytest.mark.django_db
def test_review_queue_ranks_decayed_topics_and_reports_router(review_user, topics):
    a, b = topics
    UserCodingProfile.objects.create(user=review_user, elo_rating=1300)
    stale = timezone.now() - timedelta(days=10)
    # Practiced long ago with a short halflife -> heavily decayed, due.
    UserTopicMastery.objects.create(
        user=review_user, topic=a, accuracy=0.9, reviews=5,
        hlr_halflife=2.0, last_practiced=stale,
    )
    # Practiced just now -> fresh, not due.
    UserTopicMastery.objects.create(
        user=review_user, topic=b, accuracy=0.7, reviews=2,
        hlr_halflife=2.0, last_practiced=timezone.now(),
    )
    # Never practiced -> must not appear at all.
    c = Topic.objects.create(name="ReviewGraph", structure_type="flat", portal=a.portal)
    UserTopicMastery.objects.create(user=review_user, topic=c, accuracy=0.0, reviews=0)

    client = APIClient()
    client.force_authenticate(user=review_user)
    data = client.get(reverse("review-queue")).json()

    names = [i["topic"] for i in data["queue"]]
    assert names == ["ReviewArray", "ReviewTree"]  # worst retention first
    decayed, fresh = data["queue"]
    assert decayed["due"] is True
    assert decayed["retention_pct"] < 10          # 10 days at h=2 -> ~3%
    assert decayed["effective_mastery_pct"] < decayed["accuracy_pct"]
    assert fresh["due"] is False
    assert data["due_count"] == 1

    router = data["router"]
    assert router["route"] in ("hierarchical", "flat")
    assert router["elo"] == 1300
    assert "runs_z" in router and "avg_acc" in router


@pytest.mark.django_db
def test_review_queue_requires_auth():
    assert APIClient().get(reverse("review-queue")).status_code == 401


@pytest.mark.django_db
@override_settings(CURRICULUM_GATE_ENFORCE=True)
def test_locked_topic_is_rejected_server_side_when_gate_is_on(review_user, topics):
    a, b = topics  # b requires a; the user has mastered nothing
    client = APIClient()
    client.force_authenticate(user=review_user)

    response = client.get(reverse("code-next-problem"), {"topic": "ReviewTree"})
    assert response.status_code == 403
    assert response.json()["error"] == "topic_locked"
    assert response.json()["missing_prerequisites"] == ["ReviewArray"]


@pytest.mark.django_db
def test_locked_topic_is_allowed_while_gate_is_staged_off(review_user, topics):
    # Default (flag off): behavior is unchanged pre-demo; the frontend
    # guard carries the lock. This pins the staged-rollout contract.
    client = APIClient()
    client.force_authenticate(user=review_user)
    response = client.get(reverse("code-next-problem"), {"topic": "ReviewTree"})
    assert response.status_code != 403
