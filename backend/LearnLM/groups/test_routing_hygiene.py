"""
Routing hygiene (M2 P2.8a).

The defect this file exists to prevent:

    A learner fails a question and the router hands them the identical
    question again, indefinitely.

Nothing in the selection changed when they failed. `solved_ids` only grows on
`accepted`; Elo does not move for an unverified question; mastery and routing
telemetry are gated the same way; so the DAG returned the same topic and
`ORDER BY elo_diff LIMIT 1` returned the same row. Every input was
bit-identical, so the output had to be.

P2.8a makes selection exposure-aware and totally ordered:

    1. |base_difficulty - target_elo|   nearest difficulty (unchanged intent)
    2. failed within the cooldown       not-in-cooldown first
    3. attempt count                    least-seen first
    4. last attempt                     ascending, NULLs first
    5. question id                      terminal — makes the order TOTAL

Cooldown DEMOTES, it never excludes: a filter could empty the candidate set
and fall through to the topic-crossing fallback, so the invariant is enforced
by the shape of the query rather than by a guard someone has to remember.

Trust boundary: this reads `status` from submissions that may be UNVERIFIED.
That is permitted — it decides WHAT WE SHOW, never WHAT WE BELIEVE. No rating,
mastery, telemetry or tensor value is written here (P2.7c).

Every test drives the real consumer — `NextProblemView` through the API,
`ProgressionService`, the actual management command. Two previous audits found
tests that re-ran a production query instead of invoking it and stayed green
when the production filter was deleted.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from groups import coding_views
from groups.coding_views import FAILED_QUESTION_COOLDOWN
from groups.hybrid_router import ROUTING_POLICY_VERSION
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
    TopicPrerequisite, UserCodingProfile,
)
from groups.services import GradeResult, ProgressionService

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_dag_cache():
    """
    `HierarchicalEngine._get_graph` caches the curriculum DAG for 30 minutes,
    keyed on the portal name. Tests build different topic graphs under similar
    names, so a stale graph would leak between them.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Hygiene Portal")


@pytest.fixture
def topic(portal):
    return Topic.objects.create(name="HygieneTopic", structure_type="flat",
                                portal=portal)


@pytest.fixture
def learner(db):
    return User.objects.create_user(username="router-learner",
                                    password="Route#2026x", email="rl@t.com")


@pytest.fixture
def client(learner):
    api = APIClient()
    api.force_authenticate(user=learner)
    return api


def make(topic, title, difficulty=1200.0):
    return Question.objects.create(
        title=title, content="solve it", topic=topic, base_difficulty=difficulty,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "class Solution: pass"},
        hidden_wrapper_code={})


def attempt(learner, question, status="wrong_answer", ago=timedelta(0)):
    """
    A real CodeSubmission row placed at a controlled point in the past.

    `submitted_at` is auto_now_add, so it is rewritten afterwards — and it is
    the partition key, so this genuinely exercises the cross-partition move.
    """
    submission = CodeSubmission.objects.create(
        user=learner, question=question, language="python", code="x",
        status=status, adaptive_eligible=False)
    CodeSubmission.objects.filter(pk=submission.pk).update(
        submitted_at=timezone.now() - ago)
    return submission


def next_problem(client, topic_name="HygieneTopic"):
    return client.get(reverse("code-next-problem"), {"topic": topic_name})


def served_id(client, topic_name="HygieneTopic"):
    body = next_problem(client, topic_name).json()
    return body.get("id")


def grade(passed):
    return GradeResult(
        stored_code="print(1)",
        final_status="accepted" if passed else "wrong_answer",
        passed=1 if passed else 0, total=1,
        results=[{"time": "0.01", "memory": 1000, "status": "Accepted"}])


# ═════════════════════════════════════════════════════════════
# Q4 — counter consistency
# ═════════════════════════════════════════════════════════════

def test_rebuild_counters_agrees_with_the_live_writer(topic, learner):
    """
    The live writer increments unconditionally; the rebuild used to recount
    only `adaptive_eligible` rows. With nothing verified anywhere, running
    `--apply` would have reset every learner's visible activity to zero.
    """
    question = make(topic, "Counted")
    for passed in (True, False, False):
        ProgressionService.apply_submission(
            user=learner, question=question, language="python",
            difficulty=1200.0, grade=grade(passed))

    profile = UserCodingProfile.objects.get(user=learner)
    assert (profile.total_submissions, profile.successful_submissions) == (3, 1)

    call_command("rebuild_counters")          # report-only, no --apply

    profile.refresh_from_db()
    assert (profile.total_submissions, profile.successful_submissions) == (3, 1), (
        "rebuild_counters disagrees with the live writer")


def test_rebuild_counters_reports_no_drift_for_unverified_activity(
        topic, learner, capsys):
    question = make(topic, "No drift")
    for _ in range(4):
        ProgressionService.apply_submission(
            user=learner, question=question, language="python",
            difficulty=1200.0, grade=grade(False))

    call_command("rebuild_counters")

    assert "No counter drift detected." in capsys.readouterr().out


def test_rebuild_counters_still_repairs_real_drift(topic, learner):
    """Positive control — it must not be inert."""
    question = make(topic, "Real drift")
    ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=1200.0, grade=grade(True))
    UserCodingProfile.objects.filter(user=learner).update(
        total_submissions=99, successful_submissions=99)

    call_command("rebuild_counters", "--apply")

    profile = UserCodingProfile.objects.get(user=learner)
    assert (profile.total_submissions, profile.successful_submissions) == (1, 1)


def test_success_rate_counts_activity_not_evidence(topic, learner):
    question = make(topic, "Activity")
    for passed in (True, False):
        ProgressionService.apply_submission(
            user=learner, question=question, language="python",
            difficulty=1200.0, grade=grade(passed))

    profile = UserCodingProfile.objects.get(user=learner)
    assert profile.success_rate == 50.0
    assert CodeSubmission.objects.filter(
        user=learner, adaptive_eligible=True).count() == 0


# ═════════════════════════════════════════════════════════════
# Q3 — onboarding must not write ability
# ═════════════════════════════════════════════════════════════

def test_onboarding_with_no_topics_leaves_elo_at_the_default(topic, learner):
    ProgressionService.apply_onboarding(learner, [])

    assert UserCodingProfile.objects.get(user=learner).elo_rating == 1200.0


def test_onboarding_with_ten_topics_still_leaves_elo_at_the_default(
        portal, learner):
    """
    The whole point. Ten checkboxes used to be worth +500 Elo — roughly 15-30
    verified solves — and placed the learner above the hardest question in the
    bank on their very first request.
    """
    names = [f"Claimed{i}" for i in range(10)]
    for name in names:
        t = Topic.objects.create(name=name, structure_type="flat", portal=portal)
        make(t, f"Q for {name}")

    ProgressionService.apply_onboarding(learner, names)

    assert UserCodingProfile.objects.get(user=learner).elo_rating == 1200.0


def test_onboarding_still_marks_claimed_topics_as_solved(portal, learner):
    """Deduplication is the part that was always legitimate — keep it."""
    t = Topic.objects.create(name="Claimed", structure_type="flat", portal=portal)
    question = make(t, "Claimed question")

    ProgressionService.apply_onboarding(learner, ["Claimed"])

    row = CodeSubmission.objects.get(user=learner, question=question)
    assert row.status == "accepted"
    assert row.adaptive_eligible is False, "onboarding fabricated adaptive evidence"


def test_onboarding_still_stores_the_claim_as_theta(portal, learner):
    names = [f"T{i}" for i in range(5)]
    for name in names:
        Topic.objects.create(name=name, structure_type="flat", portal=portal)

    theta = ProgressionService.apply_onboarding(learner, names)

    profile = UserCodingProfile.objects.get(user=learner)
    assert profile.irt_latent_logic == pytest.approx(theta)
    assert theta == pytest.approx(0.0)


# ═════════════════════════════════════════════════════════════
# Q1 + Q9 — exposure-aware, totally ordered selection
# ═════════════════════════════════════════════════════════════

def test_a_failed_question_is_not_served_again_immediately(topic, learner, client):
    """R-C1. The defect this whole phase exists for."""
    first = make(topic, "First", 1200.0)
    make(topic, "Second", 1200.0)

    served = served_id(client)
    attempt(learner, Question.objects.get(pk=served), "wrong_answer")

    assert served_id(client) != served, "the failed question was served again"


def test_two_calls_with_no_activity_between_them_are_stable(topic, learner, client):
    """
    A GET with nothing changed in between must be idempotent. Repetition is
    only a defect once the learner has actually interacted.
    """
    for i in range(3):
        make(topic, f"Stable {i}", 1200.0)

    assert served_id(client) == served_id(client) == served_id(client)


def test_a_recently_failed_question_is_demoted_below_untried_ones(
        topic, learner, client):
    failed = make(topic, "Failed", 1200.0)
    untried = make(topic, "Untried", 1200.0)
    attempt(learner, failed, "wrong_answer", ago=timedelta(hours=1))

    assert served_id(client) == str(untried.pk)


def test_cooldown_expiry_returns_a_question_to_the_normal_pool(
        topic, learner, client):
    """The other half of demotion — it must wear off."""
    old = make(topic, "Old failure", 1200.0)
    recent = make(topic, "Recent failure", 1200.0)
    attempt(learner, old, "wrong_answer",
            ago=FAILED_QUESTION_COOLDOWN + timedelta(hours=1))
    attempt(learner, recent, "wrong_answer", ago=timedelta(minutes=5))

    assert served_id(client) == str(old.pk)


def test_when_everything_is_in_cooldown_the_least_recent_is_still_served(
        topic, learner, client):
    """
    The invariant that makes demotion safe: cooldown can NEVER empty the
    candidate set. If it filtered instead of ordering, this would fall through
    to the topic-crossing fallback.
    """
    stale = make(topic, "Failed 20h ago", 1200.0)
    fresh = make(topic, "Failed 1h ago", 1200.0)
    attempt(learner, stale, "wrong_answer", ago=timedelta(hours=20))
    attempt(learner, fresh, "wrong_answer", ago=timedelta(hours=1))

    body = next_problem(client).json()

    assert body.get("status") != "topic_exhausted", "cooldown emptied the set"
    assert body["id"] == str(stale.pk)


def test_the_least_attempted_question_wins_a_tie(topic, learner, client):
    once = make(topic, "Tried once", 1200.0)
    thrice = make(topic, "Tried three times", 1200.0)
    attempt(learner, once, "wrong_answer", ago=timedelta(days=10))
    for _ in range(3):
        attempt(learner, thrice, "wrong_answer", ago=timedelta(days=10))

    assert served_id(client) == str(once.pk)


def test_nearest_difficulty_still_outranks_every_exposure_term(
        topic, learner, client):
    """
    Ordering key 1 is unchanged. Exposure breaks ties; it does not override
    difficulty matching.
    """
    near = make(topic, "Near", 1200.0)
    far = make(topic, "Far", 1600.0)
    attempt(learner, near, "wrong_answer", ago=timedelta(minutes=1))

    assert served_id(client) == str(near.pk), "an exposure term outranked difficulty"


def test_selection_is_deterministic_across_repeated_calls(topic, learner, client):
    """
    Q9. `base_difficulty` takes one of three values across the whole bank, so
    ties are hundreds of rows deep and SQL leaves their order unspecified.
    Without the terminal id key the answer can change between query plans.
    """
    for i in range(8):
        make(topic, f"Tied {i}", 1200.0)

    answers = {served_id(client) for _ in range(12)}

    assert len(answers) == 1, f"non-deterministic selection: {answers}"


def test_ties_resolve_to_the_lowest_question_id(topic, learner, client):
    ids = sorted(make(topic, f"Same {i}", 1200.0).pk for i in range(5))

    assert served_id(client) == str(ids[0])


def test_a_solved_question_is_never_served_again(topic, learner, client):
    solved = make(topic, "Solved", 1200.0)
    other = make(topic, "Other", 1600.0)
    attempt(learner, solved, "accepted")

    for _ in range(3):
        assert served_id(client) == str(other.pk)


def test_solved_exclusion_ignores_adaptive_eligibility(topic, learner, client):
    """
    Dedup is a serving fact, not an adaptive signal. An unverified accepted
    submission still means "you solved this" (P2.7c).
    """
    solved = make(topic, "Unverified solve", 1200.0)
    other = make(topic, "Remaining", 1200.0)
    row = attempt(learner, solved, "accepted")
    assert row.adaptive_eligible is False

    assert served_id(client) == str(other.pk)


def test_selection_runs_a_bounded_number_of_queries(topic, learner,
                                                    client, django_assert_max_num_queries):
    """
    The replaced implementation materialised every solved question id into
    Python and sent it back as NOT IN (...), an IN-list that grew with the
    learner's own history. Selection is now one set-based statement.
    """
    for i in range(30):
        make(topic, f"Bulk {i}", 1200.0)
    for i in range(20):
        attempt(learner, make(topic, f"Done {i}", 1200.0), "accepted")

    with django_assert_max_num_queries(25):
        next_problem(client)


# ═════════════════════════════════════════════════════════════
# Fallback safety
# ═════════════════════════════════════════════════════════════

def test_an_exhausted_topic_returns_a_typed_response(portal, topic, learner, client):
    """
    Never silently cross topics. The old fallback returned an arbitrary
    question from anywhere while the portal badge still showed the topic the
    learner had asked for.
    """
    solved = make(topic, "Only one", 1200.0)
    attempt(learner, solved, "accepted")
    elsewhere = Topic.objects.create(name="Elsewhere", structure_type="flat",
                                     portal=portal)
    make(elsewhere, "Somewhere else", 1200.0)

    body = next_problem(client).json()

    assert body["status"] == "topic_exhausted"
    assert body["requested_topic"] == "HygieneTopic"
    assert body["next_problem"] is None
    assert "Elsewhere" in body["suggested_topics"]


def test_an_exhausted_topic_never_returns_a_foreign_question(
        portal, topic, learner, client):
    solved = make(topic, "Only one", 1200.0)
    attempt(learner, solved, "accepted")
    elsewhere = Topic.objects.create(name="Elsewhere", structure_type="flat",
                                     portal=portal)
    foreign = make(elsewhere, "Foreign", 1200.0)

    body = next_problem(client).json()

    assert body.get("id") is None
    assert str(foreign.pk) not in str(body)


def test_a_genuinely_empty_catalogue_still_reports_completed(topic, learner, client):
    """The pre-existing behaviour must survive."""
    solved = make(topic, "The only question", 1200.0)
    attempt(learner, solved, "accepted")

    body = next_problem(client).json()

    assert body["status"] == "completed"
    assert body["mastery_percentage"] == 100.0


def test_suggested_topics_are_deterministic(portal, topic, learner, client):
    attempt(learner, make(topic, "Solved", 1200.0), "accepted")
    for name in ("Zeta", "Alpha", "Mid"):
        make(Topic.objects.create(name=name, structure_type="flat", portal=portal),
             f"Q {name}")

    first = next_problem(client).json()["suggested_topics"]
    second = next_problem(client).json()["suggested_topics"]

    assert first == second == sorted(first)


# ═════════════════════════════════════════════════════════════
# Q5 — topic provenance
# ═════════════════════════════════════════════════════════════

def test_the_response_reports_the_topic_actually_served(topic, learner, client):
    make(topic, "Served", 1200.0)

    body = next_problem(client).json()

    assert body["requested_topic"] == "HygieneTopic"
    assert body["served_topic"] == "HygieneTopic"
    assert body["topic_substituted"] is False


def test_a_dag_substitution_is_reported(portal, learner, client):
    """
    The hierarchical route may serve a different topic than requested — that
    is the DAG doing its job. Nothing in the response used to say so, and the
    portal renders its badge from the URL, so the badge could sit above a
    question from somewhere else.
    """
    foundation = Topic.objects.create(name="Foundation", structure_type="flat",
                                      portal=portal)
    advanced = Topic.objects.create(name="Advanced", structure_type="flat",
                                    portal=portal)
    TopicPrerequisite.objects.create(topic=advanced, prerequisite=foundation)
    make(foundation, "Foundation question", 1200.0)
    make(advanced, "Advanced question", 1200.0)

    body = next_problem(client, topic_name="Advanced").json()

    assert body["requested_topic"] == "Advanced"
    assert body["served_topic"] == "Foundation"
    assert body["topic_substituted"] is True


def test_provenance_comparison_is_case_insensitive(topic, learner, client):
    make(topic, "Case", 1200.0)

    body = next_problem(client, topic_name="hygienetopic").json()

    assert body["topic_substituted"] is False


def test_an_unknown_topic_is_rejected(topic, learner, client):
    """
    P2.8a pinned the old `Topic.objects.first()` fallback as a regression
    guard, explicitly "not an endorsement". P2.8b (B11) replaces it: an
    unknown topic is a client error, not an invitation to guess.

    The detailed contract lives in test_learning_signal_hygiene.py; this
    asserts only that the routing path no longer substitutes silently.
    """
    make(topic, "Reachable", 1200.0)

    response = next_problem(client, topic_name="NoSuchTopic")

    assert response.status_code == 400
    assert response.json()["requested_topic"] == "NoSuchTopic"


# ═════════════════════════════════════════════════════════════
# Q10 — policy version
# ═════════════════════════════════════════════════════════════

def test_new_recommendations_carry_the_new_policy_version(topic, learner, client):
    make(topic, "Logged", 1200.0)

    next_problem(client)

    log = RecommendationLog.objects.filter(user=learner).latest("created_at")
    assert log.policy_version == "v2-exposure-aware" == ROUTING_POLICY_VERSION


def test_historical_rows_keep_their_own_policy_version(topic, learner, client):
    """Attribution is not backfillable — old rows must not be rewritten."""
    make(topic, "Logged", 1200.0)
    historical = RecommendationLog.objects.create(
        user=learner, recommended_topic=topic, engine_used="flat",
        problem_id="1", policy_version="v1-runs-test-elo-band")

    next_problem(client)

    historical.refresh_from_db()
    assert historical.policy_version == "v1-runs-test-elo-band"


# ═════════════════════════════════════════════════════════════
# Trust boundary — P2.7c must be untouched
# ═════════════════════════════════════════════════════════════

def test_an_unverified_submission_still_moves_nothing(topic, learner):
    question = make(topic, "Unverified", 1200.0)
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    submission, elo, after = ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=1200.0, grade=grade(False))

    assert submission.adaptive_eligible is False
    assert after.elo_rating == before
    assert elo["rating_change"] == 0.0


def test_a_verified_submission_still_updates_normally(topic, learner):
    """Positive control — P2.8a must not have broken the adaptive path."""
    question = make(topic, "Verified", 1200.0)
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = profile.elo_rating

    submission, elo, after = ProgressionService.apply_submission(
        user=learner, question=question, language="python",
        difficulty=1200.0, grade=grade(True))

    assert submission.adaptive_eligible is True
    assert after.elo_rating > before
    assert elo["rating_change"] > 0


def test_serving_a_question_never_writes_learner_state(topic, learner, client):
    """
    The cooldown reads a possibly-wrong verdict. It may change WHAT WE SHOW;
    it must never change WHAT WE BELIEVE.
    """
    make(topic, "Read only", 1200.0)
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    before = (profile.elo_rating, profile.total_submissions,
              profile.successful_submissions)

    next_problem(client)

    profile.refresh_from_db()
    assert (profile.elo_rating, profile.total_submissions,
            profile.successful_submissions) == before


# ═════════════════════════════════════════════════════════════
# Deliberate retry must keep working
# ═════════════════════════════════════════════════════════════

def _accepting_runner(stdout="1"):
    def runner(source, language, stdin=""):
        return {"status": "Accepted", "status_id": 3, "stdout": stdout,
                "stderr": "", "compile_output": "", "time": "0.01",
                "memory": 1000}
    return runner


def test_a_failed_question_can_still_be_retried_by_raw_id(
        topic, learner, client, monkeypatch):
    """
    Demotion changes what the RECOMMENDER offers. It must not touch the raw-id
    path, which is how a learner deliberately retries the problem in front of
    them.
    """
    question = make(topic, "Retry me", 1200.0)
    attempt(learner, question, "wrong_answer", ago=timedelta(minutes=1))
    monkeypatch.setattr(coding_views, "_run_on_judge0", _accepting_runner())

    run = client.post(reverse("code-run"), {
        "code": "print(1)", "language": "python", "problem_id": question.pk,
        "stdin": "1"}, format="json")
    submit = client.post(reverse("code-submit"), {
        "code": "print(1)", "language": "python", "problem_id": question.pk},
        format="json")

    assert run.status_code == 200
    assert submit.status_code == 200
    assert submit.json()["all_passed"] is True


# ═════════════════════════════════════════════════════════════
# Ordering keys — each test is built so that ONLY its key can
# produce the expected answer
# ═════════════════════════════════════════════════════════════
#
# The first version of these tests all passed with individual ordering keys
# deleted, because the remaining keys happened to agree. A test that cannot
# distinguish its key from the one below it is not testing that key. Each case
# below puts two keys in DELIBERATE CONFLICT and asserts the one that should
# win.

def test_cooldown_outranks_attempt_count(topic, learner, client):
    """
    Kills "remove the cooldown key" and "set the window to zero".

    Fewer attempts (1) but failed minutes ago, versus more attempts (2) but
    last failed well outside the window. Cooldown says serve the stale one;
    attempt count alone would say serve the fresh one.
    """
    fresh = make(topic, "Failed once, minutes ago", 1200.0)
    stale = make(topic, "Failed twice, days ago", 1200.0)
    attempt(learner, fresh, "wrong_answer", ago=timedelta(hours=1))
    for days in (3, 4):
        attempt(learner, stale, "wrong_answer", ago=timedelta(days=days))

    assert served_id(client) == str(stale.pk), (
        "attempt count outranked the cooldown, or the cooldown key is inert")


def test_attempt_count_outranks_last_attempt(topic, learner, client):
    """
    Kills "remove the attempt-count key".

    Both are outside the cooldown, so key 2 ties. Fewer attempts but seen more
    recently, versus more attempts seen longer ago: attempt count says the
    former, recency alone says the latter.
    """
    seldom = make(topic, "One attempt, 25h ago", 1200.0)
    often = make(topic, "Three attempts, 5 days ago", 1200.0)
    attempt(learner, seldom, "wrong_answer", ago=timedelta(hours=25))
    for days in (5, 6, 7):
        attempt(learner, often, "wrong_answer", ago=timedelta(days=days))

    assert served_id(client) == str(seldom.pk), (
        "recency outranked attempt count, or the attempt-count key is inert")


def test_last_attempt_outranks_question_id(topic, learner, client):
    """
    Kills "remove the last-attempt key".

    Same cooldown bucket, same attempt count — only recency separates them.
    The longer-ago question is created SECOND so it carries the HIGHER id: if
    the recency key is dropped, the terminal id key returns the other one.
    """
    recent = make(topic, "Failed 1h ago (lower id)", 1200.0)
    older = make(topic, "Failed 20h ago (higher id)", 1200.0)
    attempt(learner, recent, "wrong_answer", ago=timedelta(hours=1))
    attempt(learner, older, "wrong_answer", ago=timedelta(hours=20))

    assert served_id(client) == str(older.pk), (
        "question id outranked recency, or the recency key is inert")


def test_the_terminal_id_key_decides_a_wide_tie(topic, learner, client):
    """
    Kills "remove the terminal id key".

    Two hundred rows tie on every other key. PostgreSQL sorts equal keys with
    an unstable quicksort, so at this width the pre-sort heap order does not
    survive — without an explicit final key the winner is whatever the sort
    happens to leave on top.
    """
    ids = sorted(make(topic, f"Wide tie {i}", 1200.0).pk for i in range(200))

    assert served_id(client) == str(ids[0])


def test_null_last_attempt_is_unreachable_by_the_recency_key(topic, learner):
    """
    EQUIVALENT-MUTANT PROOF, not a behaviour test.

    `nulls_first=True` on the recency key cannot be observed: a NULL
    `last_attempt_at` means the learner has no submissions for that question,
    which also makes `attempt_count` zero — and attempt count is compared
    first. So every NULL row is already separated from every non-NULL row by
    the key above. Flipping the NULL ordering is therefore an equivalent
    mutant, and this test records why rather than pretending otherwise.

    It is still asserted, because the equivalence depends on the two
    annotations staying in agreement.
    """
    from groups.coding_views import _candidate_questions

    untried = make(topic, "Never attempted", 1200.0)
    tried = make(topic, "Attempted", 1200.0)
    attempt(learner, tried, "wrong_answer", ago=timedelta(days=9))

    rows = {q.pk: q for q in _candidate_questions(learner, topic.name)}

    assert rows[untried.pk].last_attempt_at is None
    assert rows[untried.pk].attempt_count == 0
    assert rows[tried.pk].last_attempt_at is not None
    assert rows[tried.pk].attempt_count == 1


def test_the_selection_query_actually_orders_by_question_id_last(
        topic, learner, client):
    """
    STRUCTURAL GUARD for the terminal ordering key.

    Deleting `"id"` from the ordering is not killable by a behavioural test.
    SQL leaves the order of rows with equal sort keys UNSPECIFIED — it does
    not make them wrong, so PostgreSQL is free to return the same row as the
    correct query, and at every width tried here it does. The behaviour only
    diverges under a different plan: a parallel sort, a different work_mem, an
    index scan, or the physical order after a VACUUM or a row update.

    "It happens to agree on this machine today" is exactly the property that
    cannot be relied on, so the guard inspects the SQL the production
    queryset actually emits. This reads the real statement issued by a real
    request — it does not rebuild the query, which is the failure mode two
    earlier audits caught.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(4):
        make(topic, f"Ordered {i}", 1200.0)

    with CaptureQueriesContext(connection) as captured:
        next_problem(client)

    selects = [
        q["sql"] for q in captured.captured_queries
        if "groups_question" in q["sql"] and "ORDER BY" in q["sql"]
    ]
    assert selects, "no ordered question query was issued"

    ordered_by = [s.split("ORDER BY")[-1] for s in selects]
    assert any(
        clause.strip().rstrip(" LIMIT 1").rstrip().endswith('"id" ASC')
        or '"id" ASC' in clause.split(",")[-1]
        for clause in ordered_by
    ), (
        "the candidate query does not end its ORDER BY with question id; "
        "without a unique terminal key the ordering is only PARTIAL and every "
        f"guarantee above it is void. Clauses seen: {ordered_by}"
    )


# ═════════════════════════════════════════════════════════════════════
# Structured routing-decision log (M2 P2.24)
#
# The predecessor was a formatted sentence: readable, and useless for
# answering "how often does the router choose flat?" without regex over
# prose. These tests pin the schema and — more importantly — pin that the
# LOGGED route is the route actually served, not a second computation.
# ═════════════════════════════════════════════════════════════════════

import json as _json
import logging as _logging


def _routing_events(caplog):
    """Every structured routing decision captured, parsed."""
    events = []
    for record in caplog.records:
        message = record.getMessage()
        # Require the JSON body, so a same-prefixed non-event could never be
        # parsed as one. The failure notice is named so it cannot collide,
        # but a parser should not depend on that.
        prefix = "routing decision {"
        if message.startswith(prefix):
            events.append(_json.loads(message.split("routing decision ", 1)[1]))
    return events


def test_a_routing_decision_emits_one_structured_event(client, topic, caplog):
    make(topic, "Structured Log Q")

    with caplog.at_level(_logging.INFO):
        response = next_problem(client)

    assert response.status_code == 200
    events = _routing_events(caplog)
    assert len(events) == 1, "exactly one routing decision per recommendation"

    event = events[0]
    assert set(event) == {
        "event", "learner_id", "route", "avg_acc", "runs_z", "n",
        "cold_start", "decided_by", "model_artifact", "policy_version",
        "elo", "latency_ms"}
    assert event["event"] == "routing_decision"
    assert event["policy_version"] == ROUTING_POLICY_VERSION


def test_the_logged_route_is_the_route_actually_served(
        client, learner, topic, caplog):
    """
    The load-bearing test. The log must report the decision that was USED,
    not a recomputation that could disagree with it.
    """
    from groups.hybrid_router import RoutingClassifier, compute_routing_telemetry

    make(topic, "Route Agreement Q")

    with caplog.at_level(_logging.INFO):
        response = next_problem(client)

    assert response.status_code == 200
    event = _routing_events(caplog)[0]

    avg_acc, runs_z, _ = compute_routing_telemetry(learner)
    profile = UserCodingProfile.objects.get(user=learner)
    expected = RoutingClassifier().predict_route(
        avg_acc, runs_z, profile.elo_rating / 2000.0)

    assert event["route"] == expected
    assert event["route"] in {"flat", "hierarchical"}


def test_logged_telemetry_matches_the_telemetry_used_for_the_decision(
        client, learner, topic, caplog):
    from groups.hybrid_router import compute_routing_telemetry

    make(topic, "Telemetry Agreement Q")

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    event = _routing_events(caplog)[0]
    avg_acc, runs_z, n = compute_routing_telemetry(learner)

    assert event["avg_acc"] == round(float(avg_acc), 4)
    assert event["runs_z"] == round(float(runs_z), 4)
    assert event["n"] == n


def test_cold_start_is_logged_as_cold_start(client, learner, topic, caplog):
    """
    No adaptive-eligible history, so telemetry takes the documented
    cold-start path. The event must say so rather than leaving a reader to
    infer it from n.
    """
    make(topic, "Cold Start Q")
    assert not CodeSubmission.objects.filter(
        user=learner, adaptive_eligible=True).exists()

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    event = _routing_events(caplog)[0]
    assert event["n"] == 0
    assert event["cold_start"] is True
    assert event["avg_acc"] == 0.7
    assert event["runs_z"] == 0.0


def test_the_event_names_the_branch_and_carries_no_artifact(
        client, topic, caplog):
    """No trained artifact exists, so the heuristic decides and the event
    must say which — a reader must never infer whether a model was used."""
    make(topic, "Branch Q")

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    event = _routing_events(caplog)[0]
    assert event["decided_by"] == "heuristic"
    assert event["model_artifact"] is None


def test_the_event_carries_latency(client, topic, caplog):
    make(topic, "Latency Q")

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    event = _routing_events(caplog)[0]
    assert event["latency_ms"] is not None
    assert event["latency_ms"] >= 0.0


def test_logging_mutates_no_state(client, learner, topic, caplog):
    """Observability must be inert. Snapshot everything routing could touch."""
    from groups.models import UserTopicMastery

    make(topic, "Inert Q")
    # NextProblemView reaches Elo through get_or_create, so the FIRST request
    # legitimately creates a profile row. Warm it before snapshotting, or this
    # test measures that write rather than logging's effect.
    next_problem(client)

    before = (
        CodeSubmission.objects.count(),
        UserTopicMastery.objects.count(),
        Question.objects.count(),
        Question.objects.filter(
            trust_state=Question.TRUST_ORACLE_VERIFIED).count(),
        UserCodingProfile.objects.get(user=learner).elo_rating,
    )

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    after = (
        CodeSubmission.objects.count(),
        UserTopicMastery.objects.count(),
        Question.objects.count(),
        Question.objects.filter(
            trust_state=Question.TRUST_ORACLE_VERIFIED).count(),
        UserCodingProfile.objects.get(user=learner).elo_rating,
    )
    assert before == after, "routing logging changed state"


def test_a_broken_logger_cannot_change_the_route_or_break_serving(
        client, topic, monkeypatch, caplog):
    """
    Requirement F. The recommendation must survive a logging failure
    unchanged — the decision is already made by the time the log is written.
    """
    from groups import hybrid_router

    served_before = served_id(client)

    def exploding_info(*args, **kwargs):
        raise RuntimeError("log backend down")

    # NOT json.dumps: `json` is a shared module, so patching it there breaks
    # DRF's renderer too and the failure would come from response rendering
    # rather than from logging. Patching this logger is what "the logging
    # backend is down" actually means.
    monkeypatch.setattr(hybrid_router.logger, "info", exploding_info)
    # The warm-up call above emitted a real event; scope the assertion to
    # what happens AFTER the logger is broken.
    caplog.clear()

    with caplog.at_level(_logging.INFO):
        response = next_problem(client)

    assert response.status_code == 200, "a logging failure broke serving"
    assert response.json().get("id") == served_before, (
        "a logging failure changed which question was served")
    assert _routing_events(caplog) == [], "no event should have been emitted"
    assert "routing_decision_log_failed" in caplog.text


def test_the_event_is_serializable_and_carries_no_grading_truth(
        client, topic, caplog):
    make(topic, "Serializable Q")

    with caplog.at_level(_logging.INFO):
        next_problem(client)

    event = _routing_events(caplog)[0]
    body = _json.dumps(event)          # must round-trip for a log pipeline
    for forbidden in ("expected_output", "hidden_test_cases", "stdin",
                      "boilerplate", "reasoning"):
        assert forbidden not in body
