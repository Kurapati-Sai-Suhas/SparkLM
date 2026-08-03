"""
run_maintenance — the single scheduled periodic job (M4 Phase B).

Milestone 3 left two time-dependent commands that nothing invoked:
`calculate_decay` (inactivity Elo decay) and `send_spaced_repetition`
(review notifications). 60 of 66 production mastery rows had never been
decayed, so `/api/review/queue/` was reading a model that had not updated
since its rows were created.

── Why one command instead of two scheduled workflows ─────────────────────
Two schedules double the surface that can silently stop, and the M4 review
specifically criticised adding a second unmonitored periodic job while
listing unscheduled jobs as critical debt. One command, one schedule, one
heartbeat — and the heartbeat is the point. An unscheduled job was invisible
for months; a job that starts failing at 3am would be equally invisible
without a freshness signal something else can read.

── Failure isolation ──────────────────────────────────────────────────────
Sub-tasks are independent. Decay failing must not prevent notifications, and
neither must prevent the heartbeat from recording what happened — a run that
crashes before writing its heartbeat looks identical to a run that never
started, which is exactly the ambiguity this exists to remove.

The process still exits non-zero if any sub-task failed, so the scheduled
workflow goes red. Heartbeat for humans reading /healthz, exit code for CI.
"""

import json
import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from common.models import MaintenanceRun

logger = logging.getLogger(__name__)

# (heartbeat task name, management command, supports_dry_run, kwargs)
#
# Sub-tasks are invoked through call_command rather than imported and called
# directly so each keeps its own argument handling, and so running one by
# hand behaves identically to running it from here.
#
# `supports_dry_run` is declared here rather than inferred by name. The first
# version of this command special-cased `send_spaced_repetition` by string
# comparison, which meant `--dry-run` silently ran the REAL decay sweep and
# mutated learner state — the opposite of what a dry run promises. A task
# that cannot honour --dry-run must be declared, not assumed.
TASKS = [
    ("calculate_decay", "calculate_decay", True, {}),
    ("send_spaced_repetition", "send_spaced_repetition", True, {}),
]

OVERALL_TASK = "run_maintenance"


class Command(BaseCommand):
    help = "Run all periodic maintenance sweeps and record a heartbeat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            help="Run a single sub-task by name (for debugging a failure).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Pass --dry-run to sub-tasks that support it; records no heartbeat.",
        )

    def handle(self, *args, **options):
        only = options.get("only")
        dry_run = options.get("dry_run")

        tasks = [t for t in TASKS if not only or t[0] == only]
        if only and not tasks:
            raise CommandError(
                f"Unknown sub-task {only!r}. Known: {', '.join(t[0] for t in TASKS)}"
            )

        if dry_run:
            unsupported = [n for n, _, supports, _ in tasks if not supports]
            if unsupported:
                # Better to refuse than to half-honour --dry-run: a sweep that
                # mutates for some sub-tasks and not others is worse than one
                # that clearly declines.
                raise CommandError(
                    f"--dry-run is not supported by: {', '.join(unsupported)}. "
                    f"Use --only to run the sub-tasks that do."
                )
            self.stdout.write(self.style.WARNING(
                "DRY RUN — no data will be changed and no heartbeat recorded."
            ))

        overall_start = time.perf_counter()
        failures = []
        results = []

        for name, command, supports_dry_run, kwargs in tasks:
            call_kwargs = dict(kwargs)
            if dry_run and supports_dry_run:
                call_kwargs["dry_run"] = True
            # Sub-task output goes to OUR streams, so redirecting this
            # command captures the whole sweep. Without it the sub-commands
            # write straight to the process stdout and a piped or captured
            # run silently loses everything they reported.
            call_kwargs.setdefault("stdout", self.stdout)
            call_kwargs.setdefault("stderr", self.stderr)

            self.stdout.write(f"-> {name}")
            started = time.perf_counter()
            try:
                output = call_command(command, **call_kwargs)
                elapsed = int((time.perf_counter() - started) * 1000)
                stats = _parse_stats(output)
                detail = _format_detail(stats) or str(output or "").strip() or "ok"
                results.append((name, True, elapsed, stats))
                if not dry_run:
                    MaintenanceRun.record(name, True, elapsed, detail)
                self.stdout.write(self.style.SUCCESS(f"  {name} ok ({elapsed} ms)"))
            except Exception as exc:  # noqa: BLE001 — isolation is the point
                elapsed = int((time.perf_counter() - started) * 1000)
                failures.append(name)
                results.append((name, False, elapsed, {"error": f"{type(exc).__name__}: {exc}"}))
                logger.exception("Maintenance sub-task %s failed", name)
                if not dry_run:
                    MaintenanceRun.record(name, False, elapsed, f"{type(exc).__name__}: {exc}")
                self.stderr.write(self.style.ERROR(f"  {name} FAILED ({elapsed} ms): {exc}"))

        overall_ms = int((time.perf_counter() - overall_start) * 1000)
        if not dry_run:
            MaintenanceRun.record(
                OVERALL_TASK,
                not failures,
                overall_ms,
                "all ok" if not failures else f"failed: {', '.join(failures)}",
            )

        self._summary(results, overall_ms, dry_run, failures)

        if failures:
            # Heartbeat is already written above — the non-zero exit is for
            # the workflow, not a reason to lose the record of what happened.
            raise CommandError(
                f"{len(failures)} maintenance sub-task(s) failed: {', '.join(failures)}"
            )

    def _summary(self, results, overall_ms, dry_run, failures):
        """
        One scannable block at the end. A scheduled job's output is read
        exactly twice: when it is first set up, and when something is wrong.
        Both readers want counts and warnings, not a transcript.
        """
        succeeded = sum(1 for _, ok, _, _ in results if ok)
        warnings = [
            (name, w)
            for name, _, _, stats in results
            for w in stats.get("warnings", [])
        ]

        self.stdout.write("")
        self.stdout.write("=" * 58)
        self.stdout.write(
            f"MAINTENANCE SUMMARY{'  (DRY RUN — nothing was changed)' if dry_run else ''}"
        )
        self.stdout.write("=" * 58)
        self.stdout.write(f"  {'task':<24}{'result':>8}{'rows':>8}{'changed':>9}{'ms':>8}")
        for name, ok, elapsed, stats in results:
            self.stdout.write(
                f"  {name:<24}{'ok' if ok else 'FAILED':>8}"
                f"{stats.get('scanned', '-'):>8}{stats.get('changed', '-'):>9}{elapsed:>8}"
            )
        self.stdout.write("-" * 58)
        self.stdout.write(
            f"  {len(results)} sub-task(s): {succeeded} succeeded, "
            f"{len(failures)} failed, in {overall_ms} ms"
        )

        if warnings:
            self.stdout.write("")
            for name, warning in warnings:
                self.stdout.write(self.style.WARNING(f"  warning [{name}]: {warning}"))

        self.stdout.write("")
        if failures:
            self.stdout.write(self.style.ERROR(f"  EXIT: failure ({', '.join(failures)})"))
        elif dry_run:
            self.stdout.write(self.style.WARNING("  EXIT: success (dry run — no changes applied)"))
        else:
            self.stdout.write(self.style.SUCCESS("  EXIT: success"))


def _parse_stats(output):
    """
    Sub-tasks report structured counts as a JSON string (see
    calculate_decay.py for why a string rather than a dict). A task that
    returns something else, or nothing, simply contributes no counts —
    the summary degrades to dashes rather than failing the sweep.
    """
    if not output:
        return {}
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_detail(stats):
    """Compact `k=v` summary for the heartbeat row; '' if there is nothing."""
    if not stats:
        return ""
    parts = [f"{k}={v}" for k, v in stats.items() if k != "warnings" and v is not None]
    if stats.get("warnings"):
        parts.append(f"warnings={len(stats['warnings'])}")
    return " ".join(parts)
