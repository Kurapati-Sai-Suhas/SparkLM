"""
M4 Phase A — JWT revocation counter.

Purely additive with a default, so it is safe under the expand-migrate-
contract rule the architecture requires (§15): old code ignores the column,
new code reads it, and no backfill is needed because every existing user
starts at version 0 — which matches the "missing claim is valid" branch in
common/authentication.py, so no one is logged out by this migration.

Rolling back the CODE does not require rolling back this migration, and it
should not be reversed: dropping the column while any deployed process
still reads it turns every authenticated request into a 500.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0032_partition_codesubmission'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='token_version',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
