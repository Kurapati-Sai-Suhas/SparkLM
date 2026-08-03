"""
common.models — operational state for the v2 shared-services app (M4 Phase B).

`common` was model-free until now, holding only services, middleware and
management commands. The heartbeat lives here rather than in `groups`
because the frozen architecture (§9) requires new code to land in v2 modules
and `groups` to shrink by extraction, never grow.

Why a database row rather than a cache key: a cache-backed heartbeat
disappears on a Redis restart or eviction, and an absent heartbeat is
indistinguishable from a job that stopped running. That would turn routine
infrastructure noise into a false "maintenance has failed" signal — and
worse, would train whoever reads it to ignore the alarm.
"""

from django.db import models
from django.utils import timezone


class MaintenanceRun(models.Model):
    """
    One row per periodic task, overwritten on each run.

    Not an append-only log: the question this answers is "when did this last
    run, and did it work?", which needs exactly one row. An audit trail of
    every sweep would grow without bound for a question nobody asks, and the
    per-run detail is already in the deploy log.
    """

    task = models.CharField(max_length=64, unique=True)
    last_run_at = models.DateTimeField(default=timezone.now)
    succeeded = models.BooleanField(default=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    # Short human-readable outcome, e.g. "scanned 66, decayed 12".
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["task"]

    def __str__(self):
        state = "ok" if self.succeeded else "FAILED"
        return f"{self.task} @ {self.last_run_at:%Y-%m-%d %H:%M} ({state})"

    @property
    def age_seconds(self):
        return (timezone.now() - self.last_run_at).total_seconds()

    @classmethod
    def record(cls, task, succeeded, duration_ms=None, detail=""):
        """Upsert the heartbeat for one task. Never raises on detail length."""
        return cls.objects.update_or_create(
            task=task,
            defaults={
                "last_run_at": timezone.now(),
                "succeeded": bool(succeeded),
                "duration_ms": duration_ms,
                "detail": (detail or "")[:2000],
            },
        )[0]
