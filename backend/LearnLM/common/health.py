"""
common.health — liveness/readiness endpoint for the Render health check
(and the seed of the observability milestone).

Unauthenticated and unthrottled by design: platform health checkers carry
no credentials, and a throttle here would mark the service unhealthy
under normal probe cadence. The DB round-trip makes this a readiness
probe — a dead database reports 503 instead of a lying 200.
"""

from django.db import connection
from django.http import JsonResponse


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unhealthy", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})
