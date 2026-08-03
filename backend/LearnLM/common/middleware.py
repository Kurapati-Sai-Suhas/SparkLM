"""
common.middleware — bounded in-flight admission control (M4 Phase A).

The problem this solves, measured in Milestone 3:

    10 concurrent auth requests ->   7.1 s, 0 failures      healthy
    20 concurrent auth requests -> 103.0 s, 0 failures      degraded
    40 concurrent auth requests ->  32/40 -> 502, ~60 s outage

Nothing in the stack bounds the queue. Daphne has no concurrency flag and
asgiref 3.11 exposes no thread-count knob, so overload manifests as 502s
from Render's load balancer AFTER each request has already consumed twenty
seconds of work. That is the worst available failure mode: the work is
done, then discarded.

This turns it into a fast 503 with Retry-After, which a client can act on.

── Why 12, and not the 20 that "completed with zero failures" ──────────────
20 was the FAILURE boundary, and at 20 concurrent the responses took 103
seconds. Nobody waits 103 seconds. Because sync work here is substantially
serialised, the n-th queued request waits roughly n x W, so the limit should
come from acceptable wait time rather than from where the service breaks:

    W (login, server-side p50)  ~= 0.6 s
    acceptable wait             ~= 10 s
    limit                        = 10 / 0.6 ~= 16, rounded down to 12 for margin

Set ADMISSION_LIMIT=0 to disable entirely.

── ⚠ The limit is PER PROCESS ─────────────────────────────────────────────
threading.BoundedSemaphore is process-local, so N server processes admit
N x ADMISSION_LIMIT concurrently, not ADMISSION_LIMIT. That is correct today
because the Render topology runs exactly one Daphne process (512 MB will not
hold a second: ~202 MiB resident each). It stops being correct the moment
multiple workers land — which is Milestone 5's opening move — and the limit
must then be divided by the worker count, or moved to a shared counter in
Redis. Verified live: with ADMISSION_LIMIT=2 under real Daphne, 6 concurrent
requests returned 2x200 and 4x503 in 1.60 s wall clock, so the semaphore does
observe genuine concurrency in this deployment.

── Why middleware and not an ASGI wrapper ─────────────────────────────────
An ASGI-layer wrapper would reject before a worker thread is involved, which
sounds strictly better. It was not chosen because it was not needed: driving
10 concurrent requests through real Daphne showed peak simultaneous
occupancy of 10 INSIDE the Django middleware chain, so concurrency is fully
visible here. Middleware also keeps the change inside the existing
architecture — no edit to asgi.py, no second place where requests are
counted, and the AccessLogMiddleware above it still logs every rejection.
"""

import logging
import threading

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Paths that must answer even while shedding load. A health check that
# reports the service down under exactly the load where you need the truth
# is worse than no health check — and Render gates traffic on /healthz, so
# throttling it would turn a busy minute into a deploy-level outage.
EXEMPT_PATHS = frozenset({"/healthz", "/api/health/"})

# Process-local counters for the /healthz operational snapshot (M4 Phase B).
# Deliberately plain ints behind a lock rather than a metrics library: the
# question is "has admission control ever fired, and how often", and a
# collector agent costs the memory it would be measuring on a 512 MB
# instance. Reset on restart, which is correct — they describe this process.
_stats_lock = threading.Lock()
_stats = {"admitted": 0, "rejected": 0}


def admission_stats():
    """Snapshot of the counters. Safe to call from any thread."""
    with _stats_lock:
        snapshot = dict(_stats)
    snapshot["limit"] = int(getattr(settings, "ADMISSION_LIMIT", 0) or 0)
    return snapshot


def _bump(key):
    with _stats_lock:
        _stats[key] += 1


class AdmissionControlMiddleware:
    """Reject beyond N in-flight requests with 503 instead of queueing."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.limit = int(getattr(settings, "ADMISSION_LIMIT", 0) or 0)
        # Bounded, so a release() bug raises instead of silently inflating
        # the ceiling — a semaphore that quietly grows is worse than none.
        self._slots = threading.BoundedSemaphore(self.limit) if self.limit > 0 else None
        if self._slots is None:
            logger.warning(
                "Admission control DISABLED (ADMISSION_LIMIT=%s). Overload will "
                "queue until the load balancer returns 502.", self.limit,
            )

    def __call__(self, request):
        if self._slots is None or request.path in EXEMPT_PATHS:
            return self.get_response(request)

        # Non-blocking: the entire point is to fail fast rather than join
        # the queue we are trying to bound.
        if not self._slots.acquire(blocking=False):
            _bump("rejected")
            logger.warning(
                "Admission control REJECTED %s %s (limit=%d in flight)",
                request.method, request.path, self.limit,
            )
            response = JsonResponse(
                {
                    "error": "Server is at capacity. Please retry shortly.",
                    "detail": "admission_control",
                },
                status=503,
            )
            # Advisory, not a promise. Long enough that a retrying client
            # does not immediately re-add to the pressure it just hit.
            response["Retry-After"] = "5"
            return response

        _bump("admitted")
        try:
            return self.get_response(request)
        finally:
            self._slots.release()
