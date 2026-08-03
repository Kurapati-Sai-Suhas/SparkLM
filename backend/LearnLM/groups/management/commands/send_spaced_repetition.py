"""
send_spaced_repetition — notify users whose practiced topics are due for review.

Rewritten in M4 Phase B. The previous version was a demonstration stub that
never queried the database:

    # NOTE: Replace this mock data with your actual UserTopicMastery query
    # mastery_records = UserTopicMastery.objects.filter(user=user)
    topic_name = "Arrays"
    days_since_last = 9
    reviews = 8

It iterated EVERY user and pushed a WebSocket notification built from those
three constants, so every user would have been told they were forgetting
"Arrays" regardless of what they had practiced. Nothing scheduled it, which
is the only reason that never reached anyone. Phase B adds a schedule, so
fixing this had to come first.

── Why it reuses learning.memory rather than its own formula ──────────────
The stub carried a second, incompatible retention model: Ebbinghaus
R = e^(-t/S) with S = 1 + 0.5*reviews and a 0.60 threshold. The rest of the
platform — ReviewQueueView, effective mastery — uses the HLR curve in
learning/memory.py: P(t) = 2^(-t/h) with a 0.5 threshold, where h is the
per-user-topic halflife maintained by the SM-2 update in ProgressionService.

Two models would mean /api/review/queue/ and the notification disagreeing
about the same topic for the same user, which is worse than either being
wrong alone. This command now answers exactly the question the review queue
answers, using the same function.
"""

import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.management.base import BaseCommand
from django.utils import timezone

from groups.models import UserTopicMastery
from learning.memory import is_due, retention

logger = logging.getLogger(__name__)

# Only the most-decayed topics are worth interrupting someone for. A user
# with eleven due topics does not need eleven notifications; they need the
# worst few and a reason to open the review queue.
MAX_TOPICS_PER_USER = 3


def due_topics_for(mastery_rows, now=None):
    """
    Filter mastery rows to those genuinely due, worst retention first.

    Pure over the rows it is given — no ORM access, no channel layer — so
    the selection logic is testable without a database or a WebSocket.
    Returns [(mastery, retention_fraction)].
    """
    now = now or timezone.now()
    due = []
    for m in mastery_rows:
        days = (now - m.last_practiced).total_seconds() / 86400.0
        r = retention(m.hlr_halflife, days)
        if is_due(m.reviews, r):
            due.append((m, r))
    due.sort(key=lambda pair: pair[1])
    return due[:MAX_TOPICS_PER_USER]


class Command(BaseCommand):
    help = "Notify users whose practiced topics are due for review (HLR retention)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent without pushing any notification.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        channel_layer = get_channel_layer()

        # One query ordered by user, not a query per user. select_related
        # because the message needs topic.name and the username.
        # reviews__gt=0 mirrors is_due()'s first condition at the database
        # layer: an unpracticed topic has nothing to forget.
        rows = (
            UserTopicMastery.objects.select_related("topic", "user")
            .filter(reviews__gt=0)
            .order_by("user_id")
        )

        by_user = {}
        for row in rows:
            by_user.setdefault(row.user_id, []).append(row)

        notified = 0
        topics_flagged = 0
        push_failures = 0
        scanned = len(rows)
        for user_id, mastery_rows in by_user.items():
            due = due_topics_for(mastery_rows)
            if not due:
                continue

            topics_flagged += len(due)
            notified += 1
            names = ", ".join(m.topic.name for m, _ in due)
            worst_pct = int((1.0 - due[0][1]) * 100)

            self.stdout.write(
                f"  {mastery_rows[0].user.username}: {len(due)} due ({names}), "
                f"worst {worst_pct}% decayed"
            )
            if dry_run:
                continue

            try:
                async_to_sync(channel_layer.group_send)(
                    f"notifications_{user_id}",
                    {
                        "type": "send_notification",
                        "title": "Time for a review",
                        "message": (
                            f"Your recall of {names} has faded. "
                            f"A quick refresher will bring it back."
                        ),
                        "category": "warning",
                    },
                )
            except Exception:
                # A dead channel layer must not abort the sweep for everyone
                # else. Notifications are enrichment; the review queue still
                # shows the same topics on next page load.
                push_failures += 1
                logger.exception("Failed to push review notification to user=%s", user_id)

        verb = "would notify" if dry_run else "notified"
        self.stdout.write(self.style.SUCCESS(
            f"Spaced repetition: {verb} {notified} user(s) across "
            f"{topics_flagged} due topic(s)."
        ))
        warnings = []
        if push_failures:
            # Swallowed above so one dead socket cannot end the sweep, but
            # silently dropping notifications is exactly the kind of quiet
            # degradation this milestone exists to surface.
            warnings.append(f"{push_failures} notification push(es) failed")
            self.stdout.write(self.style.WARNING(f"  warning: {warnings[-1]}"))

        # JSON string, not a dict — Django writes a command's return value
        # to stdout and calls .endswith() on it. See calculate_decay.py.
        return json.dumps({
            "scanned": scanned,
            "changed": notified,
            "topics_flagged": topics_flagged,
            "warnings": warnings,
        })
