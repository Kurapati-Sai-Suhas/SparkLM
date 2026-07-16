# Frozen architecture §4.4 index catalog — the two non-partitioned tables.
# CodeSubmission's three catalog indexes are created by migration 0032 on
# the range-partitioned parent table.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0030_agenticcoachlog_hint_source_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="question",
            index=models.Index(
                fields=["topic", "base_difficulty"],
                name="question_topic_diff_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="recommendationlog",
            index=models.Index(
                fields=["user", "problem_id", "-created_at"],
                name="reclog_user_prob_ts_idx",
            ),
        ),
    ]
