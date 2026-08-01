"""
Auth-scope throttle regression tests (frozen architecture §7).

The token endpoint is the credential-stuffing surface: it must allow the
configured 'auth' rate (10/minute per anonymous IP) and reject the request
after it — regardless of whether the attempted credentials are valid.
"""

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient


def _limit(scope):
    """
    Read the configured allowance instead of hardcoding it.

    These tests previously pinned 'auth' at 10/minute as a literal. When
    Phase B lowered it to 5/minute (the rate limits sat ABOVE measured
    service capacity — one IP could 502 the instance without exceeding
    them), the literals broke. Deriving the number keeps the tests pinned
    to the *behaviour* — allow N, reject N+1 — at whatever N is configured.
    """
    return int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope].split("/")[0])


@pytest.mark.django_db
def test_token_endpoint_throttles_after_limit():
    # Throttle counters live in the default cache; isolate from other tests.
    cache.clear()
    try:
        get_user_model().objects.create_user(
            username="authuser", password="right-password-1", email="auth@test.com"
        )
        client = APIClient()
        url = reverse("token_obtain_pair")
        allowed = _limit("auth")

        # Every attempt up to the allowance passes the throttle (and fails
        # authentication).
        for _ in range(allowed):
            response = client.post(
                url, {"username": "authuser", "password": "wrong"}, format="json"
            )
            assert response.status_code == 401

        # The next one within the window is throttled — even with CORRECT
        # credentials, proving the brake sits in front of authentication.
        response = client.post(
            url, {"username": "authuser", "password": "right-password-1"}, format="json"
        )
        assert response.status_code == 429

        # Refresh uses an INDEPENDENT bucket: with the login bucket
        # exhausted, refresh must still be reachable (401 for a garbage
        # token — but never 429 from login traffic).
        refresh = client.post(
            reverse("token_refresh"), {"refresh": "not-a-token"}, format="json"
        )
        assert refresh.status_code == 401
    finally:
        cache.clear()  # never leak throttle state into other tests


@pytest.mark.django_db
def test_rotating_infrastructure_hops_share_one_bucket():
    """
    The bug this replaces a previous assertion for.

    Throttling was measured completely inert in production. With NUM_PROXIES=1
    DRF identified the client by the LAST X-Forwarded-For hop, which on Render
    is a rotating internal load balancer: 12 sequential requests to /token/
    landed in THREE buckets (10.31.175.104, 10.26.174.7, 10.27.203.252), none
    of which reached the limit.

    ClientIPScopedRateThrottle keys on the FIRST hop instead — the real client
    — so a constant client behind rotating infrastructure now shares one
    bucket and is correctly throttled.
    """
    cache.clear()
    try:
        client = APIClient()
        url = reverse("token_obtain_pair")

        # Same client, a different Render-internal last hop each time.
        for i in range(_limit("auth")):
            response = client.post(
                url, {"username": "ghost", "password": "wrong"}, format="json",
                HTTP_X_FORWARDED_FOR=f"203.0.113.7, 10.{i}.0.1",
            )
            assert response.status_code == 401

        response = client.post(
            url, {"username": "ghost", "password": "wrong"}, format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.7, 10.99.99.99",
        )
        assert response.status_code == 429, (
            "a constant client must share one bucket regardless of which "
            "internal hop forwarded the request"
        )
    finally:
        cache.clear()


@pytest.mark.django_db
def test_spoofing_the_client_hop_evades_the_throttle():
    """
    The accepted cost of the fix above, asserted rather than left implicit.

    The first X-Forwarded-For entry is client-supplied, so an attacker who
    rotates it gets a fresh bucket each time. This is a genuine weakening
    versus NUM_PROXIES=1 — and it was chosen deliberately, because the prior
    behaviour required no evasion effort at all: there was no working limit.
    A spoofable limit strictly dominates an absent one.

    If this test ever starts failing, someone has hardened client
    identification — which is good. Update it rather than deleting it, and
    revisit common/throttling.py.
    """
    cache.clear()
    try:
        client = APIClient()
        url = reverse("token_obtain_pair")

        codes = [
            client.post(
                url, {"username": "ghost", "password": "wrong"}, format="json",
                HTTP_X_FORWARDED_FOR=f"198.51.100.{i}, 10.0.0.1",
            ).status_code
            for i in range(_limit("auth") * 3)
        ]
        assert 429 not in codes, (
            "documented limitation: rotating the client hop evades the limit"
        )
    finally:
        cache.clear()


# Highest single-IP burst Phase B measured completing with ZERO failures.
# 40 concurrent produced 32 x 502 and a ~60 s production outage; 20 concurrent
# completed every request (slowly, 103 s). Sourced from measurement, not taste.
MEASURED_SAFE_CONCURRENCY = 20


def test_anonymous_rate_ceiling_stays_within_measured_capacity():
    """
    The defect Phase B found, pinned so it cannot come back.

    Rate limits had been chosen purely as security controls, with no
    reference to what the instance can actually serve. The result: one IP
    was permitted anon(30) + auth(10) = 40 requests/minute, and 40
    concurrent auth requests is exactly what returned 32 x 502 and took
    production down for ~60 s. The limit authorised the outage.

    Django's ASGI handler serialises sync views onto one worker thread, so
    capacity does not grow with arrival rate — the ceiling must be set below
    what the single queue can drain.

    If you need to raise these, raise CAPACITY first (paid instance, or real
    admission control) and re-measure; then update MEASURED_SAFE_CONCURRENCY
    with the new number and say where it came from.
    """
    anon, auth = _limit("anon"), _limit("auth")
    ceiling = anon + auth
    assert ceiling <= MEASURED_SAFE_CONCURRENCY, (
        f"one anonymous IP may send anon({anon}) + auth({auth}) = "
        f"{ceiling} req/min, above the {MEASURED_SAFE_CONCURRENCY} concurrent "
        f"requests measured to complete without failures. A client could "
        f"exhaust the service without exceeding its rate limit."
    )
