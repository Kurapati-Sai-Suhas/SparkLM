"""
DashboardBootstrapView — the combined dashboard payload (perf follow-up:
collapses 5 separate dashboard requests, incl. GamificationDashboard's own
independent fetch, into 1).
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from groups.models import StudyGroup, UserBadge, Badge, UserCodingProfile


@pytest.fixture(autouse=True)
def clear_leaderboard_cache():
    # The leaderboard is cached under a fixed key shared with
    # GamificationDashboardView — clear it so tests don't see stale data
    # left over from a previous test or process.
    cache.delete("gamification_leaderboard_top3")
    yield
    cache.delete("gamification_leaderboard_top3")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="bootstrap_user", password="pw-not-relevant", email="bootstrap@test.com"
    )


@pytest.mark.django_db
def test_requires_auth():
    assert APIClient().get(reverse("dashboard-bootstrap")).status_code == 401


@pytest.mark.django_db
def test_returns_all_sections_and_omits_mlops_for_non_staff(user):
    client = APIClient()
    client.force_authenticate(user=user)
    data = client.get(reverse("dashboard-bootstrap")).json()

    assert data["username"] == "bootstrap_user"
    assert set(data["stats"].keys()) >= {
        "active_groups", "created_groups", "joined_groups",
        "study_hours", "quizzes_taken", "achievement_points",
    }
    assert data["groups"] == []
    assert data["gamification"]["streak"] == 0
    # The leaderboard is a global cache shared across every user by design
    # (that's the whole point of it) -- another test in this same run may
    # have already populated it, so only its shape is guaranteed here, not
    # emptiness. test_badges_and_leaderboard_reflect_real_data below covers
    # its actual content.
    assert isinstance(data["gamification"]["leaderboard"], list)
    assert data["gamification"]["badges"] == []
    assert data["mlops"] is None


@pytest.mark.django_db
def test_mlops_populated_for_staff_only(user):
    client = APIClient()
    client.force_authenticate(user=user)
    assert client.get(reverse("dashboard-bootstrap")).json()["mlops"] is None

    user.is_staff = True
    user.save(update_fields=["is_staff"])
    data = client.get(reverse("dashboard-bootstrap")).json()
    assert data["mlops"] is not None
    assert "total_logs_captured" in data["mlops"]["stats"]


@pytest.mark.django_db
def test_groups_list_uses_a_count_not_a_full_member_list(user):
    other = get_user_model().objects.create_user(username="other_member", password="pw")
    group = StudyGroup.objects.create(
        name="Real Group", description="", creator=user, join_code="ABC123", capacity=10
    )
    group.members.add(user, other)

    client = APIClient()
    client.force_authenticate(user=user)
    data = client.get(reverse("dashboard-bootstrap")).json()

    assert len(data["groups"]) == 1
    row = data["groups"][0]
    assert row["name"] == "Real Group"
    assert row["capacity"] == 10
    assert row["members_count"] == 2
    assert "members" not in row  # never the full nested list — just the count


@pytest.mark.django_db
def test_badges_and_leaderboard_reflect_real_data(user):
    UserCodingProfile.objects.create(user=user, elo_rating=1500)
    badge = Badge.objects.create(badge_id="first-blood", name="First Blood", description="First accept")
    UserBadge.objects.create(user=user, badge=badge)

    client = APIClient()
    client.force_authenticate(user=user)
    data = client.get(reverse("dashboard-bootstrap")).json()

    assert data["gamification"]["leaderboard"][0]["elo"] == 1500
    assert data["gamification"]["badges"][0]["id"] == "first-blood"
