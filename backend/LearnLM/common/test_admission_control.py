"""
Admission control contract (M4 Phase A).

Milestone 3 measured this instance failing at 40 concurrent auth requests —
32 of 40 returned 502 after each had already consumed twenty-odd seconds of
work. Nothing in the stack bounds the queue: Daphne has no concurrency flag
and asgiref 3.11 exposes no thread-count knob. This converts that into a
fast 503 with Retry-After.

The tests exercise the middleware directly with a controllable inner
handler rather than through the full request stack. Driving real
concurrency through Django's test client to observe a semaphore would be
slow and flaky, and would test the test harness as much as the middleware.
A blocking stub handler gives deterministic control over exactly how many
requests are in flight at once.
"""

import json
import threading

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from common.middleware import EXEMPT_PATHS, AdmissionControlMiddleware


class BlockingHandler:
    """
    Stands in for the rest of the middleware chain. A call to `hold_path`
    parks until released, so the number of in-flight requests is exactly the
    number of such calls — which is the condition the semaphore bounds.
    Every other path returns immediately, as the real chain would.

    `hold_path` exists because the first version blocked *everything*: the
    exempt-path tests then parked on the same event and returned 200 only
    after the 10-second safety timeout expired. They passed — for the wrong
    reason, and at 10 seconds each. A test that passes on a timeout is
    testing the timeout.
    """

    HOLD_PATH = "/api/code/next/"

    def __init__(self, hold_path=HOLD_PATH):
        self.hold_path = hold_path
        self.release = threading.Event()
        self.entered = threading.Semaphore(0)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if request.path != self.hold_path:
            return HttpResponse("ok")
        self.entered.release()
        assert self.release.wait(timeout=10), "handler was never released"
        return HttpResponse("ok")


def build(limit, handler=None):
    handler = handler or (lambda request: HttpResponse("ok"))
    with override_settings(ADMISSION_LIMIT=limit):
        return AdmissionControlMiddleware(handler), handler


def get(path="/api/code/next/"):
    return RequestFactory().get(path)


# ── the core behaviour ───────────────────────────────────────────────────

def test_requests_below_the_limit_pass_through():
    middleware, _ = build(2)
    assert middleware(get()).status_code == 200


def test_requests_beyond_the_limit_are_shed_with_503():
    handler = BlockingHandler()
    middleware, _ = build(1, handler)

    occupier = threading.Thread(target=lambda: middleware(get()))
    occupier.start()
    handler.entered.acquire(timeout=5)          # the single slot is taken

    try:
        response = middleware(get())
        assert response.status_code == 503
        assert response["Retry-After"] == "5"
        body = json.loads(response.content)
        assert body["detail"] == "admission_control"
    finally:
        handler.release.set()
        occupier.join(timeout=5)


def test_the_shed_request_never_reaches_the_application():
    """
    The entire point. A 502 at the load balancer costs a full request's work
    before discarding it; this must cost nothing downstream.
    """
    handler = BlockingHandler()
    middleware, _ = build(1, handler)

    occupier = threading.Thread(target=lambda: middleware(get()))
    occupier.start()
    handler.entered.acquire(timeout=5)

    try:
        middleware(get())
        assert handler.calls == 1, "the rejected request entered the handler"
    finally:
        handler.release.set()
        occupier.join(timeout=5)


def test_slots_are_released_so_capacity_recovers():
    middleware, _ = build(1)
    for _ in range(5):
        assert middleware(get()).status_code == 200


def test_slots_are_released_even_when_the_view_raises():
    """
    A leaked slot permanently shrinks capacity, and the leak is invisible
    until the service wedges. The release must be in a finally block.
    """
    def exploding(request):
        raise ValueError("boom")

    middleware, _ = build(1, exploding)

    for _ in range(3):
        with pytest.raises(ValueError):
            middleware(get())

    # If the slot had leaked, this would 503 rather than raise.
    with pytest.raises(ValueError):
        middleware(get())


# ── exemptions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", sorted(EXEMPT_PATHS))
def test_health_endpoints_answer_even_while_shedding(path):
    """
    Render gates traffic on /healthz. A health check that reports failure
    under exactly the load where you need the truth turns a busy minute into
    a deploy-level outage.
    """
    handler = BlockingHandler()
    middleware, _ = build(1, handler)

    occupier = threading.Thread(target=lambda: middleware(get()))
    occupier.start()
    handler.entered.acquire(timeout=5)

    try:
        assert middleware(get(path)).status_code == 200
    finally:
        handler.release.set()
        occupier.join(timeout=5)


# ── the kill switch ──────────────────────────────────────────────────────

def test_limit_of_zero_disables_the_middleware_entirely():
    handler = BlockingHandler()
    middleware, _ = build(0, handler)

    occupier = threading.Thread(target=lambda: middleware(get()))
    second = threading.Thread(target=lambda: middleware(get()))
    occupier.start()
    handler.entered.acquire(timeout=5)

    try:
        # With no semaphore, a second concurrent request must reach the
        # handler rather than being shed.
        second.start()
        assert handler.entered.acquire(timeout=5), "second request was shed"
    finally:
        handler.release.set()
        occupier.join(timeout=5)
        second.join(timeout=5)


def test_disabling_admission_control_is_logged_loudly(caplog):
    """
    Running without a queue bound is a real risk posture, not a neutral
    default. If someone sets ADMISSION_LIMIT=0 to silence an incident, the
    next person reading the logs should see why 502s came back.
    """
    import logging

    with caplog.at_level(logging.WARNING):
        build(0)

    assert any("DISABLED" in r.getMessage() for r in caplog.records)


# ── the configured value ─────────────────────────────────────────────────

def test_the_shipped_limit_is_below_the_measured_failure_point():
    """
    Guards the number itself, not just the mechanism.

    Milestone 3 measured 20 concurrent completing in 103 seconds and 40
    concurrent failing outright. 20 is where the service BREAKS, not where
    it is usable — a 103-second response is a failure with a 200 status.
    The shipped limit is sized from acceptable wait time instead.

    Raising this past the measured degradation point requires re-measuring
    capacity, not editing the constant.
    """
    from django.conf import settings

    MEASURED_DEGRADED_CONCURRENCY = 20

    limit = int(getattr(settings, "ADMISSION_LIMIT", 0))
    assert 0 < limit < MEASURED_DEGRADED_CONCURRENCY, (
        f"ADMISSION_LIMIT={limit} is not below the concurrency measured to "
        f"produce 103-second responses ({MEASURED_DEGRADED_CONCURRENCY})."
    )
