"""
Guards on the CI/CD and scheduled-job configuration (M5 Phase 2, F14).

Phase 2 found that CI had been RED on every push since the frontend tests
were added: `ci.yml` pinned Node 20 while jsdom 30.0.1 declares
`engines.node = "^22.22.2 || ^24.15.0 || >=26.0.0"`, so the vitest forks
worker could not start and both test files errored before running a single
assertion. The suite that was supposed to protect the frontend had never
executed in CI, and nothing said so — the failure looked like a flaky build.

These tests read the workflow YAML the same way GitHub does, so the class of
"the pipeline is lying to us" defect fails locally and in CI rather than
being discovered months later.

They deliberately assert on configuration, not on behaviour: nothing here
needs network, credentials, or a GitHub token.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
FRONTEND = REPO / "studysphere-ai-11"


def _read(name):
    path = WORKFLOWS / name
    assert path.exists(), f"missing workflow: {name}"
    return path.read_text(encoding="utf-8")


# ── the defect this phase found ──────────────────────────────────────────

def test_ci_node_version_satisfies_the_frontend_toolchain():
    """
    The regression test for the broken pipeline. jsdom's engine range is the
    binding constraint; if someone pins Node below it, vitest cannot start
    and every frontend test silently stops running.
    """
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))

    jsdom = None
    for key, meta in lock.get("packages", {}).items():
        if key.endswith("node_modules/jsdom"):
            jsdom = meta
            break
    if jsdom is None or "engines" not in jsdom:
        pytest.skip("jsdom not resolvable from the lockfile")

    required = jsdom["engines"]["node"]
    majors = {int(m) for m in re.findall(r"[\^>=]*\s*(\d+)\.", required)}

    ci = _read("ci.yml")
    pinned = re.search(r"node-version:\s*'([^']+)'", ci)
    assert pinned, "ci.yml does not pin a node-version"
    pinned_major = int(pinned.group(1).split(".")[0])

    assert pinned_major in majors or pinned_major > max(majors), (
        f"ci.yml pins Node {pinned.group(1)} but the frontend toolchain "
        f"requires {required}. On an unsupported major the vitest worker "
        f"fails to start and NO frontend test runs."
    )


def test_frontend_tests_run_before_the_build():
    """
    A failing unit test must be reported as a failing test, not swallowed by
    a build step that happens to come first.
    """
    ci = _read("ci.yml")
    assert "npm test" in ci
    assert ci.index("npm test") < ci.index("npm run build")


# ── scheduled jobs ───────────────────────────────────────────────────────

def test_maintenance_workflow_is_scheduled_and_manually_triggerable():
    """
    Both matter: cron is the routine path, workflow_dispatch is how a failed
    sweep gets re-run without waiting a day.
    """
    wf = _read("maintenance.yml")
    assert "schedule:" in wf and "cron:" in wf
    assert "workflow_dispatch:" in wf


def test_maintenance_workflow_runs_the_real_command():
    wf = _read("maintenance.yml")
    assert "run_maintenance" in wf


def test_every_secret_used_by_a_workflow_is_documented():
    """
    Seven secrets are referenced across the workflows. An undocumented one
    is how a job ends up failing in a way nobody can diagnose — the register
    in docs/FEATURE_FLAGS.md is the operator's checklist.
    """
    referenced = set()
    for wf in WORKFLOWS.glob("*.yml"):
        referenced |= set(re.findall(r"secrets\.([A-Z_]+)", wf.read_text(encoding="utf-8")))
    referenced.discard("GITHUB_TOKEN")  # provided by Actions, never configured

    register = (REPO / "docs" / "FEATURE_FLAGS.md").read_text(encoding="utf-8")

    undocumented = sorted(
        name for name in referenced
        if name not in register and name.split("_")[0] not in register
    )
    assert not undocumented, (
        f"workflow secrets absent from docs/FEATURE_FLAGS.md: {undocumented}"
    )


def test_the_maintenance_timeout_is_bounded():
    """
    An unbounded job on a free-tier runner can burn the monthly minute
    budget on one hung sweep.
    """
    wf = _read("maintenance.yml")
    assert "timeout-minutes:" in wf, "maintenance.yml has no timeout"


# ── the operational contract these workflows depend on ───────────────────

@pytest.mark.django_db
def test_the_maintenance_heartbeat_round_trips():
    """
    The signal the whole scheduled-job story rests on: if a sweep fails or
    stops running, /healthz?ops=1 must be able to say so.
    """
    from common.models import MaintenanceRun

    MaintenanceRun.record("unit_test_task", succeeded=True, duration_ms=12,
                          detail="scanned 0, decayed 0")

    row = MaintenanceRun.objects.get(task="unit_test_task")
    assert row.succeeded is True
    assert row.age_seconds is not None and row.age_seconds < 60


@pytest.mark.django_db
def test_the_heartbeat_survives_a_non_string_detail():
    """
    record() is called from inside run_maintenance's own except block. If it
    raised there, the sweep would lose the failure it was reporting and take
    the command down with it. Found by passing a stats dict — the obvious
    caller mistake — which used to raise KeyError on the truncation slice.
    """
    from common.models import MaintenanceRun

    MaintenanceRun.record("unit_test_dict", succeeded=False, duration_ms=1,
                          detail={"scanned": 3, "error": "boom"})

    row = MaintenanceRun.objects.get(task="unit_test_dict")
    assert row.succeeded is False
    assert "boom" in row.detail


@pytest.mark.django_db
def test_an_overlong_detail_is_truncated_not_rejected():
    from common.models import MaintenanceRun

    MaintenanceRun.record("unit_test_long", succeeded=True, detail="x" * 5000)

    assert len(MaintenanceRun.objects.get(task="unit_test_long").detail) == 2000


@pytest.mark.django_db
def test_a_failed_sweep_is_recorded_as_failed():
    """A heartbeat that only ever says 'ok' is not a heartbeat."""
    from common.models import MaintenanceRun

    MaintenanceRun.record("unit_test_fail", succeeded=False, duration_ms=5,
                          detail={"error": "boom"})

    assert MaintenanceRun.objects.get(task="unit_test_fail").succeeded is False


# ── daily question-bank validation (M2 P2.5, Phase 8) ────────────────────

QUESTION_BANK_WF = "question-bank-validation.yml"

#: Any of these in the daily workflow would make a read-only audit into a
#: scheduled data change. `seed_data` is the dangerous one: it deletes every
#: Question, and CodeSubmission.question is on_delete=CASCADE.
DESTRUCTIVE_COMMANDS = (
    "seed_data", "seed_leetcode", "seed_problems", "bulk_seed",
    "restore_questions", "cleanup_question_bank", "reseed_questions",
    "seed_dsa_dag", "flush", "sqlflush",
)


def test_the_daily_validation_runs_at_the_approved_time():
    """
    22:45 Asia/Kolkata. IST is UTC+5:30 with no daylight saving, so 17:15 UTC
    holds all year and needs no seasonal correction.
    """
    wf = _read(QUESTION_BANK_WF)

    assert 'cron: "15 17 * * *"' in wf
    assert "workflow_dispatch" in wf, "no manual trigger for an ad-hoc audit"


def _executable(name):
    """
    Workflow text with comment lines removed.

    This file DOCUMENTS why it does not reseed, so it names `seed_data` and
    `SPARKLM_ENV` in prose. A guard that greps raw text fails on its own
    explanation — and the property that matters is what the job RUNS.
    """
    return "\n".join(
        line for line in _read(name).splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_daily_validation_is_read_only():
    """
    The safety property of the whole schedule. A reseed on a timer would
    destroy learner history the first time it ran against production.
    """
    wf = _executable(QUESTION_BANK_WF)

    found = [c for c in DESTRUCTIVE_COMMANDS if c in wf]
    assert found == [], (
        f"the daily job invokes data-changing command(s): {found}. It must "
        f"only validate and reconcile."
    )
    assert "validate_question_bank" in wf
    assert "reconcile_hidden_tests" in wf


def test_the_daily_validation_does_not_declare_a_disposable_environment():
    """
    common/environment.py treats an unset SPARKLM_ENV as production, so this
    job is protected by default. Setting it to a disposable value here would
    silently arm every destructive command against the production database.
    """
    wf = _executable(QUESTION_BANK_WF)

    assert "SPARKLM_ENV" not in wf, (
        "the daily job declares SPARKLM_ENV — unset means production, which "
        "is the protection this job relies on"
    )


def test_the_daily_validation_timeout_is_bounded():
    wf = _read(QUESTION_BANK_WF)

    assert "timeout-minutes:" in wf


def test_the_daily_validation_keeps_its_reports_even_when_it_fails():
    """The run that failed is the one whose report someone needs to read."""
    wf = _read(QUESTION_BANK_WF)

    assert "if: always()" in wf
    assert "upload-artifact" in wf


def test_coverage_validation_can_fail_the_job_but_reconciliation_cannot():
    """
    Deliberate asymmetry. Coverage validation is pure database reads, so a
    failure is a real bank problem. Reconciliation calls Judge0 once per
    hidden test against a shared, rate-limited free tier — a throttled run is
    expected noise and must not cry wolf every night.
    """
    wf = _read(QUESTION_BANK_WF)
    validate_at = wf.index("Validate hidden-test coverage")
    reconcile_at = wf.index("Reconcile stored outputs")

    assert "continue-on-error" not in wf[validate_at:reconcile_at], (
        "coverage validation is allowed to fail silently"
    )
    assert "continue-on-error: true" in wf[reconcile_at:], (
        "a throttled Judge0 run would fail the nightly job"
    )
