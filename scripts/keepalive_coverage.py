#!/usr/bin/env python3
"""
How much of the time is production actually warm? (M15b)

THE GAP THIS CLOSES
    scripts/keepalive.sh answers "was the API warm when I pinged it?" — and it
    answers it honestly, which is more than the version it replaced did.

    But nothing answered the question one level up: "how much of the wall clock
    is covered by a ping at all?" That question has a different answer, and it
    is the one that matters to a visitor, because a visitor does not arrive
    during our runs — they arrive whenever they arrive.

    Every run of the warm-keeper reported `success` while the observed gaps
    between runs were 72 to 304 minutes (measured 2026-09-14 from the eight
    most recent runs). The in-run loop covers 45 of those minutes. So the API
    was unattended for roughly three quarters of the day, and the workflow's
    green checkmark said nothing about it — the same shape of blindness the
    keepalive header describes, one level up.

    GitHub throttles `schedule:` on free runners; that is outside this repo's
    control and is NOT a defect to fix here. What IS fixable is that it was
    invisible.

WARNS, DOES NOT FAIL
    Deliberate. The cause is GitHub's scheduler, so a failing check would be
    red on essentially every run, and a check that is always red is one people
    filter out of their inbox — which would put us back where we started. A
    warning annotation plus a step summary is read by whoever looks; a
    permanent failure is read by no one.

    The behaviour that must never regress silently is the PING, and that one
    does fail loudly (keepalive.sh, and test_keepalive_contract.py).

Usage:
    python scripts/keepalive_coverage.py --gap-seconds 4200
    python scripts/keepalive_coverage.py --previous-end <iso> --current-start <iso>
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

# Render stops a free web service after ~15 minutes without a request. Kept in
# sync with RENDER_IDLE_TIMEOUT_SECONDS in common/test_keepalive_contract.py.
RENDER_IDLE_TIMEOUT_SECONDS = 15 * 60


def warm_fraction(gap_seconds: float, loop_seconds: float,
                  idle_timeout_seconds: float = RENDER_IDLE_TIMEOUT_SECONDS
                  ) -> float:
    """Fraction of one run-to-run cycle during which the API stays awake.

    A cycle is the run itself (`loop_seconds` of pings) plus `gap_seconds` of
    nothing. The service does not fall asleep the instant the pings stop — it
    coasts for `idle_timeout_seconds` — so the warm stretch is the loop plus
    however much of the gap the idle timer covers.
    """
    if loop_seconds < 0 or gap_seconds < 0 or idle_timeout_seconds < 0:
        raise ValueError("durations cannot be negative")
    cycle = loop_seconds + gap_seconds
    if cycle == 0:
        # No elapsed time to describe. Saying "0% warm" here would be a lie
        # about a cycle that has not happened yet.
        return 1.0
    warm = loop_seconds + min(idle_timeout_seconds, gap_seconds)
    return warm / cycle


def report(gap_seconds: float, loop_seconds: float,
           idle_timeout_seconds: float = RENDER_IDLE_TIMEOUT_SECONDS
           ) -> tuple[bool, str]:
    """Return (covered, human-readable line).

    `covered` is True when the gap is short enough that the service never
    spun down — i.e. the next run started before the idle timer expired.
    """
    fraction = warm_fraction(gap_seconds, loop_seconds, idle_timeout_seconds)
    covered = gap_seconds <= idle_timeout_seconds
    gap_minutes = gap_seconds / 60
    if covered:
        line = (f"Continuous coverage: the previous run ended "
                f"{gap_minutes:.0f} min ago, inside Render's "
                f"{idle_timeout_seconds / 60:.0f} min idle window. "
                f"Warm duty cycle {fraction:.0%}.")
    else:
        cold_minutes = (gap_seconds - idle_timeout_seconds) / 60
        line = (f"Production was asleep for about {cold_minutes:.0f} min "
                f"before this run: the previous run ended {gap_minutes:.0f} "
                f"min ago and Render sleeps after "
                f"{idle_timeout_seconds / 60:.0f} min. Warm duty cycle "
                f"{fraction:.0%}. A visitor arriving in that window waits out "
                f"a cold start (measured 92.9 s). Fix: an external uptime "
                f"monitor on a true 5-minute cadence, or a paid Render plan "
                f"that does not sleep - see docs/DEPLOYMENT.md.")
    return covered, line


def _parse_iso(value: str) -> dt.datetime:
    # GitHub timestamps end in "Z"; fromisoformat only learned that in 3.11.
    return dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-seconds", type=float)
    parser.add_argument("--previous-end")
    parser.add_argument("--current-start")
    parser.add_argument("--loop-seconds", type=float,
                        default=float(os.environ.get("LOOP_SECONDS", 0)))
    args = parser.parse_args(argv)

    if args.gap_seconds is not None:
        gap = args.gap_seconds
    elif args.previous_end and args.current_start:
        gap = (_parse_iso(args.current_start)
               - _parse_iso(args.previous_end)).total_seconds()
    else:
        # First ever run, or the previous run could not be read. Nothing to
        # compare against; that is not a finding.
        print("No previous run to measure a gap against; skipping coverage.")
        return 0

    if gap < 0:
        # Overlapping runs. The concurrency group should prevent this, but if
        # it ever happens, coverage is total and there is nothing to warn on.
        print(f"Runs overlapped by {-gap:.0f}s; coverage is continuous.")
        return 0

    covered, line = report(gap, args.loop_seconds)
    print(line)
    if not covered:
        print(f"::warning title=Production slept between warm-keeper runs::{line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Warm coverage\n\n{line}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
