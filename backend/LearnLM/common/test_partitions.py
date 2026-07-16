"""
Milestone 2 regression tests: §4.4 index catalog + §4.3 monthly
partitioning of groups_codesubmission.

These run raw catalog queries on purpose — they verify that the DATABASE
matches what migrations 0031/0032 and models.py claim, catching any drift
between Django's model state and the physical schema.
"""

from datetime import datetime, timezone as dt_timezone
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from groups.models import CodeSubmission

from common.management.commands.ensure_submission_partitions import (
    _month_add,
    _partition_name,
)

pytestmark = pytest.mark.django_db

CATALOG_INDEXES = {
    "subm_user_ts_idx",
    "subm_user_q_ts_idx",
    "subm_user_status_idx",
    "question_topic_diff_idx",
    "reclog_user_prob_ts_idx",
}


def _fetch_one(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def _partition_holding(submission):
    """Physical partition a row lives in (via its tableoid)."""
    return _fetch_one(
        "SELECT tableoid::regclass::text FROM groups_codesubmission "
        "WHERE id = %s",
        [submission.id],
    )[0]


def _existing_partition_names():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT child.relname FROM pg_inherits"
            " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
            " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
            " WHERE parent.relname = 'groups_codesubmission'"
        )
        return {row[0] for row in cursor.fetchall()}


def _make_submission(username):
    user = get_user_model().objects.create_user(
        username=username, password="test-pass-1", email=f"{username}@test.com"
    )
    return CodeSubmission.objects.create(
        user=user, language="python", code="print(1)", status="accepted"
    )


def test_codesubmission_table_is_partitioned():
    row = _fetch_one(
        "SELECT relkind FROM pg_class WHERE relname = 'groups_codesubmission'"
    )
    assert row is not None and row[0] == "p"  # 'p' = partitioned table


def test_index_catalog_exists_in_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        names = {row[0] for row in cursor.fetchall()}
    missing = CATALOG_INDEXES - names
    assert not missing, f"§4.4 catalog indexes missing from DB: {missing}"


def test_submission_routes_to_current_month_partition():
    submission = _make_submission("part_route_user")
    now = timezone.now().astimezone(dt_timezone.utc)
    assert _partition_holding(submission) == _partition_name(now.year, now.month)


def test_ensure_partitions_creates_horizon_and_is_idempotent():
    call_command("ensure_submission_partitions", months_ahead=6, stdout=StringIO())

    existing = _existing_partition_names()
    now = timezone.now().astimezone(dt_timezone.utc)
    year, month = now.year, now.month
    for _ in range(7):  # current month + 6 ahead
        assert _partition_name(year, month) in existing
        year, month = _month_add(year, month, 1)

    # Second run must be a clean no-op.
    out = StringIO()
    call_command("ensure_submission_partitions", months_ahead=6, stdout=out)
    assert "Created 0 partition(s), moved 0 row(s)" in out.getvalue()


def test_months_ahead_is_bounded():
    # Guard against typo-driven DDL runaway (e.g. "--months-ahead 2027").
    with pytest.raises(CommandError):
        call_command("ensure_submission_partitions", months_ahead=121)
    with pytest.raises(CommandError):
        call_command("ensure_submission_partitions", months_ahead=-1)


def test_dry_run_changes_nothing():
    before = _existing_partition_names()
    out = StringIO()
    call_command(
        "ensure_submission_partitions", months_ahead=12, dry_run=True, stdout=out
    )
    assert _existing_partition_names() == before
    assert "[dry-run]" in out.getvalue()


def test_rescues_rows_stranded_in_default_partition():
    submission = _make_submission("part_rescue_user")
    now = timezone.now().astimezone(dt_timezone.utc)

    # Push the row 8 months out — beyond the migration's 3-month horizon —
    # via queryset update (auto_now_add ignores assignment; Postgres moves
    # the row across partitions on UPDATE). It must land in DEFAULT.
    year, month = _month_add(now.year, now.month, 8)
    target_ts = datetime(year, month, 15, 12, 0, tzinfo=dt_timezone.utc)
    CodeSubmission.objects.filter(pk=submission.pk).update(submitted_at=target_ts)
    assert _partition_holding(submission) == "groups_codesubmission_default"

    # Extending the horizon past it must relocate the row into its month...
    call_command("ensure_submission_partitions", months_ahead=9, stdout=StringIO())
    assert _partition_holding(submission) == _partition_name(year, month)

    # ...without altering the row itself.
    submission.refresh_from_db()
    assert submission.submitted_at == target_ts
    assert submission.status == "accepted"
