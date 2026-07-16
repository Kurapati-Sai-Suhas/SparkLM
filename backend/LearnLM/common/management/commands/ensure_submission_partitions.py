"""
Partition maintenance for the range-partitioned groups_codesubmission table
(frozen architecture §4.3: monthly partitions, DEFAULT partition as the
safety net).

Migration groups.0032 creates partitions through three months past its own
run date; this command maintains the horizon from then on. It is idempotent
and safe to run on any schedule (monthly is enough; after long downtime it
also heals). For each missing month in [current .. current + N]:

* If the DEFAULT partition holds no rows for that month, the partition is
  created directly.
* If strays landed in DEFAULT (possible only when the horizon lapsed), the
  month's rows are moved into a standalone table which is then attached —
  one transaction per month, so a failure never loses or duplicates rows.

All identifiers are built from validated integers (never from input
strings); date bounds in DML go through bind parameters.
"""

from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

PARENT = "groups_codesubmission"
DEFAULT_PARTITION = "groups_codesubmission_default"


def _month_add(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _partition_name(year, month):
    return f"{PARENT}_{year:04d}_{month:02d}"


def _month_bounds(year, month):
    next_year, next_month = _month_add(year, month, 1)
    return (
        f"{year:04d}-{month:02d}-01 00:00:00+00",
        f"{next_year:04d}-{next_month:02d}-01 00:00:00+00",
    )


class Command(BaseCommand):
    help = (
        "Create upcoming monthly partitions of groups_codesubmission and "
        "relocate any rows stranded in the DEFAULT partition. Idempotent; "
        "run monthly (frozen architecture §4.3)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--months-ahead",
            type=int,
            default=3,
            help="How many months past the current one to keep partitioned "
            "(default: 3).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created/moved without touching the "
            "database.",
        )

    def handle(self, *args, **options):
        months_ahead = options["months_ahead"]
        if months_ahead < 0:
            raise CommandError("--months-ahead must be >= 0")
        dry_run = options["dry_run"]

        existing = self._existing_partitions()
        if DEFAULT_PARTITION not in existing:
            raise CommandError(
                f"{PARENT} has no DEFAULT partition — has migration "
                "groups.0032 been applied?"
            )

        now = datetime.now(tz=timezone.utc)
        created = 0
        moved = 0
        year, month = now.year, now.month
        for _ in range(months_ahead + 1):
            name = _partition_name(year, month)
            if name in existing:
                year, month = _month_add(year, month, 1)
                continue
            low, high = _month_bounds(year, month)
            strays = self._count_default_rows(low, high)
            if dry_run:
                self.stdout.write(
                    f"[dry-run] would create {name}"
                    + (f" and move {strays} row(s) from DEFAULT" if strays else "")
                )
            else:
                self._create_partition(name, low, high, strays)
                self.stdout.write(
                    f"created {name}"
                    + (f" (moved {strays} row(s) from DEFAULT)" if strays else "")
                )
            created += 1
            moved += strays
            year, month = _month_add(year, month, 1)

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Created {created} partition(s), moved {moved} row(s) "
            "out of DEFAULT."
        )

        remaining = self._count_default_rows()
        if remaining:
            self.stdout.write(
                self.style.WARNING(
                    f"{remaining} row(s) remain in {DEFAULT_PARTITION} beyond "
                    "the horizon — rerun with a larger --months-ahead to "
                    "relocate them."
                )
            )

    @staticmethod
    def _existing_partitions():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT child.relname"
                " FROM pg_inherits"
                " JOIN pg_class child ON child.oid = pg_inherits.inhrelid"
                " JOIN pg_class parent ON parent.oid = pg_inherits.inhparent"
                " WHERE parent.relname = %s AND parent.relkind = 'p'",
                [PARENT],
            )
            names = {row[0] for row in cursor.fetchall()}
        if not names:
            raise CommandError(
                f"{PARENT} is not a partitioned table — has migration "
                "groups.0032 been applied?"
            )
        return names

    @staticmethod
    def _count_default_rows(low=None, high=None):
        sql = f"SELECT count(*) FROM {DEFAULT_PARTITION}"
        params = []
        if low is not None:
            sql += " WHERE submitted_at >= %s AND submitted_at < %s"
            params = [low, high]
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()[0]

    @staticmethod
    def _create_partition(name, low, high, strays):
        with transaction.atomic(), connection.cursor() as cursor:
            if strays == 0:
                cursor.execute(
                    f"CREATE TABLE {name} PARTITION OF {PARENT} "
                    f"FOR VALUES FROM ('{low}') TO ('{high}')"
                )
                return
            # DEFAULT holds rows for this month, so a direct PARTITION OF
            # would be rejected. Build the month standalone, move the rows,
            # then attach (attach validates bounds and builds the parent's
            # partitioned indexes/constraints on the new member).
            cursor.execute(
                f"CREATE TABLE {name} (LIKE {PARENT} INCLUDING DEFAULTS)"
            )
            cursor.execute(
                f"INSERT INTO {name} SELECT * FROM {DEFAULT_PARTITION} "
                "WHERE submitted_at >= %s AND submitted_at < %s",
                [low, high],
            )
            cursor.execute(
                f"DELETE FROM {DEFAULT_PARTITION} "
                "WHERE submitted_at >= %s AND submitted_at < %s",
                [low, high],
            )
            cursor.execute(
                f"ALTER TABLE {PARENT} ATTACH PARTITION {name} "
                f"FOR VALUES FROM ('{low}') TO ('{high}')"
            )
