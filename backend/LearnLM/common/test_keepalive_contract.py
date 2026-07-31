"""
Keepalive workflow contract (M1).

These assert on a CI config file rather than on Python, which is unusual in
this suite and deliberate: the production defect they guard lived entirely
in `.github/workflows/keepalive.yml`, and it was invisible precisely because
nothing could observe it.

Measured 2026-07-29: gaps between runs of that workflow were 54-213 min
(mean 104.5) against Render's ~15 min idle timeout, so production spun down
between essentially every pair of pings; a controlled 21-minute quiesce then
measured a 92.9 s cold start. The old script exited 0 on the first HTTP 200
it received -- and a cold start still eventually returns 200 -- so all 194
historical runs reported success while never preventing a single spin-down.

The invariants below are the ones whose violation reproduces that bug:
a keepalive that pings once, or that cannot fail, is a green checkmark
rather than a warm server.

Plain-text assertions are used on purpose. PyYAML is not pinned in
backend/requirements.txt, and this repo has previously gone red in CI on
unpinned transitive imports, so parsing the YAML would trade a real
dependency risk for cosmetic elegance.
"""

import re
from pathlib import Path

import pytest

# common/ -> LearnLM/ -> backend/ -> repo root
WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows" / "keepalive.yml"
)

# Render stops a free web service after ~15 minutes of inactivity. Any ping
# interval at or above that guarantees a spin-down between pings.
RENDER_IDLE_TIMEOUT_SECONDS = 15 * 60


@pytest.fixture(scope="module")
def workflow_text():
    assert WORKFLOW.exists(), f"keepalive workflow not found at {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _int_setting(text, name):
    """Read a `NAME: "123"` env value out of the workflow."""
    match = re.search(rf'{name}:\s*"?(\d+)"?', text)
    assert match, f"{name} is not defined in the keepalive workflow"
    return int(match.group(1))


def test_workflow_still_exists_and_is_scheduled(workflow_text):
    assert "schedule:" in workflow_text
    assert "cron:" in workflow_text
    # Manual trigger is what lets an operator verify the fix on demand.
    assert "workflow_dispatch:" in workflow_text


def test_pings_more_than_once_per_run(workflow_text):
    """
    The core regression guard.

    GitHub throttles free-tier cron to ~1-3.5 hours, which we do not control.
    A run that pings once therefore cannot keep a 15-minute idle timeout at
    bay no matter how the cron line is written. Coverage has to come from
    looping WITHIN the run.
    """
    assert "while" in workflow_text, "the warm-keeper must loop, not ping once"
    loop_minutes = _int_setting(workflow_text, "LOOP_MINUTES")
    assert loop_minutes > 0, "LOOP_MINUTES must be positive"
    assert loop_minutes >= 15, (
        "a loop shorter than Render's idle timeout adds no coverage beyond a single ping"
    )


def test_ping_interval_is_shorter_than_render_idle_timeout(workflow_text):
    interval = _int_setting(workflow_text, "INTERVAL_SECONDS")
    assert interval < RENDER_IDLE_TIMEOUT_SECONDS, (
        f"ping interval {interval}s >= Render idle timeout "
        f"{RENDER_IDLE_TIMEOUT_SECONDS}s: the service would sleep between pings"
    )


def test_keepalive_can_actually_fail(workflow_text):
    """
    The original workflow exited 0 on the first HTTP 200 and had no notion of
    'slow'. Because a cold start also returns 200, success was unfalsifiable.
    A warm-keeper must have at least one path that reports failure.
    """
    assert "exit 1" in workflow_text, "the workflow has no failure path"
    assert "::error::" in workflow_text, "failures must surface in the run log"


def test_cold_response_threshold_is_between_warm_noise_and_a_cold_start(workflow_text):
    """
    Warm healthz measured 0.70-1.46 s; a cold start measured 92.9 s. The
    threshold must sit clearly between the two: too low flaps on transient
    blips (alert fatigue is its own blindness), too high stops detecting
    real spin-downs.
    """
    threshold = _int_setting(workflow_text, "WARM_THRESHOLD_SECONDS")
    assert 2 <= threshold <= 30, (
        f"WARM_THRESHOLD_SECONDS={threshold} is outside the useful band "
        "(above warm noise ~1.5s, well below a ~93s cold start)"
    )


def test_concurrent_runs_are_prevented(workflow_text):
    """A cron firing during a long loop must queue, not run a second warm-keeper."""
    assert "concurrency:" in workflow_text
    assert "cancel-in-progress: false" in workflow_text, (
        "cancelling the in-flight run would end the warm window early"
    )


def test_probes_the_readiness_endpoint(workflow_text):
    """
    /healthz round-trips the database, so pinging it wakes Render AND Neon.
    Pointing the warm-keeper at a DB-free path would silently stop covering
    the database.
    """
    assert "/healthz" in workflow_text
