"""
ReferenceSolution lifecycle and approval provenance (M2 P2.7d).

ADDITIVE. This migration creates no reference solutions, activates none,
touches no Question row, no CodeSubmission row, no hidden test and no expected
output, and executes nothing against Judge0. It changes SHAPE.

One data operation is unavoidable and is deliberately included — see
`_deactivate_unapproved_references` below. It DEACTIVATES; it never approves,
promotes or activates anything, and it fabricates no approval history.
"""

import django.db.models.deletion
import groups.models
from django.conf import settings
from django.db import migrations, models


def _deactivate_unapproved_references(apps, schema_editor):
    """
    Demote any pre-existing active reference to inactive before the
    `reference_active_requires_approval` constraint is added.

    Why this data step exists at all, in a phase whose rule is "migrations
    change shape, commands change content":

    Before P2.7d, `is_active` defaulted to True, so every reference ever
    created is active and — under the new schema — DRAFT. Adding a CHECK
    constraint to a table containing rows that violate it FAILS. There are
    exactly two ways to make the migration applicable: demote the rows, or
    approve them. Approving them would be fabricating a human review that
    never happened, on the very data this whole milestone exists because
    nobody has ever reviewed. So: demote.

    Nothing is lost. The row, its `source_code`, its language and its
    timestamps are all preserved; only the claim "this is the canonical
    grading truth" is withdrawn — a claim no human ever made. Reactivating
    is a normal lifecycle operation once someone has actually approved it.

    The reverse is a deliberate no-op: we do not know which rows were active
    before, and inventing that on rollback would be the same fabrication in
    the other direction.
    """
    ReferenceSolution = apps.get_model("groups", "ReferenceSolution")
    demoted = ReferenceSolution.objects.filter(is_active=True).update(
        is_active=False
    )
    if demoted:
        print(
            f"\n  P2.7d: deactivated {demoted} pre-existing reference "
            f"solution(s). They are retained as DRAFT and can be activated "
            f"again once a human has approved them."
        )


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0038_codesubmission_adaptive_eligible_question_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='referencesolution',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='referencesolution',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='approved_reference_solutions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='referencesolution',
            name='review_state',
            field=models.CharField(choices=[('DRAFT', 'Draft'), ('IN_REVIEW', 'In review'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='DRAFT', max_length=20),
        ),
        migrations.AddField(
            model_name='referencesolution',
            name='source_hash',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='referencesolution',
            name='is_active',
            field=models.BooleanField(default=False),
        ),
        # Must run after the fields exist and before any constraint is added.
        migrations.RunPython(
            _deactivate_unapproved_references,
            migrations.RunPython.noop,
            elidable=False,
        ),
        migrations.AddConstraint(
            model_name='referencesolution',
            constraint=models.CheckConstraint(condition=models.Q(('is_active', False), ('review_state', 'APPROVED'), _connector='OR'), name='reference_active_requires_approval'),
        ),
        migrations.AddConstraint(
            model_name='referencesolution',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('review_state', 'APPROVED'), ('approved_by__isnull', False), ('approved_at__isnull', False), ('source_hash__isnull', False)), models.Q(models.Q(('review_state', 'APPROVED'), _negated=True), ('approved_by__isnull', True), ('approved_at__isnull', True), ('source_hash__isnull', True)), _connector='OR'), name='reference_approval_provenance'),
        ),
        migrations.AddConstraint(
            model_name='referencesolution',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('review_state', 'APPROVED'), _negated=True), ('source_hash', groups.models.Sha256Hex(models.F('source_code'))), _connector='OR'), name='reference_approved_source_unmodified'),
        ),
    ]
