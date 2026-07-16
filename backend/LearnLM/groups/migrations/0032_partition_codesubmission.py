# Convert groups_codesubmission to a declarative range-partitioned table
# (monthly, on submitted_at) per frozen architecture §4.3, and create the
# §4.4 CodeSubmission indexes on the partitioned parent.
#
# Postgres facts that shaped this migration (recorded in
# docs/ARCHITECTURE_V2.md §16, amendments M2-1..M2-3):
#
# * A partitioned table's primary key must contain the partition key, so
#   the DB-level PK becomes (id, submitted_at). id uniqueness is still
#   guaranteed by its sequence, and the ORM keys on id alone (lookups use
#   the PK index's leading column), so application behavior is unchanged.
#   Nothing in the schema holds a foreign key to CodeSubmission.
# * PostgreSQL < 17 forbids IDENTITY columns on partitioned tables, so id
#   keeps its numbering through an owned sequence + column DEFAULT.
# * The old single-column user_id index is not recreated: user_id equality
#   scans are served by the leading column of subm_user_ts_idx and
#   subm_user_status_idx. question_id keeps its single-column index
#   because no composite in the catalog leads with it.
#
# Partitions are created from the oldest existing data month through three
# months ahead of the migration date, plus a DEFAULT partition so an
# insert can never fail for lack of a partition. The
# ensure_submission_partitions command (common app) maintains the horizon
# from then on. The conversion is atomic: any failure rolls back to the
# untouched original table.

from datetime import datetime, timezone

from django.db import migrations, models

MONTHS_AHEAD = 3

COLUMNS = (
    "id, language, code, status, execution_time_ms, "
    "memory_used_kb, submitted_at, user_id, question_id"
)


def _month_add(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _partition_name(year, month):
    return f"groups_codesubmission_{year:04d}_{month:02d}"


def _month_bounds(year, month):
    next_year, next_month = _month_add(year, month, 1)
    return (
        f"'{year:04d}-{month:02d}-01 00:00:00+00'",
        f"'{next_year:04d}-{next_month:02d}-01 00:00:00+00'",
    )


def _partition_codesubmission(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT min(submitted_at) FROM groups_codesubmission")
        oldest = cursor.fetchone()[0]

    # Partition range: oldest data month (UTC) .. migration month + 3.
    now = datetime.now(tz=timezone.utc)
    start_year, start_month = now.year, now.month
    if oldest is not None:
        oldest = oldest.astimezone(timezone.utc)
        if (oldest.year, oldest.month) < (start_year, start_month):
            start_year, start_month = oldest.year, oldest.month
    end_year, end_month = _month_add(now.year, now.month, MONTHS_AHEAD)

    execute = schema_editor.execute
    execute("ALTER TABLE groups_codesubmission RENAME TO groups_codesubmission_v1flat")
    execute(
        "CREATE TABLE groups_codesubmission ("
        " id bigint NOT NULL,"
        " language varchar(20) NOT NULL,"
        " code text NOT NULL,"
        " status varchar(20) NOT NULL,"
        " execution_time_ms integer NULL,"
        " memory_used_kb integer NULL,"
        " submitted_at timestamptz NOT NULL,"
        " user_id bigint NOT NULL,"
        " question_id bigint NULL"
        ") PARTITION BY RANGE (submitted_at)"
    )

    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        low, high = _month_bounds(year, month)
        execute(
            f"CREATE TABLE {_partition_name(year, month)} "
            f"PARTITION OF groups_codesubmission "
            f"FOR VALUES FROM ({low}) TO ({high})"
        )
        year, month = _month_add(year, month, 1)
    execute(
        "CREATE TABLE groups_codesubmission_default "
        "PARTITION OF groups_codesubmission DEFAULT"
    )

    execute(
        f"INSERT INTO groups_codesubmission ({COLUMNS}) "
        f"SELECT {COLUMNS} FROM groups_codesubmission_v1flat"
    )
    # Dropping the old table also drops its column-owned identity sequence,
    # freeing both the sequence name and the index/constraint names below.
    execute("DROP TABLE groups_codesubmission_v1flat")

    execute(
        "CREATE SEQUENCE groups_codesubmission_id_seq AS bigint "
        "OWNED BY groups_codesubmission.id"
    )
    execute(
        "ALTER TABLE groups_codesubmission ALTER COLUMN id "
        "SET DEFAULT nextval('groups_codesubmission_id_seq')"
    )
    execute(
        "SELECT setval('groups_codesubmission_id_seq', "
        "COALESCE((SELECT max(id) FROM groups_codesubmission), 0) + 1, false)"
    )

    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmission_pkey PRIMARY KEY (id, submitted_at)"
    )
    # Same FK names and semantics (DEFERRABLE, ORM-managed cascade) as the
    # original Django-generated constraints.
    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmission_user_id_d21898dd_fk_groups_user_id "
        "FOREIGN KEY (user_id) REFERENCES groups_user(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmissio_question_id_16789a40_fk_groups_qu "
        "FOREIGN KEY (question_id) REFERENCES groups_question(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )

    execute(
        "CREATE INDEX groups_codesubmission_question_id_16789a40 "
        "ON groups_codesubmission (question_id)"
    )
    execute(
        "CREATE INDEX subm_user_ts_idx "
        "ON groups_codesubmission (user_id, submitted_at DESC)"
    )
    execute(
        "CREATE INDEX subm_user_q_ts_idx "
        "ON groups_codesubmission (user_id, question_id, submitted_at DESC)"
    )
    execute(
        "CREATE INDEX subm_user_status_idx "
        "ON groups_codesubmission (user_id, status)"
    )


def _unpartition_codesubmission(apps, schema_editor):
    execute = schema_editor.execute
    execute("ALTER TABLE groups_codesubmission RENAME TO groups_codesubmission_part")
    execute(
        "CREATE TABLE groups_codesubmission ("
        " id bigint NOT NULL,"
        " language varchar(20) NOT NULL,"
        " code text NOT NULL,"
        " status varchar(20) NOT NULL,"
        " execution_time_ms integer NULL,"
        " memory_used_kb integer NULL,"
        " submitted_at timestamptz NOT NULL,"
        " user_id bigint NOT NULL,"
        " question_id bigint NULL"
        ")"
    )
    execute(
        f"INSERT INTO groups_codesubmission ({COLUMNS}) "
        f"SELECT {COLUMNS} FROM groups_codesubmission_part"
    )
    # Frees the pkey/index/sequence names for the recreations below.
    execute("DROP TABLE groups_codesubmission_part")
    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmission_pkey PRIMARY KEY (id)"
    )
    execute(
        "ALTER TABLE groups_codesubmission ALTER COLUMN id "
        "ADD GENERATED BY DEFAULT AS IDENTITY"
    )
    execute(
        "SELECT setval(pg_get_serial_sequence('groups_codesubmission', 'id'), "
        "COALESCE((SELECT max(id) FROM groups_codesubmission), 0) + 1, false)"
    )
    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmission_user_id_d21898dd_fk_groups_user_id "
        "FOREIGN KEY (user_id) REFERENCES groups_user(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    execute(
        "ALTER TABLE groups_codesubmission ADD CONSTRAINT "
        "groups_codesubmissio_question_id_16789a40_fk_groups_qu "
        "FOREIGN KEY (question_id) REFERENCES groups_question(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    execute(
        "CREATE INDEX groups_codesubmission_user_id_d21898dd "
        "ON groups_codesubmission (user_id)"
    )
    execute(
        "CREATE INDEX groups_codesubmission_question_id_16789a40 "
        "ON groups_codesubmission (question_id)"
    )


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ("groups", "0031_add_index_catalog"),
    ]

    operations = [
        migrations.RunPython(_partition_codesubmission, _unpartition_codesubmission),
        # The three §4.4 CodeSubmission indexes exist physically on the
        # partitioned parent (created above); record them in model state so
        # models.py and the schema agree and makemigrations stays clean.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddIndex(
                    model_name="codesubmission",
                    index=models.Index(
                        fields=["user", "-submitted_at"],
                        name="subm_user_ts_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name="codesubmission",
                    index=models.Index(
                        fields=["user", "question", "-submitted_at"],
                        name="subm_user_q_ts_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name="codesubmission",
                    index=models.Index(
                        fields=["user", "status"],
                        name="subm_user_status_idx",
                    ),
                ),
            ],
        ),
    ]
