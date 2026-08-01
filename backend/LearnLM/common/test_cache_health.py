"""
Cache-health probe (SEC-B1 regression).

Production ran with a cache that accepted writes and returned nothing.
Django reports no error for that, so DRF throttling silently stopped
enforcing: `SimpleRateThrottle.allow_request` does
`self.history = self.cache.get(self.key, [])`, and an always-empty history
never reaches the limit.

The throttle tests in common/test_auth_throttle.py were correct and passing
throughout — they use LocMemCache, which works. No amount of throttle
testing could have caught an environment-only cache fault, which is why the
probe below tests the *cache*, and why the last test here encodes the causal
link between the two as an executable fact rather than a comment.

DummyCache is used to simulate the fault because it behaves exactly like the
production symptom: writes are accepted and discarded.
"""

import logging

import pytest
from django.core.cache import cache
from django.core.cache.backends.base import BaseCache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.apps import CACHE_PROBE_KEY, verify_cache_backend

DUMMY = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RAISING = {"default": {"BACKEND": "common.test_cache_health.RaisingCache"}}


class RaisingCache(BaseCache):
    """A backend that fails the way an unreachable Redis would."""

    def get(self, key, default=None, version=None):
        raise ConnectionError("simulated redis outage")

    def set(self, key, value, timeout=None, version=None):
        raise ConnectionError("simulated redis outage")

    def add(self, *a, **k):
        raise ConnectionError("simulated redis outage")

    def delete(self, *a, **k):
        raise ConnectionError("simulated redis outage")


class TestCacheProbe:
    @override_settings(CACHES=LOCMEM)
    def test_passes_with_a_working_cache(self):
        cache.clear()
        assert verify_cache_backend() is True

    @override_settings(CACHES=DUMMY)
    def test_detects_a_cache_that_accepts_writes_and_returns_nothing(self, caplog):
        """The exact production fault: no exception, no error, no persistence."""
        with caplog.at_level(logging.ERROR, logger="common.apps"):
            result = verify_cache_backend()

        assert result is False
        message = caplog.text
        assert "CACHE NOT PERSISTING" in message
        # The log has to name the consequences, or an operator scrolling past
        # it has no reason to care.
        assert "Throttling is INERT" in message
        assert "REDIS_URL" in message

    @override_settings(CACHES=RAISING)
    def test_reports_an_unreachable_backend_without_raising(self, caplog):
        """
        A cache outage must never stop the process from booting — this runs in
        AppConfig.ready(), so an exception here would prevent startup entirely.
        """
        with caplog.at_level(logging.ERROR, logger="common.apps"):
            result = verify_cache_backend()

        assert result is False
        assert "CACHE UNAVAILABLE" in caplog.text

    @override_settings(CACHES=LOCMEM)
    def test_probe_key_is_namespaced_and_short_lived(self):
        cache.clear()
        verify_cache_backend()
        assert CACHE_PROBE_KEY.startswith("sparklm:")
        assert cache.get(CACHE_PROBE_KEY) is not None


@pytest.mark.django_db
class TestCacheFailureDisablesThrottling:
    """
    The causal link, executable.

    common/test_auth_throttle.py proves the throttle works when the cache
    works. This proves it silently stops working when the cache does not —
    which is what happened in production, and is why the probe above exists.
    """

    @override_settings(CACHES=LOCMEM)
    def test_throttle_enforces_when_the_cache_persists(self):
        cache.clear()
        try:
            client = APIClient()
            url = reverse("token_obtain_pair")
            codes = [
                client.post(url, {"username": "ghost", "password": "wrong"}, format="json").status_code
                for _ in range(12)
            ]
            assert 429 in codes, "throttle should fire with a working cache"
            assert codes.index(429) == 10, f"expected the 11th to be throttled, got {codes}"
        finally:
            cache.clear()

    @override_settings(CACHES=DUMMY)
    def test_throttle_is_inert_when_the_cache_does_not_persist(self):
        """
        Reproduces SEC-B1 in CI. With a non-persisting cache the brute-force
        brake disappears entirely and nothing anywhere reports a problem.
        """
        client = APIClient()
        url = reverse("token_obtain_pair")
        codes = [
            client.post(url, {"username": "ghost", "password": "wrong"}, format="json").status_code
            for _ in range(12)
        ]
        assert 429 not in codes, (
            "expected the documented failure mode: a non-persisting cache "
            f"leaves throttling unenforced, got {codes}"
        )
        assert all(c == 401 for c in codes)
