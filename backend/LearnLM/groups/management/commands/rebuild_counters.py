"""
rebuild_counters — recompute denormalised submission counters from source
(M4 Phase B).

`UserCodingProfile.total_submissions` and `successful_submissions` are
incremented in application code on each graded submission. Nothing
decrements them, so deleting a question cascades its submissions away and
leaves the counters overstated — known debt (D13), and the reason the M4
review declined to add two more hand-maintained counters to `Question`.

The counters are a **cache**, not a source of truth: `CodeSubmission` records
user, question, status and timestamp for every attempt, so both values are
exactly derivable. This makes that explicit and repairable rather than
leaving drift to accumulate silently.

Read-only by default. Drift is reported and nothing is written unless
--apply is passed, because a counter repair that runs unattended and
silently rewrites learner-visible numbers is worse than the drift.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from groups.models import CodeSubmission, UserCodingProfile


class Command(BaseCommand):
    help = "Recompute UserCodingProfile submission counters from CodeSubmission."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the corrected values. Without this, only reports drift.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        # One grouped query rather than two per profile.
        truth = {
            row["user_id"]: (row["total"], row["accepted"])
            # EVERY submission, deliberately unfiltered (M2 P2.8a).
            #
            # P2.7c added an `adaptive_eligible=True` filter here on the
            # reasoning that "these counters are read by the learner model".
            # They are not: `success_rate` is returned by CodeSubmitView and
            # CodingProfileView and read by nothing in routing, Elo, mastery,
            # telemetry or the ML tensor. They are display-only.
            #
            # Worse, the live writer in ProgressionService.apply_submission
            # increments them UNCONDITIONALLY, so the filtered rebuild
            # disagreed with every row it was supposed to repair — running
            # `--apply` would have reported every profile as drifted and reset
            # each learner's visible activity to the eligible subset, which is
            # currently zero.
            #
            # These count ACTIVITY, not EVIDENCE. A learner who solved forty
            # unverified problems really did make forty submissions, and
            # hiding that from their own profile because our verification
            # backlog is long would be wrong.
            for row in CodeSubmission.objects.all()
            .values("user_id").annotate(
                total=Count("id"),
                accepted=Count("id", filter=Q(status="accepted")),
            )
        }

        drifted = []
        for profile in UserCodingProfile.objects.select_related("user").iterator():
            total, accepted = truth.get(profile.user_id, (0, 0))
            if profile.total_submissions != total or profile.successful_submissions != accepted:
                drifted.append((profile, total, accepted))

        if not drifted:
            self.stdout.write(self.style.SUCCESS("No counter drift detected."))
            return "0"

        for profile, total, accepted in drifted:
            self.stdout.write(
                f"  {profile.user.username}: "
                f"total {profile.total_submissions} -> {total}, "
                f"accepted {profile.successful_submissions} -> {accepted}"
            )

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"{len(drifted)} profile(s) drifted. Re-run with --apply to correct."
            ))
            return str(len(drifted))

        with transaction.atomic():
            for profile, total, accepted in drifted:
                profile.total_submissions = total
                profile.successful_submissions = accepted
                profile.save(update_fields=["total_submissions", "successful_submissions"])

        self.stdout.write(self.style.SUCCESS(f"Corrected {len(drifted)} profile(s)."))
        return str(len(drifted))
