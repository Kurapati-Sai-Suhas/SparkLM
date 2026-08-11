"""
The trust boundary (M2 P2.7c).

The invariant this file exists to prove:

    A potentially incorrect grading result may be SHOWN to the learner,
    but it must NEVER teach the adaptive-learning system.

Why it is needed: no question in this system has ever had its expected
outputs confirmed by executing a trusted reference — there are zero
ReferenceSolution rows anywhere. So a Wrong Answer may be OUR defect rather
than the learner's mistake, and letting it lower their rating and mastery
teaches the model that a correct solver is weak.

Two axes, deliberately independent:

    status       lifecycle  — DRAFT / PENDING_REVIEW / PUBLISHED / BLOCKED
    trust_state  evidence   — UNVERIFIED / ORACLE_VERIFIED

A legacy question is PUBLISHED + UNVERIFIED: practise on it, see a verdict,
teach the model nothing.

The eligibility decision is FROZEN onto CodeSubmission at write time. That is
not an optimisation — recomputing it from the current Question row would mean
verifying a question on day 20 retroactively converts day-1 evidence, gathered
under an unchecked answer key, into trusted evidence.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from groups import coding_views
from groups.engines.tensor_builder import TensorBuilder
from groups.hybrid_router import compute_routing_telemetry
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic, UserCodingProfile,
    UserTopicMastery,
)
from groups.services import GradeResult, ProgressionService

User = get_user_model()


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Trust Portal")
    t, _ = Topic.objects.get_or_create(
        name="TrustTopic", defaults={"structure_type": "flat", "portal": portal})
    return t


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="trustee", password="Trust#2026x", email="t@t.com")


def make(topic, status=Question.STATUS_PUBLISHED, trust=Question.TRUST_UNVERIFIED,
         title="Q"):
    return Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "class Solution: pass"}, hidden_wrapper_code={},
        status=status, trust_state=trust)


def grade(passed=True):
    return GradeResult(
        stored_code="print(1)",
        final_status="accepted" if passed else "wrong_answer",
        passed=1 if passed else 0, total=1,
        results=[{"time": "0.01", "memory": 1000, "status": "Accepted"}])


def solve(learner, question, passed=True):
    return ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=question.base_difficulty, grade=grade(passed))


# ─────────────────────────────────────────────────────────────
# Safe defaults
# ─────────────────────────────────────────────────────────────

def test_a_new_question_is_draft_and_unverified(topic):
    """No creation path may produce a trusted question by omission."""
    q = Question.objects.create(
        title="D", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[], boilerplate_code={}, hidden_wrapper_code={})

    assert q.status == Question.STATUS_DRAFT
    assert q.trust_state == Question.TRUST_UNVERIFIED
    assert q.is_adaptive_eligible is False


def test_bulk_create_also_gets_the_safe_defaults(topic):
    """
    bulk_create bypasses save() and signals, so it is the path most likely to
    escape a model-level guard. Field defaults are applied by Question.__init__,
    which is why this holds — asserted rather than assumed.
    """
    Question.objects.bulk_create([
        Question(title=f"B{i}", content="c", topic=topic, base_difficulty=1200.0,
                 hidden_test_cases=[], boilerplate_code={}, hidden_wrapper_code={})
        for i in range(3)
    ])

    assert Question.objects.filter(status=Question.STATUS_DRAFT).count() == 3
    assert Question.objects.exclude(
        trust_state=Question.TRUST_UNVERIFIED).count() == 0


def test_a_new_submission_defaults_to_ineligible(topic, learner):
    """A row created by any path that does not decide is inert."""
    q = make(topic)
    s = CodeSubmission.objects.create(
        user=learner, question=q, language="python", code="x", status="accepted")

    assert s.adaptive_eligible is False


@pytest.mark.parametrize("status,trust,eligible", [
    (Question.STATUS_PUBLISHED, Question.TRUST_ORACLE_VERIFIED, True),
    (Question.STATUS_PUBLISHED, Question.TRUST_UNVERIFIED, False),
    (Question.STATUS_DRAFT, Question.TRUST_ORACLE_VERIFIED, False),
    (Question.STATUS_PENDING_REVIEW, Question.TRUST_ORACLE_VERIFIED, False),
    (Question.STATUS_BLOCKED, Question.TRUST_ORACLE_VERIFIED, False),
    (Question.STATUS_DRAFT, Question.TRUST_UNVERIFIED, False),
])
def test_adaptive_eligibility_requires_both_axes(topic, status, trust, eligible):
    assert make(topic, status, trust).is_adaptive_eligible is eligible


# ─────────────────────────────────────────────────────────────
# Freezing — the historical-integrity property
# ─────────────────────────────────────────────────────────────

def test_a_submission_against_an_unverified_question_is_ineligible(topic, learner):
    submission, _, _ = solve(learner, make(topic))

    assert submission.adaptive_eligible is False


def test_a_submission_against_a_verified_question_is_eligible(topic, learner):
    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)

    submission, _, _ = solve(learner, q)

    assert submission.adaptive_eligible is True


def test_verifying_a_question_later_does_not_promote_past_submissions(topic, learner):
    """
    The reason eligibility is frozen rather than derived. On day 1 the learner
    really was judged by an answer key nobody had checked; verifying the
    question on day 20 does not retroactively make that a fair test.
    """
    q = make(topic, trust=Question.TRUST_UNVERIFIED)
    day1, _, _ = solve(learner, q)
    assert day1.adaptive_eligible is False

    Question.objects.filter(pk=q.pk).update(
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    q.refresh_from_db()
    day20, _, _ = solve(learner, q)

    day1.refresh_from_db()
    assert day1.adaptive_eligible is False, "history was rewritten"
    assert day20.adaptive_eligible is True


def test_changing_question_status_never_rewrites_submissions(topic, learner):
    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)
    submission, _, _ = solve(learner, q)
    assert submission.adaptive_eligible is True

    Question.objects.filter(pk=q.pk).update(status=Question.STATUS_BLOCKED)

    submission.refresh_from_db()
    assert submission.adaptive_eligible is True, "history was rewritten"


# ─────────────────────────────────────────────────────────────
# The learner model must not move
# ─────────────────────────────────────────────────────────────

def test_an_unverified_submission_does_not_move_elo(topic, learner):
    q = make(topic)
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    _, elo_result, profile_after = solve(learner, q)

    assert profile_after.elo_rating == before
    assert elo_result["rating_change"] == 0.0


def test_a_verified_submission_does_move_elo(topic, learner):
    """Positive control — without it the guard could be blocking everything."""
    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    _, elo_result, profile_after = solve(learner, q)

    assert profile_after.elo_rating != before
    assert elo_result["rating_change"] != 0.0


def test_an_unverified_submission_does_not_move_mastery(topic, learner):
    q = make(topic)

    solve(learner, q, passed=False)

    assert not UserTopicMastery.objects.filter(
        user=learner, topic=q.topic, reviews__gt=0).exists()


def test_a_verified_submission_does_move_mastery(topic, learner):
    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)

    solve(learner, q)

    mastery = UserTopicMastery.objects.get(user=learner, topic=q.topic)
    assert mastery.reviews == 1


def test_an_unverified_submission_does_not_reach_routing_telemetry(topic, learner):
    """
    The failure this whole phase prevents: a correct solution marked Wrong
    Answer by an unchecked answer key would drag avg_acc down and route the
    learner to remedial practice they do not need.
    """
    q = make(topic)
    for _ in range(6):
        solve(learner, q, passed=False)

    avg_acc, runs_z, n = compute_routing_telemetry(learner)

    assert n == 0, "unverified verdicts entered the routing window"
    assert avg_acc == 0.7, "cold-start default expected, not a measured value"


def test_verified_submissions_do_reach_routing_telemetry(topic, learner):
    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)
    for _ in range(6):
        solve(learner, q, passed=False)

    _, _, n = compute_routing_telemetry(learner)

    assert n == 6


def test_an_unverified_submission_does_not_enter_the_ml_tensor(topic, learner):
    """
    Calls the builder and asserts on the FEATURE VALUES it returns.

    An earlier version of this test re-ran the builder's query itself and
    asserted on the rows. Mutation testing killed it: deleting the filter from
    tensor_builder.py left the test green, because a test that reimplements the
    thing it is checking never touches the code under test. The feature vector
    is the only observable that actually depends on that line.

    The unverified run is deliberately the worst possible performance. If it
    leaks into the tensor the timing/memory features collapse to 0.0; correctly
    excluded, there are no eligible rows at all and the builder uses its 0.5
    cold-start values.
    """
    unverified = make(topic, title="U")
    for _ in range(3):
        submission, _, _ = solve(learner, unverified)
        CodeSubmission.objects.filter(pk=submission.pk).update(
            execution_time_ms=500, memory_used_kb=60000)

    time_norm, space_norm, _, _ = TensorBuilder.build_user_feature_tensor(
        learner, topic.name)

    assert (time_norm, space_norm) == (0.5, 0.5), (
        "unverified timings reached the ML feature vector")


def test_a_verified_submission_does_enter_the_ml_tensor(topic, learner):
    """Positive control — proves the assertion above can move at all."""
    verified = make(topic, trust=Question.TRUST_ORACLE_VERIFIED, title="V")
    submission, _, _ = solve(learner, verified)
    CodeSubmission.objects.filter(pk=submission.pk).update(
        execution_time_ms=500, memory_used_kb=60000)

    time_norm, space_norm, _, _ = TensorBuilder.build_user_feature_tensor(
        learner, topic.name)

    assert (time_norm, space_norm) == (0.0, 0.0)


def test_the_mastery_rebuild_ignores_unverified_verdicts(topic, learner):
    """
    `recompute_mastery` reconstructs mastery from submission history. If it
    read unverified verdicts it would reintroduce exactly what the boundary
    excludes — and it would do so on a schedule, long after anyone was
    watching.
    """
    from django.core.management import call_command

    unverified = make(topic, title="U")
    for _ in range(4):
        solve(learner, unverified, passed=False)

    call_command("recompute_mastery")   # writes by default; --dry-run reports only

    assert not UserTopicMastery.objects.filter(
        user=learner, topic=topic, reviews__gt=0).exists()


def test_the_classifier_flywheel_ignores_unverified_verdicts(topic, learner):
    """
    RecommendationLog.actual_result_correct is the routing classifier's
    TRAINING LABEL. An unverified verdict there teaches the router a
    falsehood that survives every later fix to the question.
    """
    from groups.models import RecommendationLog

    q = make(topic)
    log = RecommendationLog.objects.create(
        user=learner, problem_id=str(q.id), recommended_topic=topic,
        actual_result_correct=None)

    solve(learner, q, passed=False)

    log.refresh_from_db()
    assert log.actual_result_correct is None, "an unverified verdict became a label"


def test_the_classifier_flywheel_still_records_verified_verdicts(topic, learner):
    from groups.models import RecommendationLog

    q = make(topic, trust=Question.TRUST_ORACLE_VERIFIED)
    log = RecommendationLog.objects.create(
        user=learner, problem_id=str(q.id), recommended_topic=topic,
        actual_result_correct=None)

    solve(learner, q, passed=True)

    log.refresh_from_db()
    assert log.actual_result_correct is True


# ─────────────────────────────────────────────────────────────
# But the learner still gets to practise
# ─────────────────────────────────────────────────────────────

def test_an_unverified_submission_is_still_recorded_and_visible(topic, learner):
    """
    Option B: the result is shown, stored and counted for history. Only the
    learner MODEL ignores it.
    """
    q = make(topic)

    submission, _, profile = solve(learner, q)

    assert CodeSubmission.objects.filter(user=learner).count() == 1
    assert submission.status == "accepted"
    assert profile.total_submissions == 1, "history counter should still move"


def test_an_unverified_solve_still_deduplicates(topic, learner):
    """
    solved_ids reads CodeSubmission regardless of eligibility, so a learner is
    not re-served a problem they already solved. Excluding the row entirely
    would have broken this.
    """
    q = make(topic)
    solve(learner, q)

    solved = CodeSubmission.objects.filter(
        user=learner, status='accepted').values_list('question_id', flat=True)

    assert q.id in list(solved)


# ─────────────────────────────────────────────────────────────
# Onboarding
# ─────────────────────────────────────────────────────────────

def test_onboarding_rows_are_never_adaptive_evidence(topic, learner):
    """
    Self-reported, not measured: no code ran and no verdict was earned. They
    exist so recommenders skip the topic, which is deduplication. This also
    closes a pre-existing hole — they carry status='accepted', so routing
    telemetry was counting a learner's own claim as measured accuracy.
    """
    make(topic, title="Known")

    ProgressionService.apply_onboarding(learner, [topic.name])

    rows = CodeSubmission.objects.filter(user=learner)
    assert rows.count() == 1
    assert rows.first().adaptive_eligible is False
    assert compute_routing_telemetry(learner)[2] == 0


# ─────────────────────────────────────────────────────────────
# Security
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("status", "PUBLISHED"),
    ("trust_state", "ORACLE_VERIFIED"),
    ("adaptive_eligible", True),
    ("execution_contract_version", "v2"),
])
def test_a_learner_cannot_inject_trust_fields(topic, learner, monkeypatch,
                                              field, value):
    """
    The serializer is an explicit allowlist — problem_id, code, language — so
    injected keys are dropped by validation, not merely ignored downstream.
    """
    q = make(topic, trust=Question.TRUST_UNVERIFIED)
    monkeypatch.setattr(coding_views, "_run_on_judge0", lambda *a, **k: {
        "status": "Accepted", "status_id": 3, "stdout": "1", "stderr": "",
        "compile_output": "", "time": "0.01", "memory": 1})
    client = APIClient()
    client.force_authenticate(user=learner)

    response = client.post(
        reverse("code-submit"),
        {"problem_id": q.id, "language": "python", "code": "print(1)", field: value},
        format="json")

    assert response.status_code == 200
    q.refresh_from_db()
    assert q.status == Question.STATUS_PUBLISHED
    assert q.trust_state == Question.TRUST_UNVERIFIED
    assert q.execution_contract_version == "v1"
    assert CodeSubmission.objects.get(user=learner).adaptive_eligible is False


def test_the_submit_serializer_remains_a_three_field_allowlist():
    from groups.serializers import CodeSubmitSerializer

    assert set(CodeSubmitSerializer().get_fields()) == {
        "problem_id", "code", "language"}


# ─────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────

def test_admin_cannot_publish_or_verify_by_hand():
    """
    Publishing and declaring outputs verified are pipeline outputs. A form
    field would let any staff account assert all of it with a dropdown.
    """
    from django.contrib import admin as dj_admin
    from groups.models import Question as Q

    question_admin = dj_admin.site._registry[Q]

    assert "status" in question_admin.readonly_fields
    assert "trust_state" in question_admin.readonly_fields
