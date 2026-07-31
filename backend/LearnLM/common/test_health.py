"""Health endpoint contract (M6): Render's checker gets a cheap, honest probe."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_healthz_is_public_and_reports_ok():
    response = APIClient().get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_healthz_is_a_readiness_probe_not_just_liveness():
    """
    M1: healthz must keep round-tripping the database.

    The warm-keeper workflow pings this path to hold BOTH tiers awake --
    Render (idles ~15 min) and Neon (suspends ~5 min). If this endpoint were
    ever "optimised" into a static 200, the ping would still look healthy
    while the database quietly went cold, and a dead database would report
    200 instead of 503.
    """
    with CaptureQueriesContext(connection) as queries:
        response = APIClient().get(reverse("healthz"))

    assert response.status_code == 200
    assert len(queries) >= 1, (
        "healthz executed no SQL - it is no longer a readiness probe, so the "
        "keepalive would stop covering the database"
    )
