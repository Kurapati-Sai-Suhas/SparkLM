"""
Point-in-time Glicko history (M2 P2.9b).

ADDITIVE. One new table. No existing table is touched — not LearnerTopicSkill,
not QuestionSkill, not CodeSubmission, not Question. No column is altered, no
default is applied to existing data, and there is **no backfill**.

The absence of a backfill is the point. Every interaction that happened before
this migration has NO recorded Glicko state, and that state is not recoverable:
`glicko.rate` consumes `periods_inactive`, derived from wall-clock gaps at the
moment the update ran, and nothing recorded when that was. A replay would
produce a plausible history rather than the one that occurred, and a plausible
history presented as fact is worse than an admitted gap.

Historical interactions therefore stay historical-unknown, permanently.
Point-in-time Glicko exists only from this migration forward.

`submission_id_value` is a plain BigIntegerField rather than a ForeignKey.
MEASURED, not assumed: `groups_codesubmission` is RANGE PARTITIONED by
`submitted_at`, so its primary key is `(id, submitted_at)`, and PostgreSQL
rejects a reference to `id` alone with "there is no unique constraint matching
given keys for referenced table".
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0042_question_approval'),
    ]

    operations = [
        migrations.CreateModel(
            name='GlickoSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('submission_id_value', models.BigIntegerField(unique=True)),
                ('submission_submitted_at', models.DateTimeField()),
                ('learner_rating_before', models.FloatField()),
                ('learner_rd_before', models.FloatField()),
                ('learner_volatility_before', models.FloatField()),
                ('learner_periods_inactive', models.FloatField()),
                ('question_rating_before', models.FloatField()),
                ('question_rd_before', models.FloatField()),
                ('question_volatility_before', models.FloatField()),
                ('question_periods_inactive', models.FloatField()),
                ('learner_rating_after', models.FloatField()),
                ('learner_rd_after', models.FloatField()),
                ('question_rating_after', models.FloatField()),
                ('question_rd_after', models.FloatField()),
                ('recorded_at', models.DateTimeField()),
                ('glicko_version', models.CharField(max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='glicko_snapshots', to='groups.question')),
                ('topic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='glicko_snapshots', to='groups.topic')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='glicko_snapshots', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['user', 'topic', 'recorded_at'], name='glicko_snap_user_topic_idx'), models.Index(fields=['submission_submitted_at'], name='glicko_snap_submitted_idx')],
            },
        ),
    ]
