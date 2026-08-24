"""
Question-level approval (M2 P2.7g-3).

ADDITIVE. One new table, one CHECK constraint, and one READ-ONLY pre-flight
assertion. This migration creates no approval rows, promotes no question,
publishes nothing, and writes to no existing table — not Question, not
hidden_test_cases, not expected_output.

There is deliberately no backfill. An approval asserts that a named person
looked at a specific artifact at a specific time; manufacturing one in a
migration would manufacture the human judgement the entire milestone exists to
require.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def assert_no_draft_oracle_verified(apps, schema_editor):
    """
    Refuse to proceed if any question is already DRAFT + ORACLE_VERIFIED.

    READ-ONLY. It runs one COUNT and either returns or raises; there is no
    UPDATE, and no branch that repairs anything.

    The repair is deliberately withheld. `trust_state` has no writer in this
    codebase, so every row SHOULD be UNVERIFIED — but P2.7e has never run
    against production, so that is an inference, not a measurement. If the
    inference is wrong, something wrote trust state through a path nobody has
    audited, and the correct response is to stop and investigate that, not to
    let a migration quietly rewrite grading trust at 3am while it holds a lock
    on the questions table.

    Django would raise on its own when the constraint failed to validate. This
    exists to make the failure say what is actually wrong and what to do next.
    """
    Question = apps.get_model("groups", "Question")
    offending = Question.objects.filter(
        status="DRAFT", trust_state="ORACLE_VERIFIED")
    count = offending.count()
    if count:
        sample = list(offending.values_list("pk", flat=True)[:20])
        raise RuntimeError(
            f"{count} question(s) are DRAFT + ORACLE_VERIFIED, which this "
            f"migration is about to forbid. Question ids (first 20): {sample}. "
            f"Nothing has been modified. `trust_state` has no writer in this "
            f"codebase, so these rows were written by an unaudited path — "
            f"investigate their provenance before deciding whether they should "
            f"be demoted to UNVERIFIED or their status advanced past DRAFT. "
            f"This migration will not make that decision for you."
        )


def noop_reverse(apps, schema_editor):
    """Nothing to undo — the forward operation only reads."""


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0041_output_provenance'),
    ]

    operations = [
        # Before the constraint, so the failure is a legible message about
        # data rather than an opaque constraint-validation error.
        migrations.RunPython(assert_no_draft_oracle_verified, noop_reverse),
        migrations.CreateModel(
            name='QuestionApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_source_hash', models.CharField(max_length=64)),
                ('artifact_digest', models.CharField(max_length=64)),
                ('artifact_schema_version', models.PositiveSmallIntegerField(default=1)),
                ('quality_outcome', models.JSONField(default=dict)),
                ('executed_at', models.DateTimeField(blank=True, null=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField()),
                ('promoted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name='question',
            constraint=models.CheckConstraint(condition=models.Q(('status', 'DRAFT'), ('trust_state', 'ORACLE_VERIFIED'), _negated=True), name='question_draft_cannot_be_oracle_verified'),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='approved_by',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='question_approvals', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='executed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='oracle_executions_operated', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='promoted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='question_promotions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='question',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='approvals', to='groups.question'),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='reference',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='question_approvals', to='groups.referencesolution'),
        ),
        migrations.AddField(
            model_name='questionapproval',
            name='reviewed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='question_reviews', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='questionapproval',
            index=models.Index(fields=['question', '-approved_at'], name='approval_question_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='questionapproval',
            index=models.Index(fields=['reference_source_hash'], name='approval_source_hash_idx'),
        ),
        migrations.AddIndex(
            model_name='questionapproval',
            index=models.Index(fields=['artifact_digest'], name='approval_digest_idx'),
        ),
    ]
