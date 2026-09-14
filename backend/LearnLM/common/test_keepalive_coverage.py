"""
Warm-coverage reporting (M15b).

Companion to test_keepalive_contract.py, which asks "was the API warm when we
pinged it?". This asks the question that actually predicts what a visitor
experiences: "how much of the day is covered by a ping at all?"

The answer had been nobody's job. Every warm-keeper run reported success while
the gaps between runs ran to five hours, so the API slept through most of the
day behind an unbroken row of green checkmarks.

These tests pin the arithmetic and the alarm. The arithmetic matters because
the tempting simplification -- "warm time is the loop, cold time is the gap" --
is wrong in the direction that flatters us: the service coasts for Render's
idle timeout after the last ping. The alarm matters because the whole point is
that a human sees it.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "keepalive_coverage.py"

# Render stops a free web service after ~15 minutes without a request.
IDLE = 15 * 60
# The warm-keeper's in-run ping loop, LOOP_SECONDS in keepalive.yml.
LOOP = 45 * 60


def _load():
    assert MODULE_PATH.exists(), f"coverage script not found at {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("keepalive_coverage",
                                                  MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def coverage():
    return _load()


# ── the arithmetic ────────────────────────────────────────────────────────

def test_back_to_back_runs_are_fully_warm(coverage):
    assert coverage.warm_fraction(0, LOOP, IDLE) == 1.0


def test_a_gap_inside_the_idle_window_is_still_fully_warm(coverage):
    # The service does not sleep the moment the pings stop. A 10 min gap under
    # a 15 min idle timer costs nothing.
    assert coverage.warm_fraction(10 * 60, LOOP, IDLE) == 1.0


def test_the_idle_timeout_is_the_last_fully_warm_gap(coverage):
    # Boundary. At exactly the idle timeout the service is still up; one
    # second later it is not. A `<` here instead of `<=` would silently
    # reclassify the boundary case.
    assert coverage.warm_fraction(IDLE, LOOP, IDLE) == 1.0
    assert coverage.warm_fraction(IDLE + 60, LOOP, IDLE) < 1.0


def test_idle_credit_is_capped_at_the_idle_timeout(coverage):
    # THE LOAD-BEARING ASSERTION. Without the min() clamp, a longer gap would
    # count entirely as warm and every cycle would score 100% -- the exact
    # flattering bug this file exists to prevent. Real numbers from
    # 2026-09-14: a 45 min loop followed by a 304 min gap.
    fraction = coverage.warm_fraction(304 * 60, LOOP, IDLE)

    assert fraction == pytest.approx((LOOP + IDLE) / (LOOP + 304 * 60))
    assert 0.16 < fraction < 0.18          # ~17%, not ~100%


def test_a_longer_gap_is_never_better_coverage(coverage):
    # Monotonicity: any formula where waiting longer scores higher is wrong.
    fractions = [coverage.warm_fraction(gap * 60, LOOP, IDLE)
                 for gap in (0, 15, 30, 60, 120, 300)]

    assert fractions == sorted(fractions, reverse=True)


def test_a_zero_length_cycle_is_not_reported_as_zero_coverage(coverage):
    # No elapsed time at all describes nothing; calling that "0% warm" would
    # raise a false alarm on the first run.
    assert coverage.warm_fraction(0, 0, IDLE) == 1.0


def test_negative_durations_are_rejected(coverage):
    with pytest.raises(ValueError):
        coverage.warm_fraction(-1, LOOP, IDLE)


# ── the verdict ───────────────────────────────────────────────────────────

def test_a_short_gap_reports_continuous_coverage(coverage):
    covered, line = coverage.report(5 * 60, LOOP, IDLE)

    assert covered is True
    assert "Continuous coverage" in line


def test_the_verdict_agrees_with_the_arithmetic_at_every_gap(coverage):
    # `covered` and `warm_fraction` are two statements about the same fact and
    # must never disagree: the run is covered exactly when no part of the
    # cycle was cold. Asserting the relationship rather than a hand-picked
    # boundary value means the boundary cannot drift in one of them alone --
    # which it could, since the earlier version of this file only pinned the
    # boundary on warm_fraction and a `<=` -> `<` mutation in report() passed
    # every test.
    for gap_minutes in (0, 5, 14, 15, 16, 30, 304):
        gap = gap_minutes * 60
        covered, _line = coverage.report(gap, LOOP, IDLE)

        assert covered is (coverage.warm_fraction(gap, LOOP, IDLE) == 1.0), (
            f"verdict and duty cycle disagree at a {gap_minutes} min gap"
        )


def test_a_long_gap_says_how_long_production_was_asleep(coverage):
    # 304 min gap, 15 min of which the idle timer covers -> 289 min asleep.
    covered, line = coverage.report(304 * 60, LOOP, IDLE)

    assert covered is False
    assert "289 min" in line


def test_the_warning_names_a_real_remedy(coverage):
    # A warning that only says "this is bad" gets ignored. Whoever reads it
    # must be able to act without re-deriving the diagnosis.
    _covered, line = coverage.report(304 * 60, LOOP, IDLE)

    assert "uptime monitor" in line
    assert "Render plan" in line


# ── the alarm actually fires ──────────────────────────────────────────────

def test_an_uncovered_gap_emits_a_workflow_warning(coverage, capsys, tmp_path,
                                                   monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    exit_code = coverage.main(["--gap-seconds", str(304 * 60),
                               "--loop-seconds", str(LOOP)])

    out = capsys.readouterr().out
    assert exit_code == 0                  # visible, not blocking: see module docstring
    assert "::warning title=" in out
    assert "asleep" in summary.read_text(encoding="utf-8")


def test_a_covered_gap_emits_no_warning(coverage, capsys, monkeypatch):
    # Noise discipline: if this annotated every run, nobody would read any of
    # them.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    coverage.main(["--gap-seconds", "300", "--loop-seconds", str(LOOP)])

    assert "::warning" not in capsys.readouterr().out


def test_the_first_ever_run_says_it_has_no_measurement(coverage, capsys):
    # "No data" must not be rendered as "perfect coverage". Defaulting the
    # missing gap to zero would print "Continuous coverage ... 100%" about a
    # measurement that was never taken -- the same flattering lie as the 194
    # green runs that never kept anything warm. Silence about the absence is
    # what lets that lie through, so the absence is asserted explicitly.
    exit_code = coverage.main(["--loop-seconds", str(LOOP)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "::warning" not in out
    assert "No previous run" in out
    assert "Continuous coverage" not in out


def test_overlapping_runs_do_not_warn(coverage, capsys):
    # The concurrency group should make this impossible; if it ever happens,
    # coverage is total and a negative "asleep" figure would be nonsense.
    coverage.main(["--gap-seconds", "-120", "--loop-seconds", str(LOOP)])

    assert "::warning" not in capsys.readouterr().out


def test_timestamps_are_accepted_in_githubs_own_format(coverage, capsys):
    # GitHub's API returns "...Z", which datetime.fromisoformat rejected
    # before 3.11 and still rejects if the replacement is dropped.
    coverage.main(["--previous-end", "2026-09-14T07:12:38Z",
                   "--current-start", "2026-09-14T12:16:38Z",
                   "--loop-seconds", str(LOOP)])

    out = capsys.readouterr().out
    assert "::warning" in out
    assert "304 min ago" in out
