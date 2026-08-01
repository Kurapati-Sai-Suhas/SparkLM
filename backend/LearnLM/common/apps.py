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


def verify_password_hashers():
    """
    Confirm every configured hasher can actually be constructed, and that the
    preferred one is the tuned Argon2id. Returns True/False; never raises.

    Why this exists: M3 documents one hazard it cannot otherwise detect.
    Once an account has logged in post-migration its stored hash IS Argon2,
    so dropping `common.hashers.TunedArgon2PasswordHasher` from
    PASSWORD_HASHERS — or letting the `argon2-cffi` pin fall out of
    requirements.txt — makes identify_hasher() raise on every migrated
    account. Django catches that and reports an ordinary **failed login**.
    Every migrated user is locked out and nothing in the logs says why.

    test_removing_argon2_locks_out_migrated_users pins the behaviour, but a
    test cannot see a bad deploy. This turns a silent lockout into one line
    in the deploy log, the same way verify_cache_backend() does for the cache.

    Advisory on purpose: it must never stop the service booting. A service
    that refuses to start helps nobody, and the operator may be mid-rollback.
    """
    from django.conf import settings
    from django.contrib.auth.hashers import get_hashers

    configured = settings.PASSWORD_HASHERS
    try:
        hashers = get_hashers()
    except Exception as exc:  # noqa: BLE001 - advisory probe, must not raise
        logger.error(
            "PASSWORD HASHERS UNUSABLE: %s. Configured: %s. Logins will fail "
            "as ordinary bad-credential errors. If argon2-cffi is missing, "
            "reinstall it — see docs/DEPLOYMENT.md.",
            exc, configured,
        )
        return False

    algorithms = [h.algorithm for h in hashers]
    if "argon2" not in algorithms:
        logger.error(
            "ARGON2 HASHER ABSENT (configured: %s). Any account that has "
            "logged in since the M3 deploy is stored as argon2$ and CANNOT "
            "authenticate — Django reports it as a wrong password. Rolling "
            "back is a REORDER, never a REMOVAL: put PBKDF2 first and keep "
            "the Argon2 entry. See docs/DEPLOYMENT.md.",
            configured,
        )
        return False

    if algorithms[0] != "argon2":
        # Not an error: this is exactly what a deliberate rollback looks like.
        logger.warning(
            "Preferred password hasher is %r, not argon2. New and rehashed "
            "passwords will use it. Expected during a rollback; unexpected "
            "otherwise.",
            algorithms[0],
        )

    logger.debug("Password hashers verified: %s", algorithms)
    return True


class CommonConfig(AppConfig):
    """
    v2 shared-services app (frozen architecture §9). Holds no models; it is
    installed so its management commands (partition maintenance, wrapper
    audit, password-hash status) are discoverable.
    """

    name = "common"
    verbose_name = "SparkLM v2 shared services"

    def ready(self):
        # Runs once per process, including the migrate/collectstatic steps of
        # the Render start chain, so a misconfigured cache or a missing
        # password hasher is visible at deploy time rather than after a
        # security control silently lapses or users start being locked out.
        verify_cache_backend()
        verify_password_hashers()
