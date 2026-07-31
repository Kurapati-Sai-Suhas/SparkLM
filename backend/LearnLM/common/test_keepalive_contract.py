"""
Warm-keeper verification (M1).

Two layers, and the second one is the point:

1. CONTRACT tests read .github/workflows/keepalive.yml and pin the config
   invariants (interval below the idle timeout, timeout budget large enough
   for the loop, threshold in a useful band).

2. BEHAVIOURAL tests actually execute scripts/keepalive.sh against a local
   stub HTTP server and assert its exit codes.

Layer 2 exists because layer 1 alone was proven worthless. A review mutated
the script so its cold-detection branch exited 0 instead of 1 -- reintroducing
the exact "194 green runs while never working" bug -- and all seven contract
assertions still passed. Text assertions verify that a file mentions the right
words, not that the program behaves correctly. test_cold_after_warm_fails
below is the test that kills that mutant.

Plain-text parsing is used for the YAML rather than PyYAML, which is not
pinned in backend/requirements.txt and has previously broken CI here as an
unpinned transitive import.
"""

import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "keepalive.yml"
SCRIPT = REPO_ROOT / "scripts" / "keepalive.sh"

# Render stops a free web service after ~15 minutes of inactivity.
RENDER_IDLE_TIMEOUT_SECONDS = 15 * 60

BASH = shutil.which("bash")
CURL = shutil.which("curl")
needs_shell = pytest.mark.skipif(
    not BASH or not CURL,
    reason="behavioural tests need bash and curl (both present on ubuntu-latest CI)",
)


# ── contract layer ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def workflow_text():
    assert WORKFLOW.exists(), f"keepalive workflow not found at {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script_text():
    assert SCRIPT.exists(), f"keepalive script not found at {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


def _int_setting(text, name):
    match = re.search(rf'{name}:\s*"?(\d+)"?', text)
    assert match, f"{name} is not defined in the keepalive workflow"
    return int(match.group(1))


# ── production configuration resolution (N1) ─────────────────────────────
#
# The budget test used to hardcode `attempts, curl_max_time, retry_sleep =
# 3, 120, 20`. Those are the script's defaults, so the test validated an
# assumption rather than reality: raising CURL_MAX_TIME in the workflow to
# 300 pushes the real worst case to 60.6 min against a 55 min timeout, and
# the test would still have computed 51.6 min and passed.
#
# Production resolves each value the same way bash does: the workflow's env
# block if it sets one, otherwise the `${NAME:-default}` in the script. The
# helpers below resolve identically, so the test cannot drift from the
# configuration it is guarding.

def _workflow_override(workflow, name):
    """The workflow's env value for `name`, or None if it does not set one."""
    match = re.search(rf'{name}:\s*"?(\d+)"?', workflow)
    return int(match.group(1)) if match else None


def _script_default(script, name):
    """The `NAME="${NAME:-123}"` fallback baked into scripts/keepalive.sh."""
    pattern = re.escape(name) + r'="\$\{' + re.escape(name) + r':-(\d+)\}"'
    match = re.search(pattern, script)
    assert match, (
        f"{name} has no ${{{name}:-default}} in {SCRIPT.name}; the budget test "
        "can no longer resolve what production will use"
    )
    return int(match.group(1))


def _effective_setting(workflow, script, name):
    """Exactly what the running job will see: override first, else default."""
    override = _workflow_override(workflow, name)
    return override if override is not None else _script_default(script, name)


def test_workflow_exists_and_is_scheduled(workflow_text):
    assert "schedule:" in workflow_text
    assert "cron:" in workflow_text
    assert "workflow_dispatch:" in workflow_text


def test_workflow_invokes_the_tested_script(workflow_text):
    """The logic must stay in the file that has behavioural coverage."""
    assert "scripts/keepalive.sh" in workflow_text
    assert "actions/checkout" in workflow_text, (
        "the workflow runs a repo script, so it must check the repo out first"
    )


def test_ping_interval_is_shorter_than_render_idle_timeout(workflow_text):
    interval = _int_setting(workflow_text, "INTERVAL_SECONDS")
    assert interval < RENDER_IDLE_TIMEOUT_SECONDS, (
        f"ping interval {interval}s >= idle timeout {RENDER_IDLE_TIMEOUT_SECONDS}s: "
        "the service would sleep between pings"
    )


def test_job_timeout_can_absorb_a_full_loop_plus_a_worst_case_probe(workflow_text, script_text):
    """
    Regression guard for a real defect: a 50 min loop under a 55 min timeout
    could be SIGKILLed mid-run and surface as a false failure.

      worst case = (LOOP_SECONDS - 1) + attempts*curl_max_time
                                      + (attempts-1)*retry_sleep

    Every input is resolved from production config (workflow override, else
    script default), so raising CURL_MAX_TIME or PROBE_ATTEMPTS anywhere is
    reflected here instead of silently invalidating the assertion.
    """
    loop = _effective_setting(workflow_text, script_text, "LOOP_SECONDS")
    attempts = _effective_setting(workflow_text, script_text, "PROBE_ATTEMPTS")
    curl_max_time = _effective_setting(workflow_text, script_text, "CURL_MAX_TIME")
    retry_sleep = _effective_setting(workflow_text, script_text, "PROBE_RETRY_SLEEP")
    timeout_minutes = _int_setting(workflow_text, "timeout-minutes")

    worst_case = (loop - 1) + attempts * curl_max_time + (attempts - 1) * retry_sleep

    assert worst_case < timeout_minutes * 60, (
        f"worst-case run {worst_case}s (loop={loop} attempts={attempts} "
        f"curl_max={curl_max_time} retry_sleep={retry_sleep}) exceeds timeout "
        f"{timeout_minutes * 60}s: the job can be killed mid-loop and reported "
        "as a false failure"
    )


def test_budget_inputs_resolve_from_production_config_not_constants(workflow_text, script_text):
    """
    N1: proves the resolution the budget test depends on.

    Each probe setting must be findable in production config, and a workflow
    override must win over the script default — otherwise the budget test
    silently reverts to validating hardcoded assumptions.
    """
    for name in ("LOOP_SECONDS", "PROBE_ATTEMPTS", "CURL_MAX_TIME", "PROBE_RETRY_SLEEP"):
        assert _script_default(script_text, name) > 0
        assert _effective_setting(workflow_text, script_text, name) > 0

    # The workflow currently pins LOOP_SECONDS; the probe knobs fall through
    # to the script. Both directions must resolve correctly.
    assert _effective_setting(workflow_text, script_text, "LOOP_SECONDS") == _int_setting(
        workflow_text, "LOOP_SECONDS"
    )

    # Override precedence, proven against synthetic config so the assertion
    # does not depend on today's values.
    fake_workflow = 'env:\n      CURL_MAX_TIME: "300"\n'
    fake_script = 'CURL_MAX_TIME="${CURL_MAX_TIME:-120}"\n'
    assert _script_default(fake_script, "CURL_MAX_TIME") == 120
    assert _effective_setting(fake_workflow, fake_script, "CURL_MAX_TIME") == 300
    assert _effective_setting("", fake_script, "CURL_MAX_TIME") == 120


def test_cold_threshold_sits_between_warm_noise_and_a_cold_start(workflow_text):
    threshold = _int_setting(workflow_text, "WARM_THRESHOLD_SECONDS")
    assert 2 <= threshold <= 30, (
        f"WARM_THRESHOLD_SECONDS={threshold} is outside the useful band "
        "(above warm noise ~1.5s, well below a ~93s cold start)"
    )


def test_concurrent_runs_are_prevented(workflow_text):
    assert "concurrency:" in workflow_text
    assert "cancel-in-progress: false" in workflow_text


def test_probes_the_readiness_endpoint(workflow_text):
    assert "/healthz" in workflow_text


# ── behavioural layer ─────────────────────────────────────────────────────

@contextmanager
def stub_server(delays=(0,), statuses=(200,)):
    """
    Local HTTP stub.

    Both `delays` (seconds) and `statuses` (HTTP codes) are per-request
    sequences whose LAST value repeats for any further requests, so a script
    that keeps polling settles on the final element rather than running off
    the end.

    N2: `statuses` used to be a single code applied to every request, which
    made the retry-then-succeed branch structurally untestable — and that
    branch is the redeploy tolerance that stops a deploy from turning the
    warm-keeper red. `statuses=(500, 500, 200)` now expresses
    failure -> failure -> success directly, with no change to the script.

    Yields (url, get_request_count).
    """
    state = {"count": 0}

    def _at(sequence, index):
        return sequence[index] if index < len(sequence) else sequence[-1]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            index = state["count"]
            state["count"] += 1
            delay = _at(delays, index)
            if delay:
                time.sleep(delay)
            self.send_response(_at(statuses, index))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass  # keep pytest output clean

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/healthz", lambda: state["count"]
    finally:
        server.shutdown()
        server.server_close()


def run_keepalive(url, **env_overrides):
    env = {
        "HEALTH_URL": url,
        "LOOP_SECONDS": "2",
        "INTERVAL_SECONDS": "1",
        "WARM_THRESHOLD_SECONDS": "1",
        "CURL_MAX_TIME": "10",
        "PROBE_ATTEMPTS": "2",
        "PROBE_RETRY_SLEEP": "0",
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=180
    )


def _reported_max_seconds(stdout):
    match = re.search(r"max: ([\d.]+)s", stdout)
    assert match, f"summary line missing from output:\n{stdout}"
    return float(match.group(1))


@needs_shell
def test_script_exists_and_is_syntactically_valid():
    assert SCRIPT.exists(), f"keepalive script not found at {SCRIPT}"
    result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, f"bash syntax error: {result.stderr}"


@needs_shell
def test_warm_service_exits_zero():
    with stub_server(delays=(0,)) as (url, _):
        result = run_keepalive(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unexpected cold: 0" in result.stdout


@needs_shell
def test_cold_after_warm_fails():
    """
    THE mutation-killing test.

    First ping fast (service warm), later pings slow. The script must fail,
    because a service that goes cold while the warm-keeper is running is the
    exact regression this milestone exists to surface. A build whose
    cold-detection branch exits 0 passes every contract assertion and fails
    this one.
    """
    with stub_server(delays=(0, 1.6)) as (url, _):
        result = run_keepalive(url)
    assert result.returncode == 1, (
        "cold-after-warm did not fail the run:\n" + result.stdout + result.stderr
    )
    assert "::error::" in result.stdout
    assert "not staying warm" in result.stdout


@needs_shell
def test_slow_first_ping_is_tolerated():
    """
    A slow FIRST ping arrives after an uncontrolled scheduling gap, so it is
    expected, not a regression. Failing on it would produce constant red runs
    -- alert fatigue is its own form of blindness.
    """
    with stub_server(delays=(1.6, 0)) as (url, _):
        result = run_keepalive(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold tolerated" in result.stdout


@needs_shell
def test_unhealthy_endpoint_fails():
    with stub_server(delays=(0,), statuses=(503,)) as (url, _):
        result = run_keepalive(url)
    assert result.returncode == 1
    assert "did not return 200" in result.stdout


@needs_shell
def test_probe_retries_then_succeeds():
    """
    N2: failure -> failure -> success must NOT fail the run.

    This is the redeploy-tolerance path: while Render swaps instances the
    endpoint briefly stops answering, and a warm-keeper that went red for
    every deploy would train everyone to ignore it. Previously untestable
    because the stub applied one status to all requests.
    """
    with stub_server(statuses=(500, 500, 200)) as (url, get_count):
        result = run_keepalive(url, LOOP_SECONDS=0, PROBE_ATTEMPTS=3, PROBE_RETRY_SLEEP=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert get_count() == 3, f"expected 3 attempts, got {get_count()}"
    assert "pings: 1" in result.stdout  # one logical ping, three HTTP attempts


@needs_shell
def test_probe_gives_up_after_exhausting_attempts():
    """The opposite bound: retries are finite, and exhausting them fails."""
    with stub_server(statuses=(500,)) as (url, get_count):
        result = run_keepalive(url, LOOP_SECONDS=0, PROBE_ATTEMPTS=3, PROBE_RETRY_SLEEP=0)

    assert result.returncode == 1
    assert get_count() == 3, f"expected exactly 3 attempts, got {get_count()}"
    assert "did not return 200 after 3 attempts" in result.stdout


@needs_shell
def test_transient_failure_mid_loop_does_not_fail_the_run():
    """
    A blip on a LATER ping is also tolerated, provided a retry recovers it —
    the run should only fail on a sustained outage or a genuine cold start.
    """
    # ping 1 -> 200; ping 2 -> 500 then 200 on retry; ping 3 -> 200
    with stub_server(statuses=(200, 500, 200, 200)) as (url, _):
        result = run_keepalive(url, PROBE_ATTEMPTS=2, PROBE_RETRY_SLEEP=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unexpected cold: 0" in result.stdout


@needs_shell
def test_single_ping_mode_sends_exactly_one_request():
    """
    LOOP_SECONDS=0 is the kill switch for when an external uptime monitor
    owns the job. It must send exactly one request -- not zero (which would
    keep nothing warm while reporting success) and not a loop.
    """
    with stub_server(delays=(0,)) as (url, get_count):
        result = run_keepalive(url, LOOP_SECONDS=0)
        requests_made = get_count()
    assert result.returncode == 0, result.stdout + result.stderr
    assert requests_made == 1, f"expected exactly 1 request, got {requests_made}"
    assert "pings: 1" in result.stdout


@needs_shell
def test_latency_is_reported_with_sub_second_precision():
    """
    Regression guard: latency was truncated with ${x%%.*}, so a 1.432 s ping
    reported as "1s" and a 9.999 s ping passed a 10 s threshold. The reported
    maximum must retain the fraction.
    """
    with stub_server(delays=(1.5,)) as (url, _):
        result = run_keepalive(url, LOOP_SECONDS=0, WARM_THRESHOLD_SECONDS=30)
    assert result.returncode == 0, result.stdout + result.stderr
    reported = _reported_max_seconds(result.stdout)
    assert 1.2 <= reported < 3.0, f"implausible reported max: {reported}s"
    assert reported != int(reported), (
        f"reported max {reported}s lost its fractional part - latency is being truncated"
    )
