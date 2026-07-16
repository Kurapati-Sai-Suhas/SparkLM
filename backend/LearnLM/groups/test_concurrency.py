"""
Milestone 4 concurrency tests (frozen architecture §2.2).

transaction=True semantics (via the transactional_db fixture) are load-
bearing here: each thread gets real committed transactions, so
select_for_update contention actually happens. The default wrapped-
transaction test mode would hide every race these tests exist to catch.
"""

import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from groups import coding_views
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic,
    UserCodingProfile, UserTopicMastery,
)


@pytest.fixture
def question(transactional_db):
    # Throttle counters live in the process-wide cache and transactional
    # tests reset PK sequences, so a fresh user here can inherit an earlier
    # test's bucket — clear to keep these tests self-contained.
    cache.clear()
    portal = CodingPortal.objects.create(name="Race Portal")
    topic, _ = Topic.objects.get_or_create(
        name="Array", defaults={"structure_type": "flat", "portal": portal}
    )
    yield Question.objects.create(
        title="Race Problem",
        content="Return the input.",
        topic=topic,
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )
    cache.clear()


def test_concurrent_double_submit_awards_elo_exactly_once(question, monkeypatch):
    """
    Two simultaneous submissions of the same passing solution must count
    exactly once for rating: the loser of the race has to see the winner's
    committed accepted row (farming guard) and take the repeat-solve zero.
    Also asserts no profile-counter update is lost.
    """
    user = get_user_model().objects.create_user(
        username="racer", password="pw-not-relevant", email="racer@test.com"
    )
    profile = UserCodingProfile.objects.create(user=user)
    UserTopicMastery.objects.create(user=user, topic=question.topic)
    base_elo = profile.elo_rating

    # Timing assertion (the roadmap's named risk): both threads must sit
    # inside "Judge0" at the same time. If the learner-state lock ever grew
    # to span grading, the second thread could not reach the barrier while
    # the first held the lock, the barrier would time out, and the request
    # would fail loudly.
    barrier = threading.Barrier(2, timeout=10)

    def synced_judge0(source_code, language, stdin):
        barrier.wait()
        return {
            "status": "Accepted",
            "status_id": 3,
            "stdout": "1",
            "stderr": "",
            "compile_output": "",
            "time": "0.01",
            "memory": 1000,
        }

    monkeypatch.setattr(coding_views, "_run_on_judge0", synced_judge0)

    payload = {
        "problem_id": question.id,
        "language": "python",
        "code": "class Solution:\n    def f(self, x):\n        return x",
    }
    responses = []
    errors = []

    def submit():
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            responses.append(
                client.post(reverse("code-submit"), payload, format="json")
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            # Each thread opened its own DB connection; Django only closes
            # request connections on its own worker threads.
            connection.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert sorted(r.status_code for r in responses) == [200, 200]

    profile.refresh_from_db()
    assert profile.total_submissions == 2, "a profile counter update was lost"
    assert profile.successful_submissions == 2
    assert CodeSubmission.objects.filter(user=user, question=question).count() == 2

    # Exactly one submission may earn rating; the other must hit the
    # farming guard.
    changes = sorted(r.json()["elo_update"]["rating_change"] for r in responses)
    assert changes[0] == 0.0, f"both submissions earned rating: {changes}"
    assert changes[1] > 0.0
    assert profile.elo_rating == pytest.approx(base_elo + changes[1])

    mastery = UserTopicMastery.objects.get(user=user, topic=question.topic)
    assert mastery.reviews == 2, "a mastery update was lost"


def test_decay_sweep_sees_a_mid_sweep_return(question):
    """
    M4-review regression: the decay sweep must re-fetch each mastery row under
    select_for_update. A user who returns while the sweep runs commits a
    fresh last_practiced; the locked re-fetch guarantees the sweep sees it
    and charges nothing — the unlocked version decayed a stale snapshot.
    """
    user = get_user_model().objects.create_user(
        username="returner", password="pw-not-relevant", email="ret@test.com"
    )
    stale = timezone.now() - timedelta(days=30)
    mastery = UserTopicMastery.objects.create(
        user=user, topic=question.topic, elo_rating=1300.0,
        last_practiced=stale, last_decay_applied_at=stale,
    )

    errors = []

    def run_sweep():
        try:
            call_command("calculate_decay")
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    # Hold the row lock the way a mid-flight submission does, start the
    # sweep (it must block on this row), then commit a "return" with a
    # fresh last_practiced before releasing.
    with transaction.atomic():
        locked = UserTopicMastery.objects.select_for_update().get(pk=mastery.pk)
        sweep = threading.Thread(target=run_sweep)
        sweep.start()
        locked.last_practiced = timezone.now()
        locked.last_decay_applied_at = timezone.now()
        locked.save(update_fields=["last_practiced", "last_decay_applied_at"])
    sweep.join(timeout=30)

    assert not errors, errors
    mastery.refresh_from_db()
    assert mastery.elo_rating == 1300.0, "sweep charged decay from a stale row"


def test_onboarding_calibrates_profile_and_is_idempotent(question):
    user = get_user_model().objects.create_user(
        username="newbie", password="pw-not-relevant", email="newbie@test.com"
    )
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        reverse("code-onboard"), {"known_topics": ["Array"]}, format="json"
    )
    assert response.status_code == 200
    # (1/10) * 4 - 2 = -1.6 theta for one known topic
    assert response.json()["message"] == (
        "Onboarding complete! IRT Theta calibrated to -1.60."
    )

    profile = UserCodingProfile.objects.get(user=user)
    assert profile.elo_rating == 1250  # 1200 + 50 * one known topic
    assert profile.irt_latent_logic == pytest.approx(-1.6)

    synthetic = CodeSubmission.objects.filter(
        user=user, question__topic=question.topic, status="accepted"
    )
    assert synthetic.count() == 1

    # Re-onboarding must not duplicate the synthetic solve.
    again = client.post(
        reverse("code-onboard"), {"known_topics": ["Array"]}, format="json"
    )
    assert again.status_code == 200
    assert synthetic.count() == 1
