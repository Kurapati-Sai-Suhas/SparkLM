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

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from groups.models import UserTopicMastery
from groups.engines.elo_engine import EloEngine

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Applies checkpointed inactivity Elo decay to all UserTopicMastery records.'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS("🚀 Starting inactivity decay sweep..."))

        decayed_count = 0
        scanned = 0

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
            if result.get("decayed"):
                decayed_count += 1
                logger.info(
                    "Decay applied for %s on %s: -%.1f Elo -> %.1f",
                    mastery.user.username, mastery.topic,
                    result["penalty"], result["new_rating"],
                )

        self.stdout.write(self.style.SUCCESS(
            f"✅ Scanned {scanned} mastery records; decay applied to {decayed_count}."
        ))
