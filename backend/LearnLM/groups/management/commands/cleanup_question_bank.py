"""
Deletes junk question-bank data discovered during the taxonomy audit:
- Topics with blank/whitespace names (85 orphaned placeholder questions
  were sitting under one of these).
- Optionally, whole topic categories that aren't part of the DSA product
  (e.g. "Database", "Shell", "Concurrency") so reseed_questions doesn't
  burn LLM quota on them.

Dry-run by default — prints what would be deleted. Pass --apply to delete.

Usage:
    python manage.py cleanup_question_bank
    python manage.py cleanup_question_bank --apply
    python manage.py cleanup_question_bank --topics "Database,Shell,Concurrency" --apply
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from common.environment import require_disposable_environment
from groups.models import Topic


class Command(BaseCommand):
    help = 'Deletes blank-named topics (and optionally listed topics) with all their questions. Dry-run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete. Without this flag, only reports what would be deleted.")
        parser.add_argument("--topics", type=str, default="",
                            help='Comma-separated topic names to also purge, e.g. "Database,Shell,Concurrency".')

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        # Dry-run needs no gate — it deletes nothing. --apply cascades through
        # Topic -> Question -> CodeSubmission, so it does (M2 P2.5).
        if apply_changes:
            require_disposable_environment(
                'cleanup_question_bank --apply (deletes topics and their questions)',
                acknowledged=True,
            )
        extra_names = [t.strip() for t in options["topics"].split(",") if t.strip()]

        targets = [t for t in Topic.objects.all() if not t.name.strip()]
        for name in extra_names:
            topic = Topic.objects.filter(name__iexact=name).first()
            if topic:
                targets.append(topic)
            else:
                self.stdout.write(self.style.WARNING(f"Topic not found, skipping: {name!r}"))

        if not targets:
            self.stdout.write(self.style.SUCCESS("Nothing to clean up."))
            return

        total_questions = 0
        for topic in targets:
            n = topic.question_set.count()
            total_questions += n
            label = topic.name.strip() or "<blank name>"
            self.stdout.write(f"  {label}: {n} question(s)")

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — would delete {len(targets)} topic(s) and {total_questions} question(s). "
                "Re-run with --apply to delete."
            ))
            return

        with transaction.atomic():
            for topic in targets:
                topic.delete()  # Question.topic cascades

        self.stdout.write(self.style.SUCCESS(
            f"Deleted {len(targets)} topic(s) and {total_questions} question(s)."
        ))
