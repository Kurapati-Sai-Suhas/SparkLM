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
def test_javascript_submission_smoke(api_client, user, question, monkeypatch):
    # JS goes through the new generic Node wrapper; with Judge0 mocked this
    # verifies the plumbing (language mapping + wrapper injection).
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))
    api_client.force_authenticate(user=user)

    code = """class Solution {
    solve(input) { return input; }
}"""
    response = submit(api_client, question, code=code, language="javascript")

    assert response.status_code == 200
    assert response.data["status"] == "accepted"


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


@pytest.mark.django_db
def test_repeat_solve_does_not_farm_elo(api_client, user, question, monkeypatch):
    monkeypatch.setattr(coding_views, "_run_on_judge0", judge0_mock(stdout="1"))
    api_client.force_authenticate(user=user)

    first = submit(api_client, question)
    assert first.data["elo_update"]["rating_change"] > 0
    rating_after_first = UserCodingProfile.objects.get(user=user).elo_rating

    second = submit(api_client, question)
    assert second.data["status"] == "accepted"
    assert second.data["elo_update"]["rating_change"] == 0.0
    assert UserCodingProfile.objects.get(user=user).elo_rating == rating_after_first

    # Repeats still count as spaced repetition — only the rating is guarded.
    mastery = UserTopicMastery.objects.get(user=user, topic=question.topic)
    assert mastery.reviews == 2


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
    # Grading data never leaves the server: only the first case's INPUT is
    # exposed, for the Run button. Expected output would be the answer key
    # on single-case questions.
    assert "hiddenTestCases" not in data
    assert data["sample_case"] == {"stdin": question.hidden_test_cases[0]["stdin"]}
    assert "expected_output" not in data["sample_case"]
    xai = data["advanced_xai"]["xai"]
    assert set(v["subject"] for v in xai["shap_values"]) == {
        "Time Complexity", "Space Complexity", "Logic Accuracy", "Topic Recency",
    }
    assert "decay_percent" in data["advanced_xai"]["decay_info"]


@pytest.mark.django_db
def test_boilerplate_code_is_always_a_language_keyed_object(api_client, user, question, monkeypatch):
    """
    M2 contract: the editor derives BOTH the starter template and the list of
    languages offering one by indexing boilerplate_code by language key.

    If this ever came back as a JSON string, null, or a list, the frontend's
    lookup would silently yield undefined for every language -- an empty
    editor with no error anywhere, which is precisely the failure M2 fixes.
    A question with no templates must still serialize as {} so the UI can
    report "no template" rather than crash on a null index.
    """
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")
    api_client.force_authenticate(user=user)

    # The fixture question deliberately carries no boilerplate at all.
    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})
    assert response.status_code == 200
    assert isinstance(response.data["boilerplate_code"], dict)

    question.boilerplate_code = {
        "python": "class Solution:\n    def solve(self):\n        pass",
        "javascript": "class Solution {\n    solve() {}\n}",
    }
    question.save(update_fields=["boilerplate_code"])

    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})
    payload = response.data["boilerplate_code"]
    assert isinstance(payload, dict)
    # Canonical key spelling matters: the selector's "js" option maps onto
    # "javascript" in the client. Renaming this key server-side would blank
    # the editor for every JavaScript user while submissions kept working,
    # because LANGUAGE_IDS accepts both spellings.
    assert "javascript" in payload
    assert payload["python"].startswith("class Solution")


@pytest.mark.django_db
def test_placeholder_questions_are_never_served(api_client, user, question, monkeypatch):
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")

    # An unseeded placeholder sits at a closer Elo distance than the real
    # question — it must still never be recommended.
    Question.objects.create(
        title="Unseeded Placeholder",
        topic=question.topic,
        content=f"{Question.PLACEHOLDER_MARKER} Unseeded Placeholder problem.",
        base_difficulty=1200.0,
        hidden_test_cases=[],
    )
    api_client.force_authenticate(user=user)

    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})

    assert response.status_code == 200
    assert response.data["id"] == str(question.pk)  # the real question, not the placeholder

    # With ONLY placeholders left, the endpoint reports completion instead
    # of serving boilerplate content to a student.
    question.delete()
    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})
    assert response.status_code == 200
    assert response.data.get("status") == "completed"


@pytest.mark.django_db
def test_caseless_questions_are_never_served(api_client, user, question, monkeypatch):
    """
    M6-smoke regression: ~1,100 CSV-imported rows carry real descriptions
    (no placeholder marker) but ZERO judge test cases — reseed skips them
    because they look seeded, and serving one guarantees an empty sample
    case and a failed submit. They must be quarantined like placeholders.
    """
    from groups.hybrid_router import RoutingClassifier
    monkeypatch.setattr(RoutingClassifier, "predict_route", lambda self, *a, **k: "flat")

    # Sits at a closer Elo distance than the armed question — must still
    # never be recommended.
    Question.objects.create(
        title="Caseless CSV Import",
        topic=question.topic,
        content="A real-looking imported description. Example: input 1, output 1.",
        base_difficulty=1200.0,
        hidden_test_cases=[],
    )
    api_client.force_authenticate(user=user)

    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})
    assert response.status_code == 200
    assert response.data["id"] == str(question.pk)  # the armed question wins

    # With only caseless rows left, report completion — never a dud problem.
    question.delete()
    response = api_client.get(reverse("code-next-problem"), {"topic": "Array"})
    assert response.status_code == 200
    assert response.data.get("status") == "completed"


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


@pytest.mark.django_db
def test_xai_payload_matches_frontend_schema(user):
    """
    The XAI response is a frontend CONTRACT, pinned key by key.

    AdaptiveCodingPortal.tsx reads advanced_xai.xai.{dominant_factor,
    success_probability, shap_values, recommendation} and
    advanced_xai.decay_info.decay_percent. Any of those going missing
    blanks a panel in production, and no other test would notice.

    M1/P1.1 removed the SHAP-over-GCN branch this test used to force on
    with `monkeypatch.setattr(coding_views, "USE_REAL_SHAP", True)`. That
    branch could never execute in production — the web tier installs
    requirements.txt only, so torch/shap were absent, and the flag shipped
    false. The assertions below are deliberately UNCHANGED: the whole
    point of the removal was that the payload the frontend receives does
    not move. `shap_values` keeps its name for the same reason.
    """
    view = coding_views.NextProblemView()
    payload = view._compute_xai(user, "Array", hlr_state=1.0)

    assert {"source", "dominant_factor", "success_probability", "shap_values"} <= set(payload)
    assert payload["source"] == "heuristic"
    assert 0 <= payload["success_probability"] <= 100
    assert {v["subject"] for v in payload["shap_values"]} == {
        "Time Complexity", "Space Complexity", "Logic Accuracy", "Topic Recency",
    }
    # Every radar entry carries the keys Recharts indexes on.
    for entry in payload["shap_values"]:
        assert set(entry) == {"subject", "A", "fullMark"}
        assert entry["fullMark"] == 100
    # Actionable layer: always present.
    assert isinstance(payload["weak_topics"], list)
    assert payload["recommendation"]


@pytest.mark.django_db
def test_xai_has_no_torch_dependency(user):
    """
    The XAI path must stay importable and correct without the ML extras.

    Production has never had torch installed (render.yaml installs
    requirements.txt only), so this pins the property the deployment
    already relied on rather than introducing a new one.
    """
    import sys

    blocked = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    for name in blocked:
        sys.modules[name] = None  # any import inside the call now raises

    try:
        payload = coding_views.NextProblemView()._compute_xai(user, "Array", hlr_state=1.0)
        assert payload["source"] == "heuristic"
        assert payload["shap_values"]
    finally:
        for name in blocked:
            del sys.modules[name]


# ─────────────────────────────────────────────────────────────
# MLOps telemetry authorization
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_telemetry_forbidden_for_students(api_client, user):
    # Rows contain other users' usernames and outcomes — staff only.
    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("mlops-telemetry"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_telemetry_allowed_for_staff(api_client, user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    api_client.force_authenticate(user=user)
    response = api_client.get(reverse("mlops-telemetry"))
    assert response.status_code == 200
    assert "stats" in response.data


# ─────────────────────────────────────────────────────────────
# Boilerplate backfill command
# ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_backfill_adds_missing_languages_and_preserves_python(question, monkeypatch):
    from django.core.management import call_command
    import groups.management.commands.backfill_boilerplate as backfill

    question.boilerplate_code = {"python": "class Solution:\n    def solve(self, x):\n        pass"}
    question.save(update_fields=["boilerplate_code"])

    fake_stubs = {
        "java": "class Solution {\n    public int solve(int x) { return 0; }\n}",
        "cpp": "class Solution {\npublic:\n    int solve(int x) { return 0; }\n};",
        "javascript": "class Solution {\n    solve(x) {}\n}",
        "c": "int solve(int x) {\n    return 0;\n}",
    }
    monkeypatch.setattr(backfill, "generate_starter_stubs", lambda *a, **k: fake_stubs)

    call_command("backfill_boilerplate", delay=0)

    question.refresh_from_db()
    assert set(question.boilerplate_code.keys()) == {"python", "java", "cpp", "javascript", "c"}
    assert "def solve" in question.boilerplate_code["python"]  # original preserved

    # Second run: nothing left to do (resume/skip behavior)
    calls = []
    monkeypatch.setattr(backfill, "generate_starter_stubs", lambda *a, **k: calls.append(1) or fake_stubs)
    call_command("backfill_boilerplate", delay=0)
    assert calls == []


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
