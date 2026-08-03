"""
calculate_decay — nightly inactivity-decay sweep.

Rewritten during Phase 0 repair:
- The old version imported groups.engines.ebbinghaus, a module that never
  existed in the repo, so the command crashed on import.
- It also multiplied elo_rating (a ~1200-scale number) by a 0..1 retention
  probability with no checkpoint, which would have slashed every inactive
  user toward the 800 floor and compounded the penalty on every run.

This version delegates to EloEngine.apply_time_decay, which only charges
decay after 7+ days of inactivity, sizes the penalty linearly (2 Elo/day),
and checkpoints via UserTopicMastery.last_decay_applied_at so re-running
the sweep never double-charges the same inactive window.
"""

import json
import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from groups.models import UserTopicMastery
from groups.engines.elo_engine import EloEngine

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Applies checkpointed inactivity Elo decay to all UserTopicMastery records.'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Report what would be decayed and change nothing. Runs the "
                "real engine inside a rolled-back transaction, so the "
                "prediction cannot drift from what a live sweep would do."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        mode = " (DRY RUN — no changes will be saved)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"Starting inactivity decay sweep...{mode}"))

        decayed_count = 0
        scanned = 0
        total_penalty = 0.0
        at_floor = 0

        # §2.2: mastery rows are mutated only under select_for_update. The
        # id scan is lock-free; each row is re-fetched WITH the lock inside
        # its own short transaction, so a user who returns and submits
        # mid-sweep is seen with their fresh last_practiced (no wrongful
        # decay from a stale in-memory row), and a submission blocked on
        # this lock waits milliseconds, not the whole sweep. One row per
        # transaction also means no multi-row lock ordering to reason about.
        for mastery_id in UserTopicMastery.objects.values_list('pk', flat=True).iterator():
            scanned += 1
            with transaction.atomic():
                mastery = (
                    UserTopicMastery.objects.select_for_update()
                    .select_related('user', 'topic')
                    .get(pk=mastery_id)
                )
                result = EloEngine.apply_time_decay(mastery)
                if dry_run:
                    # The engine writes unconditionally, so a dry run executes
                    # the real code path and discards it. Predicting with a
                    # copy of the formula here would be a second implementation
                    # that could drift from the one that actually runs.
                    transaction.set_rollback(True)
            if result.get("decayed"):
                decayed_count += 1
                total_penalty += result["penalty"]
                if result["new_rating"] <= 800.0:
                    at_floor += 1
                logger.info(
                    "%sDecay for %s on %s: -%.1f Elo -> %.1f",
                    "[dry-run] " if dry_run else "",
                    mastery.user.username, mastery.topic,
                    result["penalty"], result["new_rating"],
                )

        verb = "would decay" if dry_run else "decayed"
        self.stdout.write(self.style.SUCCESS(
            f"Scanned {scanned} mastery record(s); {verb} {decayed_count}."
        ))
        if at_floor:
            # Not an error, but worth surfacing: further inactivity cannot
            # lower these any more, so the signal has saturated.
            self.stdout.write(self.style.WARNING(
                f"  warning: {at_floor} record(s) clamped at the 800 Elo floor"
            ))

        # Returned as a JSON STRING, not a dict: Django writes a command's
        # return value to stdout via OutputWrapper.write, which calls
        # .endswith() on it. A dict raises AttributeError there. The upside
        # is that the scheduled job's log gets one machine-readable line.
        return json.dumps({
            "scanned": scanned,
            "changed": decayed_count,
            "total_penalty": round(total_penalty, 1),
            "warnings": (
                [f"{at_floor} record(s) clamped at the 800 Elo floor"] if at_floor else []
            ),
        })
