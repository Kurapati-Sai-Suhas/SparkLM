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
    assert response.json()["status"] == "ok"


@pytest.mark.django_db
def test_healthz_payload_stays_disciplined():
    """
    Replaces an exact-equality assertion on {"status": "ok"}.

    M4 Phase B added in-memory admission counters to the default payload, so
    equality no longer holds — but the property that assertion was really
    protecting does: this endpoint is PUBLIC and unauthenticated, so its
    payload must stay a short, known set of operational fields and never
    accrue anything useful to someone mapping the service.

    Adding a key here is a deliberate act. If this fails, decide whether the
    new field belongs on a public endpoint at all — and if it costs a query,
    put it behind ?ops=1 (see TestHotPathStaysCheap in test_ops_snapshot.py).
    """
    body = APIClient().get(reverse("healthz")).json()
    assert set(body) == {"status", "admission"}


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
