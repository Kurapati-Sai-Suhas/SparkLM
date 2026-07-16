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
def test_auth_throttle_cannot_be_bypassed_by_xff_spoofing():
    """
    With NUM_PROXIES=1, only the proxy-appended LAST X-Forwarded-For hop
    identifies the client. Rotating fake upstream entries must therefore
    share one bucket. (Without NUM_PROXIES, DRF keys on the whole raw
    header and every spoofed value would mint a fresh bucket.)
    """
    cache.clear()
    try:
        client = APIClient()
        url = reverse("token_obtain_pair")

        for i in range(10):
            response = client.post(
                url, {"username": "ghost", "password": "wrong"}, format="json",
                HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.7",
            )
            assert response.status_code == 401

        # Different spoofed first hop, same real last hop → same bucket → 429.
        response = client.post(
            url, {"username": "ghost", "password": "wrong"}, format="json",
            HTTP_X_FORWARDED_FOR="10.99.99.99, 203.0.113.7",
        )
        assert response.status_code == 429
    finally:
        cache.clear()
