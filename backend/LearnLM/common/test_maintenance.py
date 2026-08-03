"""
Maintenance sweep contract (M4 Phase B).

Milestone 3 left two time-dependent commands that nothing invoked, and in
production 60 of 66 UserTopicMastery rows had never been decayed — so
/api/review/queue/ was reading a model that had not updated since its rows
were created.

The tests below are shaped around the two ways this can fail *quietly*:
a sub-task that stops working while the sweep still reports success, and a
sweep that stops running entirely. Both were invisible before; the heartbeat
is what makes them observable, so the heartbeat is tested as hard as the work.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.contrib.auth import get_user_model
from django.utils import timezone

from common.models import MaintenanceRun
from groups.models import CodingPortal, Topic, UserTopicMastery

User = get_user_model()


def make_mastery(username, *, reviews=5, halflife=1.0, days_ago=30.0, accuracy=0.9):
    user = User.objects.create_user(
        username=username, password="Maint#2026x", email=f"{username}@t.com"
    )
    portal = CodingPortal.objects.create(name="DSA Masterclass")
    topic = Topic.objects.create(name=f"Topic-{username}", portal=portal)
    return UserTopicMastery.objects.create(
        user=user,
        topic=topic,
        reviews=reviews,
        accuracy=accuracy,
        hlr_halflife=halflife,
        last_practiced=timezone.now() - timedelta(days=days_ago),
    )


def run(*args, **kwargs):
    out, err = StringIO(), StringIO()
    call_command("run_maintenance", *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue() + err.getvalue()


# ── the heartbeat ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestHeartbeat:
    def test_a_successful_sweep_records_a_fresh_heartbeat(self):
        run()
        beat = MaintenanceRun.objects.get(task="run_maintenance")
        assert beat.succeeded is True
        assert beat.age_seconds < 30
        assert beat.duration_ms is not None

    def test_each_subtask_records_its_own_heartbeat(self):
        run()
        tasks = set(MaintenanceRun.objects.values_list("task", flat=True))
        assert {"calculate_decay", "send_spaced_repetition", "run_maintenance"} <= tasks

    def test_rerunning_overwrites_rather_than_appending(self):
        """
        One row per task. An audit trail of every sweep would grow without
        bound to answer a question nobody asks — the per-run detail is
        already in the log.
        """
        run()
        run()
        assert MaintenanceRun.objects.filter(task="run_maintenance").count() == 1

    def test_a_failing_subtask_still_records_a_heartbeat(self, monkeypatch):
        """
        The ambiguity this removes: a sweep that crashes before writing its
        heartbeat is indistinguishable from a sweep that never started.
        """
        import common.management.commands.run_maintenance as mod

        def boom(*a, **kw):
            raise RuntimeError("decay exploded")

        monkeypatch.setattr(mod, "call_command", boom)

        with pytest.raises(CommandError):
            run()

        beat = MaintenanceRun.objects.get(task="run_maintenance")
        assert beat.succeeded is False
        assert "failed" in beat.detail


# ── failure isolation ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestFailureIsolation:
    def test_one_failing_subtask_does_not_prevent_the_others(self, monkeypatch):
        import common.management.commands.run_maintenance as mod

        real = mod.call_command
        calls = []

        def selective(command, *a, **kw):
            calls.append(command)
            if command == "calculate_decay":
                raise RuntimeError("decay exploded")
            return real(command, *a, **kw)

        monkeypatch.setattr(mod, "call_command", selective)

        with pytest.raises(CommandError):
            run()

        assert "send_spaced_repetition" in calls, (
            "notifications were skipped because decay failed — sub-tasks must "
            "be independent"
        )
        assert MaintenanceRun.objects.get(task="calculate_decay").succeeded is False
        assert MaintenanceRun.objects.get(task="send_spaced_repetition").succeeded is True

    def test_the_process_exits_nonzero_when_a_subtask_fails(self, monkeypatch):
        """Heartbeat is for humans; the exit code is what reddens the workflow."""
        import common.management.commands.run_maintenance as mod
        monkeypatch.setattr(mod, "call_command", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(CommandError):
            run()

    def test_unknown_subtask_is_rejected_rather_than_silently_doing_nothing(self):
        with pytest.raises(CommandError, match="Unknown sub-task"):
            run(only="not_a_task")


# ── idempotence of the decay sweep ───────────────────────────────────────

@pytest.mark.django_db
class TestDecayIdempotence:
    def test_running_twice_does_not_double_charge_decay(self):
        mastery = make_mastery("decay_twice", days_ago=30)
        run()
        mastery.refresh_from_db()
        after_first = mastery.elo_rating

        run()
        mastery.refresh_from_db()
        assert mastery.elo_rating == after_first, (
            "second sweep re-charged the same inactive window; the "
            "last_decay_applied_at checkpoint is not holding"
        )

    def test_decay_is_applied_to_an_inactive_user(self):
        mastery = make_mastery("decay_me", days_ago=30)
        before = mastery.elo_rating
        run()
        mastery.refresh_from_db()
        assert mastery.elo_rating < before
        assert mastery.last_decay_applied_at is not None


# ── spaced repetition: the mock-data regression ──────────────────────────

@pytest.mark.django_db
class TestSpacedRepetitionUsesRealData:
    def test_a_user_with_no_due_topics_is_not_notified(self):
        """
        The stub's worst behaviour: it notified EVERY user about "Arrays"
        from three hardcoded constants, regardless of what they had
        practiced. Recently-practiced topics must produce nothing.
        """
        make_mastery("fresh_user", days_ago=0.0, halflife=30.0)
        out = run()
        assert "notified 0 user(s)" in out

    def test_a_user_with_a_decayed_topic_is_notified(self):
        m = make_mastery("stale_user", days_ago=30.0, halflife=1.0)
        out = run()
        assert "notified 1 user(s)" in out
        assert m.topic.name in out

    def test_unpracticed_topics_are_never_due(self):
        """is_due() requires reviews > 0 — nothing to forget."""
        make_mastery("never_practiced", reviews=0, days_ago=99.0, halflife=1.0)
        out = run()
        assert "notified 0 user(s)" in out

    def test_notifications_name_the_users_actual_topic(self):
        """
        The stub hardcoded "Arrays". Whatever this reports must come from
        the database.
        """
        m = make_mastery("real_topic_user", days_ago=30.0, halflife=1.0)
        out = run()
        assert m.topic.name in out
        assert "Arrays" not in out

    def test_selection_is_capped_and_worst_first(self):
        from groups.management.commands.send_spaced_repetition import (
            MAX_TOPICS_PER_USER, due_topics_for,
        )

        user = User.objects.create_user(
            username="many_topics", password="Maint#2026x", email="mt@t.com"
        )
        portal = CodingPortal.objects.create(name="P")
        rows = []
        for i in range(6):
            topic = Topic.objects.create(name=f"T{i}", portal=portal)
            rows.append(UserTopicMastery.objects.create(
                user=user, topic=topic, reviews=5, hlr_halflife=1.0,
                last_practiced=timezone.now() - timedelta(days=10 + i * 5),
            ))

        due = due_topics_for(rows)
        assert len(due) == MAX_TOPICS_PER_USER
        retentions = [r for _, r in due]
        assert retentions == sorted(retentions), "worst retention must come first"


# ── dry run must actually be dry ─────────────────────────────────────────

@pytest.mark.django_db
class TestDryRunChangesNothing:
    """
    The polish pass found `--dry-run` running the REAL decay sweep.

    `run_maintenance` special-cased `send_spaced_repetition` by name when
    deciding who got `dry_run=True`, so `calculate_decay` — which had no
    dry-run mode at all — executed normally and mutated learner state. The
    Phase B report recommended running `--dry-run` against production first,
    which would have silently decayed ~60 rows.

    Dry-run support is now declared per task, and these assert that "dry"
    means dry.
    """

    def test_dry_run_does_not_decay(self):
        mastery = make_mastery("dry_decay", days_ago=30)
        before_rating = mastery.elo_rating

        run(dry_run=True)

        mastery.refresh_from_db()
        assert mastery.elo_rating == before_rating, "dry run mutated learner state"
        assert mastery.last_decay_applied_at is None, "dry run stamped the checkpoint"

    def test_dry_run_still_reports_what_would_happen(self):
        make_mastery("dry_report", days_ago=30)
        output = run(dry_run=True)
        assert "would decay 1" in output
        assert "DRY RUN" in output

    def test_dry_run_records_no_heartbeat(self):
        run(dry_run=True)
        assert not MaintenanceRun.objects.exists(), (
            "a dry run wrote a heartbeat, so a real sweep would look like it "
            "had already happened"
        )

    def test_a_real_run_after_a_dry_run_still_decays(self):
        """The dry run must not have consumed the checkpoint."""
        mastery = make_mastery("dry_then_real", days_ago=30)
        before = mastery.elo_rating

        run(dry_run=True)
        run()

        mastery.refresh_from_db()
        assert mastery.elo_rating < before


@pytest.mark.django_db
class TestSummaryOutput:
    def test_the_summary_reports_rows_changes_timing_and_exit(self):
        make_mastery("summary_user", days_ago=30)
        output = run()

        assert "MAINTENANCE SUMMARY" in output
        assert "calculate_decay" in output
        assert "send_spaced_repetition" in output
        assert "2 sub-task(s): 2 succeeded, 0 failed" in output
        assert "EXIT: success" in output

    def test_the_summary_marks_a_dry_run_clearly(self):
        output = run(dry_run=True)
        assert "DRY RUN" in output
        assert "no changes applied" in output

    def test_the_summary_reports_failures_and_exit_status(self, monkeypatch):
        import common.management.commands.run_maintenance as mod
        real = mod.call_command

        def selective(command, *a, **kw):
            if command == "calculate_decay":
                raise RuntimeError("boom")
            return real(command, *a, **kw)

        monkeypatch.setattr(mod, "call_command", selective)

        with pytest.raises(CommandError):
            run()
