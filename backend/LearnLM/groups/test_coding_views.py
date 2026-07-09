"""
Offline tests for the coding hub submit/next endpoints.

Judge0 is mocked at the module boundary (groups.coding_views._run_on_judge0),
so this suite needs no network access and no JUDGE0_API_KEY. The previous
version of this file hit the live Judge0 API on every run and asserted a
response key ('agentic_coach') that the view never returned.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

import groups.coding_views as coding_views
from groups.models import (
    CodingPortal, Topic, Question, CodeSubmission, UserCodingProfile,
    UserTopicMastery, AgenticCoachLog,
)

User = get_user_model()


def judge0_mock(stdout="1", status_id=3, status="Accepted", error=None):
    """Build a fake _run_on_judge0 with a fixed verdict."""
    def _mock(source_code, language, stdin=""):
        if error:
            return {"error": error}
        return {
            "status": status,
            "status_id": status_id,
            "stdout": stdout,
            "stderr": "",
            "compile_output": "",
            "time": "0.05",
            "memory": 25000,
        }
    return _mock


SOLUTION_CODE = """class Solution:
    def solve(self, input_val):
        return input_val
"""


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="testuser", password="password", email="test@example.com")
    UserCodingProfile.objects.create(user=u, elo_rating=1200)
    return u


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Test Portal")
    topic, _ = Topic.objects.get_or_create(name="Array", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=999,
        title="Echo Problem",
        content="Return the input.",
        topic=topic,
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )


def submit(api_client, question, code=SOLUTION_CODE, language="python"):
    return api_client.post(reverse("code-submit"), {
        "problem_id": question.id,
        "code": code,
        "language": language,
    }, format="json")


# ─────────────────────────────────────────────────────────────
# Submission flow (Judge0 mocked)
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_accepted_submission_updates_elo_and_mastery(api_client, user, question, monkeypatch):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))
    api_client.force_authenticate(user=user)

    response = submit(api_client, question)

    assert response.status_code == 200
    assert response.data["status"] == "accepted"
    assert response.data["passed"] == 1
    assert response.data["all_passed"] is True
    assert response.data["elo_update"]["rating_change"] > 0

    mastery = UserTopicMastery.objects.get(user=user, topic=question.topic)
    assert mastery.accuracy == 1.0
    assert mastery.reviews == 1
    # record_real_submission must reset the decay checkpoint
    assert mastery.last_decay_applied_at is not None


@pytest.mark.django_db
def test_wrong_answer_reported_honestly(api_client, user, question, monkeypatch):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="wrong"))
    api_client.force_authenticate(user=user)

    response = submit(api_client, question)

    assert response.status_code == 200
    assert response.data["status"] == "wrong_answer"
    assert response.data["all_passed"] is False
    assert response.data["elo_update"]["rating_change"] < 0


@pytest.mark.django_db
def test_time_limit_status_is_reported(api_client, user, question, monkeypatch):
    # status_id 5 = Time Limit Exceeded. Before the status_id fix, this
    # collapsed to wrong_answer because results[] never stored status_id.
    monkeypatch.setattr(
        coding_views, "_run_on_judge0",
        judge0_mock(stdout="", status_id=5, status="Time Limit Exceeded"),
    )
    api_client.force_authenticate(user=user)

    response = submit(api_client, question)

    assert response.status_code == 200
    assert response.data["status"] == "time_limit"


@pytest.mark.django_db
def test_judge0_unavailable_returns_503(api_client, user, question, monkeypatch):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(error="Judge0 timed out. Try again."))
    api_client.force_authenticate(user=user)

    response = submit(api_client, question)

    assert response.status_code == 503


@pytest.mark.django_db
def test_java_submission_smoke(api_client, user, question, monkeypatch):
    # Java goes through import-stripping + the reflection wrapper; with
    # Judge0 mocked this is a plumbing smoke test, not an execution test.
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))
    api_client.force_authenticate(user=user)

    code = """import java.util.*;
class Solution {
    public int solve(int input) { return input; }
}"""
    response = submit(api_client, question, code=code, language="java")

    assert response.status_code == 200
    assert response.data["status"] == "accepted"


# ─────────────────────────────────────────────────────────────
# Agentic coach trigger + escalation
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_agentic_hint_appears_on_third_consecutive_failure(api_client, user, question, monkeypatch):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="wrong"))
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    api_client.force_authenticate(user=user)

    for i in range(3):
        response = submit(api_client, question)
        assert response.status_code == 200
        assert response.data["status"] == "wrong_answer"
        if i < 2:
            assert response.data.get("agentic_hint") is None
        else:
            assert response.data.get("agentic_hint") is not None


@pytest.mark.django_db
def test_agentic_coach_escalates_past_three_failures(api_client, user, question, monkeypatch):
    # Before the Phase 1 window fix, failed_attempts was mathematically
    # capped at 3, so the 5-fail (pseudocode) tier could never fire.
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="wrong"))
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    api_client.force_authenticate(user=user)

    last = None
    for _ in range(5):
        last = submit(api_client, question)

    log = AgenticCoachLog.objects.filter(user=user).order_by("-timestamp").first()
    assert log is not None
    assert log.failed_attempts_count == 5
    assert log.hint_source == "fallback"
    assert "Pseudocode" in last.data["agentic_hint"]


@pytest.mark.django_db
def test_coach_survives_webhook_timeout(api_client, user, question, monkeypatch):
    # n8n being down must never break the submission response.
    import requests as requests_lib

    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="wrong"))
    monkeypatch.setenv("N8N_WEBHOOK_URL", "http://localhost:9/dead")

    def dead_post(*args, **kwargs):
        raise requests_lib.exceptions.Timeout("Webhook offline")
    monkeypatch.setattr("groups.engines.agentic_coach.requests.post", dead_post)

    api_client.force_authenticate(user=user)
    for _ in range(3):
        response = submit(api_client, question)
        assert response.status_code == 200

    assert response.data["agentic_hint"] is not None  # fallback hint served


# ─────────────────────────────────────────────────────────────
# NextProblemView (recommendation endpoint)
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_next_problem_returns_frontend_schema(api_client, user, question, monkeypatch):
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    api_client.force_authenticate(user=user)

    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})

    assert response.status_code == 200
    data = response.data
    assert data["id"] == str(question.pk)
    assert data["difficulty"] in ("Easy", "Medium", "Hard")
    xai = data["advanced_xai"]["xai"]
    assert set(v["subject"] for v in xai["shap_values"]) == {
        "Time Complexity", "Space Complexity", "Logic Accuracy", "Topic Recency",
    }
    assert "decay_percent" in data["advanced_xai"]["decay_info"]


@pytest.mark.django_db
def test_failed_generation_is_never_persisted(api_client, user, question, monkeypatch):
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    monkeypatch.setattr(coding_views, "generate_test_cases", lambda *a, **k: None)

    question.hidden_test_cases = []
    question.save(update_fields=["hidden_test_cases"])
    api_client.force_authenticate(user=user)

    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})

    assert response.status_code == 200
    question.refresh_from_db()
    # The old code saved the garbage fallback [{"stdin": "1", ...}] here,
    # permanently corrupting the question's grading data.
    assert question.hidden_test_cases == []


# ─────────────────────────────────────────────────────────────
# Onboarding (Phase 0 repair regression test)
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_onboarding_calibrates_profile_and_skips_topics(api_client, user, question):
    api_client.force_authenticate(user=user)

    response = api_client.post(reverse("code-onboard"), {
        "known_topics": ["Array"],
    }, format="json")

    assert response.status_code == 200
    profile = UserCodingProfile.objects.get(user=user)
    assert profile.elo_rating == 1250  # 1200 + 1 known topic * 50
    assert profile.irt_latent_logic == pytest.approx(-1.6)

    sub = CodeSubmission.objects.get(user=user, question=question)
    assert sub.status == "accepted"
    assert sub.code == "# Skipped via Onboarding"

    # Re-onboarding must not duplicate the synthetic submission
    api_client.post(reverse("code-onboard"), {"known_topics": ["Array"]}, format="json")
    assert CodeSubmission.objects.filter(user=user, question=question).count() == 1
