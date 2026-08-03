"""
common.health — liveness/readiness endpoint for the Render health check
(and the seed of the observability milestone).

Unauthenticated and unthrottled by design: platform health checkers carry
no credentials, and a throttle here would mark the service unhealthy
under normal probe cadence. The DB round-trip makes this a readiness
probe — a dead database reports 503 instead of a lying 200.

── Operational snapshot (M4 Phase B) ──────────────────────────────────────
The payload also carries a few operational counters. Every milestone so far
has been slowed by the same gap: no way to see runtime state without opening
a shell. Phase A added admission control that sheds requests under load and
nobody could tell whether it had ever fired.

Three deliberate constraints:

  * Integers and timestamps only. This endpoint is PUBLIC — Render polls it
    unauthenticated — so it must never carry usernames, configuration,
    versions or anything else useful to someone mapping the service.
  * The STATUS CODE still depends on database reachability alone. A stale
    maintenance heartbeat is a thing to look at, not a reason to tell the
    load balancer to stop routing traffic. Widening the failure conditions
    of a health check is how a monitoring signal becomes an outage.
  * No new dependency and no metrics stack. A collector agent costs the
    memory it would measure on a 512 MB instance.

The maintenance heartbeat needs a database read, so it is opt-in behind
`?ops=1`. This is the most-polled endpoint in the system — Render's health
check plus the warm-keeper every 5 minutes for 45 minutes per run — and
doubling its query count permanently, for a value that changes once a day,
is exactly the kind of cost that never gets noticed and never gets removed.
The default path stays at ONE query. Admission counters are in-memory and
therefore always included.

    GET /healthz          -> status + admission counters   (1 query)
    GET /healthz?ops=1    -> the above + maintenance        (2 queries)
"""

from django.db import connection
from django.http import JsonResponse

from common.middleware import admission_stats


def _maintenance_snapshot():
    """
    Age and outcome of the last maintenance sweep, or nulls if it has never
    run. Never raises: an observability field must not be able to take down
    the readiness probe it rides on.
    """
    try:
        from common.models import MaintenanceRun

        run = MaintenanceRun.objects.filter(task="run_maintenance").first()
        if run is None:
            return {"last_run_age_seconds": None, "succeeded": None}
        return {
            "last_run_age_seconds": int(run.age_seconds),
            "succeeded": run.succeeded,
        }
    except Exception:  # noqa: BLE001 — advisory field, never fatal
        return {"last_run_age_seconds": None, "succeeded": None}


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unhealthy", "database": "unreachable"}, status=503)

    body = {"status": "ok", "admission": admission_stats()}
    if request.GET.get("ops"):
        body["maintenance"] = _maintenance_snapshot()
    return JsonResponse(body)
