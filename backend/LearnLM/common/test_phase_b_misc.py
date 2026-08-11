"""
Remaining M4 Phase B items: policy attribution, counter repair, and error
semantics.

Grouped rather than split into three near-empty files — each is a small,
independent change and the alternative is more scaffolding than substance.
"""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from rest_framework.test import APIClient

from groups.hybrid_router import ROUTING_POLICY_VERSION
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
    UserCodingProfile,
)

User = get_user_model()
PASSWORD = "PhaseB#2026x"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="phaseb", password=PASSWORD, email="pb@t.com"
    )


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="DSA Masterclass")
    topic = Topic.objects.create(name="Array", portal=portal)
    return Question.objects.create(
        topic=topic, title="Q", content="real content",
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "class Solution: pass"},
    )


def auth(user):
    from common.tokens import issue_token_pair
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_token_pair(user).access_token}")
    return client


# ── B5: policy attribution ───────────────────────────────────────────────

class TestPolicyVersion:
    def test_the_constant_is_non_empty_and_bounded(self):
        # Stored in a CharField(max_length=32); a longer value would be
        # truncated silently by some backends.
        assert ROUTING_POLICY_VERSION
        assert len(ROUTING_POLICY_VERSION) <= 32

    @pytest.mark.django_db
    def test_recommendations_record_the_policy_that_produced_them(self, user, question):
        response = auth(user).get(reverse("code-next-problem"), {"topic": "Array"})
        assert response.status_code == 200

        log = RecommendationLog.objects.filter(user=user).latest("created_at")
        assert log.policy_version == ROUTING_POLICY_VERSION

    @pytest.mark.django_db
    def test_the_column_is_nullable_for_pre_phase_b_rows(self, user, question):
        """
        The 177 production rows that predate this must stay valid; a null
        means "before policy versioning", not "unknown policy".
        """
        log = RecommendationLog.objects.create(
            user=user, recommended_topic=question.topic, engine_used="flat",
        )
        assert log.policy_version is None


# ── B8: derived counter repair ───────────────────────────────────────────

@pytest.mark.django_db
class TestRebuildCounters:
    def _run(self, *args):
        out = StringIO()
        call_command("rebuild_counters", *args, stdout=out)
        return out.getvalue()

    def _drifted_profile(self, user, question):
        CodeSubmission.objects.create(
            adaptive_eligible=True,
            user=user, question=question, language="python",
            code="x", status="accepted",
        )
        profile, _ = UserCodingProfile.objects.get_or_create(user=user)
        profile.total_submissions = 99      # drift, as a cascade delete leaves
        profile.successful_submissions = 99
        profile.save()
        return profile

    def test_reports_drift_without_writing_by_default(self, user, question):
        profile = self._drifted_profile(user, question)
        output = self._run()
        assert "99 -> 1" in output
        assert "--apply" in output

        profile.refresh_from_db()
        assert profile.total_submissions == 99, "read-only run modified data"

    def test_apply_corrects_the_counters(self, user, question):
        profile = self._drifted_profile(user, question)
        self._run("--apply")

        profile.refresh_from_db()
        assert profile.total_submissions == 1
        assert profile.successful_submissions == 1

    def test_reports_nothing_when_counters_agree(self, user, question):
        CodeSubmission.objects.create(
            adaptive_eligible=True,
            user=user, question=question, language="python",
            code="x", status="wrong_answer",
        )
        profile, _ = UserCodingProfile.objects.get_or_create(user=user)
        profile.total_submissions = 1
        profile.successful_submissions = 0
        profile.save()

        assert "No counter drift" in self._run()

    def test_a_profile_with_no_submissions_rebuilds_to_zero(self, user):
        profile, _ = UserCodingProfile.objects.get_or_create(user=user)
        profile.total_submissions = 5
        profile.save()

        self._run("--apply")
        profile.refresh_from_db()
        assert profile.total_submissions == 0


# ── B9: error semantics ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestMisconfiguredQuestionStatus:
    def test_a_question_with_no_test_cases_returns_409_not_500(self, user, question):
        """
        A data-integrity condition is not a server fault. Reporting it as 500
        polluted error metrics with a content problem and made real faults
        harder to see.
        """
        question.hidden_test_cases = []
        question.save(update_fields=["hidden_test_cases"])

        response = auth(user).post(
            reverse("code-submit"),
            {"problem_id": question.pk, "language": "python", "code": "x = 1"},
            format="json",
        )
        assert response.status_code == 409
        assert response.data["detail"] == "question_not_gradable"

    def test_a_gradable_question_is_unaffected(self, user, question, monkeypatch):
        import groups.coding_views as views
        monkeypatch.setattr(
            views, "_run_on_judge0",
            lambda *a, **kw: {"status": "Accepted", "status_id": 3, "stdout": "1",
                              "stderr": "", "compile_output": "", "time": "0.01",
                              "memory": 1000},
        )
        response = auth(user).post(
            reverse("code-submit"),
            {"problem_id": question.pk, "language": "python", "code": "x = 1"},
            format="json",
        )
        assert response.status_code == 200
