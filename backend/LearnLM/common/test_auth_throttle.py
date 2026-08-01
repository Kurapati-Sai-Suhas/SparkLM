"""
Auth-scope throttle regression tests (frozen architecture §7).

The token endpoint is the credential-stuffing surface: it must allow the
configured 'auth' rate (10/minute per anonymous IP) and reject the request
after it — regardless of whether the attempted credentials are valid.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient


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

        # First 10 attempts pass the throttle (and fail authentication).
        for _ in range(10):
            response = client.post(
                url, {"username": "authuser", "password": "wrong"}, format="json"
            )
            assert response.status_code == 401

        # The 11th within the window is throttled — even with CORRECT
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

        # Same client, three different Render-internal last hops.
        for i in range(10):
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
            for i in range(15)
        ]
        assert 429 not in codes, (
            "documented limitation: rotating the client hop evades the limit"
        )
    finally:
        cache.clear()
