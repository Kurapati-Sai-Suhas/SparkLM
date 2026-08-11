"""
recompute_mastery — self-healing repair for UserTopicMastery.accuracy/reviews.

accuracy and reviews are maintained incrementally on every submission
(services.ProgressionService._apply_sm2_update). That is correct for all
submissions made through the current, row-locked pipeline (verified live:
a real accepted submission moves accuracy from 0.0 to exactly 1/17). But
rows created or touched during earlier testing — across multiple Vercel
preview deployments, admin edits, or pre-M4 code — can drift from the
raw CodeSubmission history, which is the actual ground truth.

This command recomputes accuracy and reviews for every (user, topic) pair
directly from CodeSubmission rows (count + accepted-count), leaving
hlr_halflife and last_practiced untouched (those aren't reconstructable
from history alone — only a fresh submission legitimately advances them).
Idempotent: safe to run any time; a clean account is a no-op.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from groups.models import CodeSubmission, UserTopicMastery


class Command(BaseCommand):
    help = "Recompute UserTopicMastery.accuracy/reviews from real submission history."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                             help="Show what would change without writing.")
        parser.add_argument("--username", type=str, default=None,
                             help="Only repair this user (default: all users).")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        username = options["username"]

        truth = (
            # adaptive_eligible only (M2 P2.7c): mastery is learner-model state.
            CodeSubmission.objects.filter(question__isnull=False, adaptive_eligible=True)
            .values("user_id", "question__topic_id")
            .annotate(
                total=Count("id"),
                accepted=Count("id", filter=Q(status="accepted")),
            )
        )
        if username:
            truth = truth.filter(user__username=username)

        changed = 0
        checked = 0
        for row in truth:
            checked += 1
            real_reviews = row["total"]
            real_accuracy = round(row["accepted"] / real_reviews, 6) if real_reviews else 0.0

            mastery = UserTopicMastery.objects.filter(
                user_id=row["user_id"], topic_id=row["question__topic_id"]
            ).first()
            if mastery is None:
                continue  # no mastery row yet (e.g. onboarding-only topic) — nothing to repair

            if mastery.reviews == real_reviews and abs(mastery.accuracy - real_accuracy) < 1e-6:
                continue

            self.stdout.write(
                f"user={row['user_id']} topic={row['question__topic_id']}: "
                f"reviews {mastery.reviews}->{real_reviews}, "
                f"accuracy {mastery.accuracy:.4f}->{real_accuracy:.4f}"
            )
            changed += 1
            if not dry_run:
                with transaction.atomic():
                    locked = UserTopicMastery.objects.select_for_update().get(pk=mastery.pk)
                    locked.reviews = real_reviews
                    locked.accuracy = real_accuracy
                    locked.save(update_fields=["reviews", "accuracy"])

        verb = "would repair" if dry_run else "repaired"
        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} (user, topic) pairs; {verb} {changed}."
        ))
