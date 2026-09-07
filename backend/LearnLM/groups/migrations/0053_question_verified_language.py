"""
Record which language produced a question's Oracle verification (M2 P2.36).

── The backfill rule ───────────────────────────────────────────────────────

`verified_language` is written ONLY from the language of the reference that
actually produced the verification, and the migration derives that from the
question's canonical reference — active, APPROVED, and with a source_hash,
which is the same rule `ReferenceSolution.is_canonical` applies.

It does NOT default to "python". Every production reference happens to be
Python today, so a blanket default would have produced the right answer for
the wrong reason and would have been silently wrong the first time a Java
reference existed.

── Why it refuses instead of guessing ──────────────────────────────────────

An ORACLE_VERIFIED row whose language cannot be derived has exactly two safe
treatments: leave it verified with an unknown language, or demote it. The
first violates the constraint this migration adds; the second changes trust
state, which is `question_promote`'s and `question_demote`'s authority and
not a migration's.

So it raises, naming the questions, and asks an operator to re-verify them.
Verified against production before writing: all six ORACLE_VERIFIED questions
resolve to exactly one canonical reference, all Python.
"""

from django.db import migrations, models

ORACLE_VERIFIED = "ORACLE_VERIFIED"
REVIEW_APPROVED = "APPROVED"


def backfill_verified_language(apps, schema_editor):
    Question = apps.get_model("groups", "Question")
    alias = schema_editor.connection.alias

    unresolved = []
    for question in Question.objects.using(alias).filter(
            trust_state=ORACLE_VERIFIED).iterator():
        canonical = [
            reference for reference in
            question.reference_solutions.using(alias).filter(
                is_active=True, review_state=REVIEW_APPROVED)
            if reference.source_hash
        ]
        # Exactly one, matching the live one-canonical-oracle contract. Two
        # active references is a configuration error the oracle already
        # refuses, and it is not this migration's place to pick between them.
        if len(canonical) != 1 or not (canonical[0].language or "").strip():
            unresolved.append(question.pk)
            continue

        question.verified_language = canonical[0].language.strip().lower()
        question.save(using=alias, update_fields=["verified_language"])

    if unresolved:
        raise RuntimeError(
            "cannot derive verified_language for ORACLE_VERIFIED question(s) "
            f"{sorted(unresolved)}: each needs exactly one active, approved "
            "reference. Refusing to guess a language for grading truth, and "
            "refusing to demote — re-verify these questions, or demote them "
            "with question_demote, then re-run this migration."
        )


def clear_verified_language(apps, schema_editor):
    """Reverse: the column is dropped by the AddField reversal anyway."""
    Question = apps.get_model("groups", "Question")
    Question.objects.using(schema_editor.connection.alias).update(
        verified_language=None)


class Migration(migrations.Migration):

    dependencies = [
        ('groups', '0052_recommendation_exposure_trust'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='verified_language',
            field=models.CharField(blank=True, help_text='Language of the reference that produced the current ORACLE_VERIFIED state. Null unless verified.', max_length=20, null=True),
        ),
        # Between the field and the constraint, on purpose: the constraint
        # makes ORACLE_VERIFIED-without-a-language unrepresentable, so every
        # existing verified row must be resolved before it is applied.
        migrations.RunPython(backfill_verified_language,
                             clear_verified_language),
        migrations.AddConstraint(
            model_name='question',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('trust_state', 'ORACLE_VERIFIED'), _negated=True), models.Q(('verified_language__isnull', False), models.Q(('verified_language', ''), _negated=True)), _connector='OR'), name='question_oracle_verified_requires_language'),
        ),
    ]
