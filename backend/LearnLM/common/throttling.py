"""
common.throttling — throttle classes that identify the real client on Render.

WHY THIS EXISTS
---------------
Rate limiting was measured completely non-functional in production. The cause
was not the cache and not the throttle configuration: it was client
identification.

DRF's `NUM_PROXIES = 1` means "trust one reverse proxy, so the LAST
X-Forwarded-For entry is the client". That is the correct, unspoofable choice
on a topology with exactly one stable proxy. Render is not that topology — the
last hop is Render's internal load balancer, and Render rotates across several
of them. Measured on production, 12 sequential requests to /api/token/ landed
in THREE different buckets:

    10.31.175.104  →  10.26.174.7  →  10.27.203.252

No bucket ever reached the 10/min limit, so no request was ever throttled.
The brake on credential stuffing and the 10/min cap protecting the *metered*
Judge0 API were both silently inert.

THE TRADE-OFF (deliberate, please read before changing this)
------------------------------------------------------------
These classes key on the FIRST X-Forwarded-For entry, which on Render is the
real client address. That entry is client-supplied, so a determined attacker
can rotate it and evade the limit.

That is a real weakening of the threat model versus `NUM_PROXIES = 1`, and it
was chosen with eyes open: the previous behaviour required no evasion effort
at all, because there was no limit. A spoofable limit strictly dominates an
absent one. `common/test_auth_throttle.py` documents both halves of this —
that the limit now works, and that spoofing defeats it.

If Render ever exposes a trusted, proxy-set client-IP header, or the service
moves behind a single stable proxy, prefer that and revert to NUM_PROXIES.

`REST_FRAMEWORK["NUM_PROXIES"]` is intentionally left in settings: these
classes bypass it, but it still governs any stock DRF throttle added later,
so it remains a sane fallback rather than dead configuration.
"""

from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)


class ClientIPIdentMixin:
    """Identify the client by the first X-Forwarded-For hop, not the last."""

    def get_ident(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            # "client, proxy1, proxy2" — the client is first. Everything after
            # it is infrastructure, and on Render that infrastructure rotates.
            client = forwarded.split(",")[0].strip()
            if client:
                return client
        # No XFF (local dev, direct connection) — REMOTE_ADDR is the client.
        return request.META.get("REMOTE_ADDR")


class ClientIPScopedRateThrottle(ClientIPIdentMixin, ScopedRateThrottle):
    """ScopedRateThrottle for the 'auth', 'auth-refresh', 'judge0' and
    'recommend' scopes. Authenticated callers are still keyed by user pk;
    the ident override only affects anonymous ones."""


class ClientIPAnonRateThrottle(ClientIPIdentMixin, AnonRateThrottle):
    """Global anonymous rate, keyed by the real client address."""


class ClientIPUserRateThrottle(ClientIPIdentMixin, UserRateThrottle):
    """Global authenticated rate. Keyed by user pk when authenticated, so the
    ident override matters only on the anonymous fallback path."""
