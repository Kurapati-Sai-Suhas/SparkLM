"""
Output provenance (M2 P2.7g-1).

ADDITIVE. One new table plus one trigger. This migration creates no provenance
rows, fabricates no history, and touches no existing table — not Question, not
ReferenceSolution, not hidden_test_cases, not expected_output. There is
deliberately no default and no backfill: a provenance row asserts that an
execution happened, so inventing one during a migration would be inventing the
very evidence the table exists to hold.
"""

import django.db.models.deletion
import groups.models
from django.db import migrations, models

#: Append-only enforcement at the database layer.
#:
#: `save()` guards the ORM path, but `QuerySet.update()`, `bulk_update`,
#: `loaddata` and raw SQL all bypass it — and history that can be quietly
#: rewritten is not provenance. Postgres refuses the write itself, so no write
#: path escapes.
#:
#: `is_authoritative` is deliberately excluded: an execution is a fact, whereas
#: whether its output is the accepted answer is a later decision (P2.7g-2), and
#: recording that decision must not require rewriting the fact.
IMMUTABILITY_TRIGGER = """
CREATE OR REPLACE FUNCTION groups_oracleexecution_append_only()
RETURNS trigger AS $$
BEGIN
    IF NEW.question_id                 IS DISTINCT FROM OLD.question_id
    OR NEW.reference_id                IS DISTINCT FROM OLD.reference_id
    OR NEW.reference_source_hash       IS DISTINCT FROM OLD.reference_source_hash
    OR NEW.language                    IS DISTINCT FROM OLD.language
    OR NEW.case_digest                 IS DISTINCT FROM OLD.case_digest
    OR NEW.input_digest                IS DISTINCT FROM OLD.input_digest
    OR NEW.produced_output             IS DISTINCT FROM OLD.produced_output
    OR NEW.output_digest               IS DISTINCT FROM OLD.output_digest
    OR NEW.execution_contract_version  IS DISTINCT FROM OLD.execution_contract_version
    OR NEW.status                      IS DISTINCT FROM OLD.status
    OR NEW.executed_at                 IS DISTINCT FROM OLD.executed_at
    OR NEW.executor                    IS DISTINCT FROM OLD.executor
    OR NEW.provenance_schema_version   IS DISTINCT FROM OLD.provenance_schema_version
    OR NEW.created_at                  IS DISTINCT FROM OLD.created_at
    THEN
        -- ERRCODE 23514 (check_violation) so this surfaces as Django's
        -- IntegrityError, exactly like the CHECK constraints beside it. A
        -- bare RAISE would arrive as ProgrammingError, and a caller sensibly
        -- catching IntegrityError would miss it.
        RAISE EXCEPTION 'OracleExecution is append-only: only is_authoritative '
                        'may change (attempted on row %)', OLD.id
              USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER groups_oracleexecution_append_only_trg
BEFORE UPDATE ON groups_oracleexecution
FOR EACH ROW EXECUTE FUNCTION groups_oracleexecution_append_only();
"""

DROP_IMMUTABILITY_TRIGGER = """
DROP TRIGGER IF EXISTS groups_oracleexecution_append_only_trg
    ON groups_oracleexecution;
DROP FUNCTION IF EXISTS groups_oracleexecution_append_only();
"""

#: Ownership at the database layer.
#:
#: A reference may only answer for its OWN question. `clean()` and
#: `record_execution` both check it, but neither is reachable from raw SQL, and
#: this is the exact cross-question defect P2.7d's adversarial review found in
#: OracleService. A CHECK constraint cannot express it — the condition spans
#: two tables — so a trigger is the only database-level option.
OWNERSHIP_TRIGGER = """
CREATE OR REPLACE FUNCTION groups_oracleexecution_ownership()
RETURNS trigger AS $$
DECLARE
    owning_question integer;
BEGIN
    SELECT question_id INTO owning_question
    FROM groups_referencesolution WHERE id = NEW.reference_id;

    IF owning_question IS DISTINCT FROM NEW.question_id THEN
        -- 23514 (check_violation): this is a constraint in every sense except
        -- that it spans two tables, which is the only reason it cannot BE a
        -- CHECK. It should behave like one to callers.
        RAISE EXCEPTION 'reference % belongs to question %, not question %; '
                        'provenance may not cross questions',
                        NEW.reference_id, owning_question, NEW.question_id
              USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER groups_oracleexecution_ownership_trg
BEFORE INSERT OR UPDATE ON groups_oracleexecution
FOR EACH ROW EXECUTE FUNCTION groups_oracleexecution_ownership();
"""

DROP_OWNERSHIP_TRIGGER = """
DROP TRIGGER IF EXISTS groups_oracleexecution_ownership_trg
    ON groups_oracleexecution;
DROP FUNCTION IF EXISTS groups_oracleexecution_ownership();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0040_shadow_adaptive_model'),
    ]

    operations = [
        migrations.CreateModel(
            name='OracleExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_source_hash', models.CharField(max_length=64)),
                ('language', models.CharField(max_length=20)),
                ('case_digest', models.CharField(max_length=64)),
                ('input_digest', models.CharField(max_length=64)),
                ('produced_output', models.TextField(blank=True)),
                ('output_digest', models.CharField(max_length=64)),
                ('execution_contract_version', models.CharField(max_length=8)),
                ('status', models.CharField(choices=[('SUCCESS', 'Ran cleanly'), ('FAILED', 'Reference did not run cleanly'), ('TIMEOUT', 'Timed out'), ('ERROR', 'Execution service error'), ('NONDETERMINISTIC', 'Disagreed with an identical run')], max_length=20)),
                ('executed_at', models.DateTimeField()),
                ('executor', models.JSONField(blank=True, default=dict)),
                ('provenance_schema_version', models.PositiveSmallIntegerField(default=1)),
                ('is_authoritative', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='oracle_executions', to='groups.question')),
                ('reference', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='oracle_executions', to='groups.referencesolution')),
            ],
            options={
                'indexes': [models.Index(fields=['reference', '-executed_at'], name='prov_reference_ts_idx'), models.Index(fields=['question', 'case_digest'], name='prov_question_case_idx'), models.Index(fields=['reference_source_hash'], name='prov_source_hash_idx')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('is_authoritative', True)), fields=('question', 'case_digest'), name='one_authoritative_output_per_case'), models.CheckConstraint(condition=models.Q(('output_digest', groups.models.Sha256Hex(models.F('produced_output')))), name='oracle_execution_output_digest_matches')],
            },
        ),
        migrations.RunSQL(OWNERSHIP_TRIGGER, DROP_OWNERSHIP_TRIGGER),
        migrations.RunSQL(IMMUTABILITY_TRIGGER, DROP_IMMUTABILITY_TRIGGER),
    ]
