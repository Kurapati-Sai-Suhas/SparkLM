import logging
import os
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)

CACHE_PROBE_KEY = "sparklm:startup:cache-probe"


def verify_cache_backend():
    """
    Confirm the configured cache actually round-trips, and say so loudly if
    it does not. Returns True/False; never raises.

    Why this exists: production ran with a cache that accepted writes and
    returned nothing. Django's cache has no failure signal for that, so three
    features degraded in total silence —

      * DRF throttling (every scope). Throttle history lives in the cache, so
        `cache.get(key, [])` returned [] on every request and no limit was
        ever reached. The credential-stuffing brake on /token/ and the 10/min
        cap protecting the metered Judge0 API were both inert.
      * The curriculum DAG cache (hybrid_router FIX-04), so the NetworkX graph
        was rebuilt from the database on every recommendation request.
      * The 60-second leaderboard cache.

    Nothing errored and the test suite stayed green: the tests use
    LocMemCache, which works, so they cannot see an environment-only fault.
    common/test_auth_throttle.py already asserts that the 11th login returns
    429 and that XFF spoofing cannot mint a fresh bucket — both pass, and
    both were powerless here. A boot-time probe is the cheapest way to turn a
    silent misconfiguration into one line in the deploy log.

    Deliberately advisory: a cache fault must never stop the service from
    booting. Degraded caching is survivable; refusing to start is not.
    """
    from django.conf import settings
    from django.core.cache import cache

    backend = settings.CACHES.get("default", {}).get("BACKEND", "<unset>")
    sentinel = f"{os.getpid()}-{time.time()}"

    try:
        cache.set(CACHE_PROBE_KEY, sentinel, 30)
        observed = cache.get(CACHE_PROBE_KEY)
    except Exception as exc:  # noqa: BLE001 - advisory probe, must not raise
        logger.error(
            "CACHE UNAVAILABLE (%s): %s. Throttling, the DAG cache and the "
            "leaderboard cache are all disabled. Check REDIS_URL.",
            backend, exc,
        )
        return False

    if observed != sentinel:
        logger.error(
            "CACHE NOT PERSISTING (%s): wrote a probe value and read back %r. "
            "Throttling is INERT (no brute-force brake on /token/, no cap on "
            "the metered Judge0 API), the curriculum DAG is rebuilt from the "
            "database on every request, and the leaderboard cache is bypassed. "
            "Most likely REDIS_URL is unset or points at a dead instance — see "
            "docs/DEPLOYMENT.md.",
            backend, observed,
        )
        return False

    logger.debug("Cache backend verified: %s round-trips correctly.", backend)
    return True


class CommonConfig(AppConfig):
    """
    v2 shared-services app (frozen architecture §9). Holds no models; it is
    installed so its management commands (partition maintenance, wrapper
    audit) are discoverable.
    """

    name = "common"
    verbose_name = "SparkLM v2 shared services"

    def ready(self):
        # Runs once per process, including the migrate/collectstatic steps of
        # the Render start chain, so a misconfigured cache is visible at
        # deploy time rather than after a security control silently lapses.
        verify_cache_backend()
