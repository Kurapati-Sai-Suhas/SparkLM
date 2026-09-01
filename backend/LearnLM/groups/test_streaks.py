"""
Daily solving streaks (M2 P2.2, T2.2.1).

`current_streak`, `highest_streak` and `last_active_date` have existed on
`UserCodingProfile` since the model was written and were read in two places
(`coding_views.GamificationDashboardView`, `dashboard_views`) and written in
none — so every learner's streak was 0 forever and the dashboard's streak card
was decoration. TECHNICAL_DEBT D6.

The update lives inside `ProgressionService.apply_submission`, under the
profile row lock that transaction already takes for Elo. That placement is the
whole design:

  * no second lock and no new lock order, so §2.2's deadlock preclusion is
    untouched;
  * the streak cannot commit without the submission that earned it, nor the
    submission without the streak;
  * it costs zero extra queries, because the existing `profile.save()`
    persists the fields.

These tests drive the real service and assert on the persisted row. None of
them call the streak helper directly — a test that pokes the helper would pass
just as happily if `apply_submission` stopped calling it.
"""

import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from groups import coding_views
from groups.models import (
    CodeSubmission, CodingPortal, Question, Topic, UserCodingProfile,
)
from groups.services import GradeResult, ProgressionService

User = get_user_model()


def make_question(name="Streak Problem", code="sp"):
    portal = CodingPortal.objects.create(name=f"{name} Portal")
    topic, _ = Topic.objects.get_or_create(
        name=f"Topic {code}", defaults={"structure_type": "flat", "portal": portal}
    )
    return Question.objects.create(
        title=name,
        content="Return the input.",
        topic=topic,
        base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )


def grade(passed=True):
    """A GradeResult standing in for a real Judge0 run."""
    return GradeResult(
        stored_code="print(1)",
        final_status="accepted" if passed else "wrong_answer",
        passed=1 if passed else 0,
        total=1,
        results=[{"time": "0.01", "memory": 1000, "status": "Accepted"}],
    )


def solve(user, question, passed=True):
    """One graded submission through the real transactional entry point."""
    return ProgressionService.apply_submission(
        user=user,
        question=question,
        language="python",
        difficulty=question.base_difficulty,
        grade=grade(passed),
    )


@pytest.fixture
def learner(db):
    return User.objects.create_user(
        username="streaker", password="Streak#2026x", email="s@t.com"
    )


@pytest.fixture
def question(db):
    return make_question()


def profile_of(user):
    p = UserCodingProfile.objects.get(user=user)
    return p


def backdate(user, days):
    """Pretend the learner's last solve was `days` ago."""
    UserCodingProfile.objects.filter(user=user).update(
        last_active_date=timezone.localdate() - timedelta(days=days)
    )


# ─────────────────────────────────────────────────────────────
# Streak semantics
# ─────────────────────────────────────────────────────────────

def test_first_solve_starts_a_one_day_streak(learner, question):
    """
    A learner who has solved today has a streak of 1, not 0. This is the
    value the dashboard streak card reads.
    """
    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 1
    assert p.highest_streak == 1
    assert p.last_active_date == timezone.localdate()


def test_solving_on_the_next_day_increments(learner, question):
    """The roadmap's success criterion, stated literally."""
    solve(learner, question)
    backdate(learner, 1)

    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 2
    assert p.highest_streak == 2


def test_three_consecutive_days_reach_three(learner, question):
    for _ in range(3):
        solve(learner, question)
        backdate(learner, 1)

    assert profile_of(learner).current_streak == 3


def test_solving_twice_in_one_day_counts_once(learner, question):
    """
    Idempotency per day. `last_active_date` is a DateField, so the day is the
    unit — the second solve of a day has already been counted. Without this,
    a learner could inflate a streak by resubmitting.
    """
    solve(learner, question)
    solve(learner, question)
    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 1, "same-day solves inflated the streak"
    assert p.highest_streak == 1


def test_a_second_solve_the_same_day_does_not_reset_an_established_streak(
    learner, question
):
    """
    The same-day guard's real job, and the case a one-day fixture cannot see.

    With a streak of 1, removing the guard is invisible: the recomputed value
    is 1 either way. At a streak of 2 the difference is destructive — today
    minus today is 0 days, which is not "consecutive", so the else-branch
    resets an established streak to 1. A learner would lose their streak by
    solving a second problem.

    Found by mutation testing: deleting `if last == today: return` left all
    eighteen other tests green.
    """
    solve(learner, question)
    backdate(learner, 1)
    solve(learner, question)
    assert profile_of(learner).current_streak == 2

    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 2, "a second solve the same day reset the streak"
    assert p.highest_streak == 2


def test_a_missed_day_resets_the_streak_to_one(learner, question):
    """
    Reset to 1, not 0: today IS a solve. A learner returning after a gap has
    a one-day streak, not a zero-day one.
    """
    solve(learner, question)
    backdate(learner, 1)
    solve(learner, question)
    assert profile_of(learner).current_streak == 2

    backdate(learner, 2)   # skipped a day
    solve(learner, question)

    assert profile_of(learner).current_streak == 1


def test_highest_streak_survives_a_break(learner, question):
    """`highest_streak` is a running maximum and must never decrease."""
    for _ in range(4):
        solve(learner, question)
        backdate(learner, 1)
    assert profile_of(learner).current_streak == 4

    backdate(learner, 10)
    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 1
    assert p.highest_streak == 4, "the record was lost when the streak broke"


def test_a_failed_submission_does_not_start_a_streak(learner, question):
    """
    "Solving on consecutive days" — a failed attempt is not a solve. It must
    not start a streak, and it must not set `last_active_date`, or the next
    day's real solve would read a false "yesterday".
    """
    solve(learner, question, passed=False)

    p = profile_of(learner)
    assert p.current_streak == 0
    assert p.last_active_date is None
    assert p.total_submissions == 1, "the submission itself must still count"


def test_a_failed_submission_does_not_break_a_streak(learner, question):
    """The other half: failing today leaves yesterday's streak standing."""
    solve(learner, question)
    backdate(learner, 1)

    solve(learner, question, passed=False)

    p = profile_of(learner)
    assert p.current_streak == 1
    assert p.last_active_date == timezone.localdate() - timedelta(days=1)


def test_a_failed_attempt_before_a_solve_on_the_same_day_still_counts(learner, question):
    """
    A learner who fails twice then succeeds has still solved today. The
    failures must not consume the day.
    """
    solve(learner, question, passed=False)
    solve(learner, question, passed=False)
    solve(learner, question, passed=True)

    assert profile_of(learner).current_streak == 1


def test_re_solving_an_already_solved_problem_still_counts_as_activity(
    learner, question
):
    """
    A repeat solve earns no rating — the Elo farming guard zeroes it — but it
    IS activity. `apply_submission` already documents repeat solves as
    "legitimate spaced repetition", and a learner revising yesterday's
    problem has practised today.

    Pinned because the two behaviours are decided in the same block and it
    would be easy to extend the farming guard over the streak by accident,
    which would punish exactly the revision the system is built to encourage.
    """
    solve(learner, question)          # first solve
    backdate(learner, 1)

    solve(learner, question)          # same question again, next day

    p = profile_of(learner)
    assert p.current_streak == 2, "a repeat solve did not count as activity"


def test_a_future_last_active_date_resets_rather_than_going_backwards(learner, question):
    """
    Only reachable if a row was written under a different clock, but the
    arithmetic is signed: without the explicit `.days == 1` test, a future
    date yields a negative delta and an undefined result.
    """
    solve(learner, question)
    UserCodingProfile.objects.filter(user=learner).update(
        last_active_date=timezone.localdate() + timedelta(days=5)
    )

    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 1
    assert p.last_active_date == timezone.localdate()


def test_the_day_boundary_is_the_project_timezone(learner, question, settings):
    """
    There is no per-user timezone field in the system, so the project
    timezone is the only day definition available. Pinning it here documents
    that as a deliberate constraint rather than an oversight, and fails if
    someone later introduces a boundary that disagrees with settings.
    """
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True

    solve(learner, question)

    assert profile_of(learner).last_active_date == timezone.localdate()


# ─────────────────────────────────────────────────────────────
# Transactional integrity
# ─────────────────────────────────────────────────────────────

def test_a_failure_after_the_streak_update_rolls_the_streak_back(
    learner, question, monkeypatch
):
    """
    The core P2.2 invariant: the streak cannot survive a transaction that did
    not commit. Fails the mastery step, which runs AFTER the streak is
    assigned and inside the same atomic block, and proves the database kept
    neither the streak nor the submission.

    This is a real rollback, not a mocked one — the assertions read committed
    rows back from the database.
    """
    solve(learner, question)
    backdate(learner, 1)
    before = profile_of(learner)

    def boom(*args, **kwargs):
        raise RuntimeError("mastery exploded")

    monkeypatch.setattr(ProgressionService, "_apply_sm2_update", boom)

    with pytest.raises(RuntimeError):
        solve(learner, question)

    after = profile_of(learner)
    assert after.current_streak == before.current_streak, "streak survived a rollback"
    assert after.last_active_date == before.last_active_date
    assert after.total_submissions == before.total_submissions
    assert CodeSubmission.objects.filter(user=learner).count() == 1


def test_the_streak_commits_with_the_submission_not_separately(learner, question):
    """
    Both halves of the same transaction: a persisted streak implies a
    persisted submission for the same action. Guards against the streak
    being moved to its own write outside the atomic block.
    """
    solve(learner, question)

    p = profile_of(learner)
    assert p.current_streak == 1
    assert CodeSubmission.objects.filter(user=learner, status="accepted").count() == 1


def test_the_streak_rides_the_existing_profile_write(learner, question):
    """
    Guards the transaction boundary, not merely the query count.

    The streak is assigned in memory before the `profile.save()` that Elo
    already performs, so one UPDATE carries both. Any refactor that gives the
    streak its OWN write shows up here as a second UPDATE — and the two
    refactors that would do so are exactly the dangerous ones: a separate
    `profile.save(update_fields=[...])` inside the lock (harmless but adds
    cost to the hottest transaction in the product), or a write moved after
    the atomic block, which would let a submission commit with no streak.
    Mutation testing confirmed this is what catches the post-commit variant.

    Matched with startswith("UPDATE") rather than a substring: the profile is
    locked with SELECT ... FOR UPDATE, whose SQL contains both "UPDATE" and
    the table name, so a substring test counts the lock as a write and this
    test reports 2 no matter what the code does.
    """
    solve(learner, question)
    backdate(learner, 1)

    with CaptureQueriesContext(connection) as captured:
        solve(learner, question)

    writes = [
        q["sql"] for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("UPDATE")
        and "codingprofile" in q["sql"].lower()
    ]
    assert len(writes) == 1, (
        f"the profile was written {len(writes)} times; the streak should ride "
        f"the existing save"
    )


# ─────────────────────────────────────────────────────────────
# Authorization
# ─────────────────────────────────────────────────────────────

def test_a_submission_only_touches_its_own_learners_streak(question):
    """
    Streaks must not become a cross-user write. Two learners solve; each
    profile reflects only its owner's activity.
    """
    alice = User.objects.create_user(
        username="alicestreak", password="Streak#2026x", email="a@t.com"
    )
    bob = User.objects.create_user(
        username="bobstreak", password="Streak#2026x", email="b@t.com"
    )

    solve(alice, question)
    solve(alice, question)

    assert profile_of(alice).current_streak == 1
    assert not UserCodingProfile.objects.filter(user=bob).exists(), (
        "another learner's profile was created by someone else's submission"
    )


@pytest.mark.django_db
def test_submitting_requires_authentication():
    """The streak is only reachable through an authenticated submission."""
    assert APIClient().post(reverse("code-submit"), {}, format="json").status_code == 401


def test_a_learner_cannot_aim_a_submission_at_another_users_profile(
    question, monkeypatch
):
    """
    A real attempt, not an assumption: the attacker submits through the HTTP
    endpoint with `user`, `user_id` and `username` fields naming the victim
    in the body. The endpoint must derive the learner from the authenticated
    request and ignore all three, so the streak lands on the attacker and the
    victim's profile is never even created.
    """
    attacker = User.objects.create_user(
        username="attacker", password="Streak#2026x", email="at@t.com"
    )
    victim = User.objects.create_user(
        username="victimtarget", password="Streak#2026x", email="vt@t.com"
    )

    monkeypatch.setattr(coding_views, "_run_on_judge0", lambda *a, **k: {
        "status": "Accepted", "status_id": 3, "stdout": "1", "stderr": "",
        "compile_output": "", "time": "0.01", "memory": 1000,
    })
    client = APIClient()
    client.force_authenticate(user=attacker)

    response = client.post(
        reverse("code-submit"),
        {
            "problem_id": question.id,
            "language": "python",
            "code": "class Solution:\n    def f(self, x):\n        return x",
            "user": victim.id,
            "user_id": victim.id,
            "username": victim.username,
        },
        format="json",
    )

    assert response.status_code == 200
    assert profile_of(attacker).current_streak == 1
    assert not UserCodingProfile.objects.filter(user=victim).exists(), (
        "a body field redirected the streak onto another learner"
    )
    assert not CodeSubmission.objects.filter(user=victim).exists()


# ─────────────────────────────────────────────────────────────
# Concurrency — the DoD test
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def race_question(transactional_db):
    cache.clear()
    yield make_question("Race Streak", "rs")
    cache.clear()


def test_concurrent_solves_increment_the_streak_exactly_once(race_question, monkeypatch):
    """
    P2.2's definition of done. Two accepted submissions land at the same
    instant; the streak must move 0 -> 1, never 0 -> 2.

    Both threads are held inside "Judge0" by a barrier before either reaches
    the learner-state lock, so the race is real rather than incidental — the
    same technique `test_concurrent_double_submit_awards_elo_exactly_once`
    uses, and the reason it works is the same: the loser re-reads the
    profile row after acquiring the lock and sees the winner's committed
    `last_active_date`.
    """
    user = User.objects.create_user(
        username="racestreak", password="Streak#2026x", email="rs@t.com"
    )
    UserCodingProfile.objects.create(user=user)

    barrier = threading.Barrier(2, timeout=10)

    def synced_judge0(source_code, language, stdin):
        barrier.wait()
        return {
            "status": "Accepted", "status_id": 3, "stdout": "1", "stderr": "",
            "compile_output": "", "time": "0.01", "memory": 1000,
        }

    monkeypatch.setattr(coding_views, "_run_on_judge0", synced_judge0)

    payload = {
        "problem_id": race_question.id,
        "language": "python",
        "code": "class Solution:\n    def f(self, x):\n        return x",
    }
    responses, errors = [], []

    def submit():
        try:
            client = APIClient()
            client.force_authenticate(user=user)
            responses.append(client.post(reverse("code-submit"), payload, format="json"))
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert sorted(r.status_code for r in responses) == [200, 200]

    profile = UserCodingProfile.objects.get(user=user)
    assert profile.current_streak == 1, (
        f"concurrent solves double-incremented the streak to "
        f"{profile.current_streak}"
    )
    assert profile.highest_streak == 1
    assert profile.last_active_date == timezone.localdate()
    assert profile.total_submissions == 2, "a profile counter update was lost"
