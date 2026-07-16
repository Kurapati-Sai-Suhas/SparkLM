"""
Partition maintenance for the range-partitioned groups_codesubmission table
(frozen architecture §4.3: monthly partitions, DEFAULT partition as the
safety net).

Migration groups.0032 creates partitions through three months past its own
run date; this command maintains the horizon from then on. It is idempotent
and safe to run on any schedule (monthly is enough; after long downtime it
also heals). Each missing month in [current .. current + N] is built
standalone, any rows stranded in the DEFAULT partition are moved into it
with a single atomic DELETE..RETURNING statement, and the table is then
attached — one transaction per month. ATTACH itself re-verifies that no
DEFAULT row still falls inside the new range, so even a row committed
mid-flight can only cause a clean rollback (rerun the command), never a
lost or duplicated row.

All identifiers are built from validated integers (never from input
strings); date bounds in DML go through bind parameters.
"""

from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

PARENT = "groups_codesubmission"
DEFAULT_PARTITION = "groups_codesubmission_default"

# Guard against a fat-fingered --months-ahead ("2027" instead of "27")
# creating thousands of tables in one run.
MAX_MONTHS_AHEAD = 120


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
        if not 0 <= months_ahead <= MAX_MONTHS_AHEAD:
            raise CommandError(
                f"--months-ahead must be between 0 and {MAX_MONTHS_AHEAD}"
            )
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
            if dry_run:
                strays = self._count_default_rows(low, high)
                self.stdout.write(
                    f"[dry-run] would create {name}"
                    + (f" and move {strays} row(s) from DEFAULT" if strays else "")
                )
                moved += strays
            else:
                moved_rows = self._create_partition(name, low, high)
                self.stdout.write(
                    f"created {name}"
                    + (f" (moved {moved_rows} row(s) from DEFAULT)" if moved_rows else "")
                )
                moved += moved_rows
            created += 1
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
    def _create_partition(name, low, high):
        """
        Build the month standalone, move any stranded DEFAULT rows into it,
        then attach. Returns the number of rows moved.

        The move MUST be one DELETE..RETURNING statement: a separate
        copy-then-delete pair runs on two READ COMMITTED snapshots, and a
        row committed into DEFAULT between them would be deleted without
        ever being copied. ATTACH validates that no DEFAULT row still falls
        in the new range (and builds the parent's partitioned indexes,
        constraints, and FKs on the new member), so concurrent traffic can
        only produce a clean rollback, never data loss.
        """
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE {name} (LIKE {PARENT} INCLUDING DEFAULTS)"
            )
            cursor.execute(
                f"WITH moved AS ("
                f" DELETE FROM {DEFAULT_PARTITION}"
                "  WHERE submitted_at >= %s AND submitted_at < %s"
                "  RETURNING *"
                f") INSERT INTO {name} SELECT * FROM moved",
                [low, high],
            )
            moved = cursor.rowcount
            cursor.execute(
                f"ALTER TABLE {PARENT} ATTACH PARTITION {name} "
                f"FOR VALUES FROM ('{low}') TO ('{high}')"
            )
        return moved
