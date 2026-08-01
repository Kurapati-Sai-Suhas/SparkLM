"""
R1 — database connection pooling.

The bottleneck investigation attributed ~62% of authentication latency to a
single stage: the first database touch. The cause turned out to be a false
assumption baked into settings.py, not a slow database.

Django's ASGIHandler wraps every request in `async with
ThreadSensitiveContext()`, so each request runs its sync view on a FRESH
executor thread. django.db.connections is thread-critical, so every request
began life with no connection and opened one to Neon — a TCP + TLS +
Postgres startup handshake costing roughly 7.2 network round-trips.
CONN_MAX_AGE could never help: it persists a connection within a thread, and
the thread does not survive the request.

Measured against real Daphne before the fix: 12 requests -> 12 connections on
12 distinct thread ids; 20 requests -> 21 TCP sockets to Neon:5432.
After: 20 requests -> 3 sockets.

These tests pin the BEHAVIOUR (a pool shared across threads), not just the
settings literals, because the settings alone cannot express why they matter.

⚠ A note for whoever touches this next: the Django test client and
`AsyncClient` both reuse a single thread, so they CANNOT reproduce the
original defect — an early investigation using AsyncClient concluded
connections were reused and was wrong. Reproducing it needs real Daphne or
explicit threads, which is what test_pool_is_shared_across_threads does.
"""

import threading

import pytest
from django.conf import settings
from django.db import connections


DB = settings.DATABASES["default"]


def test_pooling_is_configured_instead_of_persistent_connections():
    """
    Guards against a silent revert to the slow path.

    Setting CONN_MAX_AGE back while KEEPING the pool is self-enforcing —
    Django raises ImproperlyConfigured. The dangerous edit is removing the
    pool and restoring CONN_MAX_AGE, which fails silently and simply makes
    every request pay the handshake again. Nothing else would catch it.
    """
    pool_options = DB.get("OPTIONS", {}).get("pool")
    assert pool_options, (
        "DATABASES['default']['OPTIONS']['pool'] is gone. Without it every "
        "ASGI request thread opens its own connection to Neon (~7.2 round "
        "trips). CONN_MAX_AGE is NOT a substitute — see this module's docstring."
    )
    assert DB.get("CONN_MAX_AGE") == 0, (
        "pooling requires CONN_MAX_AGE=0; Django refuses to combine them"
    )


def test_connection_health_checks_are_enabled():
    """
    Not a nicety. Neon's pooler drops idle connections and its free-tier
    compute auto-suspends, so a pooled connection can be dead on checkout.
    Observed live with this disabled:

        WARNING pool  discarding closed connection: <Connection [BAD] ...>
        ERROR   log   Service Unavailable: /healthz

    The pool discards the dead connection only AFTER the request has already
    failed. Django maps this setting onto psycopg_pool's checkout validation
    (django/db/backends/postgresql/base.py, `check=...`). It costs one round
    trip; the handshake it saves costs about 7.2.
    """
    assert DB.get("CONN_HEALTH_CHECKS") is True, (
        "without checkout validation, idle-dropped Neon connections surface "
        "as 5xx on the next request that receives one"
    )


def test_pool_bounds_are_sane():
    pool = DB["OPTIONS"]["pool"]
    assert 1 <= pool["min_size"] <= pool["max_size"], "min_size must not exceed max_size"
    assert pool["timeout"] > 0, "a request must not wait forever for a connection"
    assert pool["max_idle"] <= pool["max_lifetime"], (
        "idle recycling should happen before hard lifetime expiry"
    )


@pytest.mark.django_db(transaction=True)
def test_pool_is_shared_across_threads():
    """
    The regression that actually matters, reproduced the only way it can be.

    Each ASGI request gets its own thread, so the fix is only real if the
    pool OUTLIVES threads — it does, because psycopg_pool is held in
    DatabaseWrapper._connection_pools, a CLASS attribute shared by every
    per-thread wrapper instance.

    Run N queries from N distinct threads and assert the pool created far
    fewer than N connections. Pre-fix this would be N connections for N
    threads; the whole point of R1 is that it is not.
    """
    pool = connections["default"].pool
    assert pool is not None, "no pool configured — see the test above"

    threads_n = 12
    before = pool.get_stats()
    errors = []

    def query_from_new_thread():
        try:
            with connections["default"].cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone()[0] == 1
        except Exception as exc:            # surface, don't swallow
            errors.append(exc)
        finally:
            # Hand the connection back rather than leaking it with the thread.
            connections["default"].close()

    workers = [threading.Thread(target=query_from_new_thread) for _ in range(threads_n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=60)

    assert not errors, f"threads raised: {errors}"
    after = pool.get_stats()

    served = after["requests_num"] - before.get("requests_num", 0)
    opened = after["connections_num"] - before.get("connections_num", 0)

    assert served >= threads_n, (
        f"expected at least {threads_n} pool checkouts, saw {served} — "
        f"the threads may not have gone through the pool at all"
    )
    assert opened < threads_n, (
        f"{threads_n} threads opened {opened} connections. The pool is not "
        f"being shared across threads, so every ASGI request will pay the "
        f"Neon handshake again — this is exactly the R1 regression."
    )
    assert after["pool_size"] <= DB["OPTIONS"]["pool"]["max_size"], (
        "pool grew beyond max_size"
    )
