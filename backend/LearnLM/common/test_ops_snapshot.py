"""
/healthz operational snapshot (M4 Phase B).

Every milestone so far has been slowed by the same gap: no way to see runtime
state without opening a shell. Phase A shipped admission control that sheds
requests under load and nobody could tell whether it had ever fired.

The constraints are as important as the feature, so they are tested rather
than trusted:

  * the STATUS CODE must still depend on database reachability alone —
    widening a health check's failure conditions is how a monitoring signal
    becomes an outage;
  * the payload must stay free of anything useful to someone mapping the
    service, because Render polls this endpoint unauthenticated;
  * an observability field must never be able to break the probe it rides on.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from common.models import MaintenanceRun


def payload(client=None, ops=True):
    """`ops=1` opts into the maintenance snapshot; see common/health.py."""
    url = reverse("healthz") + ("?ops=1" if ops else "")
    response = (client or Client()).get(url)
    return response.status_code, json.loads(response.content)


@pytest.mark.django_db
class TestSnapshotContent:
    def test_healthz_still_reports_ok(self):
        status, body = payload()
        assert status == 200
        assert body["status"] == "ok"

    def test_admission_counters_are_present(self):
        _, body = payload()
        assert set(body["admission"]) == {"admitted", "rejected", "limit"}
        assert all(isinstance(v, int) for v in body["admission"].values())

    def test_maintenance_is_null_before_any_sweep_has_run(self):
        _, body = payload()
        assert body["maintenance"] == {"last_run_age_seconds": None, "succeeded": None}

    def test_maintenance_age_is_reported_after_a_sweep(self):
        MaintenanceRun.record("run_maintenance", True, 1234, "all ok")
        _, body = payload()
        assert body["maintenance"]["succeeded"] is True
        assert 0 <= body["maintenance"]["last_run_age_seconds"] < 30

    def test_a_stale_heartbeat_is_visible(self):
        """The whole point: a job that stopped must be detectable."""
        run = MaintenanceRun.record("run_maintenance", True)
        MaintenanceRun.objects.filter(pk=run.pk).update(
            last_run_at=timezone.now() - timedelta(days=3)
        )
        _, body = payload()
        assert body["maintenance"]["last_run_age_seconds"] > 2 * 86400

    def test_a_failed_sweep_is_visible(self):
        MaintenanceRun.record("run_maintenance", False, 10, "failed: calculate_decay")
        _, body = payload()
        assert body["maintenance"]["succeeded"] is False


@pytest.mark.django_db
class TestSnapshotConstraints:
    def test_a_stale_heartbeat_does_not_change_the_status_code(self):
        """
        A stale sweep is something to look at, not a reason to tell the load
        balancer to stop routing traffic. Render gates on this endpoint.
        """
        run = MaintenanceRun.record("run_maintenance", False)
        MaintenanceRun.objects.filter(pk=run.pk).update(
            last_run_at=timezone.now() - timedelta(days=30)
        )
        status, body = payload()
        assert status == 200
        assert body["status"] == "ok"

    def test_the_payload_leaks_nothing_identifying(self):
        MaintenanceRun.record("run_maintenance", True, 1, "scanned 66, decayed 12")
        _, body = payload()
        blob = json.dumps(body).lower()
        for forbidden in ["password", "secret", "token", "onrender", "postgres",
                          "redis", "@", "django", "version"]:
            assert forbidden not in blob, f"health payload leaks {forbidden!r}"

    def test_the_detail_string_is_not_exposed(self):
        # Sub-task detail can contain an exception message; it belongs in the
        # log, not in a public endpoint.
        MaintenanceRun.record("run_maintenance", False, 1, "OperationalError: host x.y.z")
        _, body = payload()
        assert "x.y.z" not in json.dumps(body)

    def test_a_broken_snapshot_cannot_break_the_probe(self):
        """
        An observability field must not be able to take down readiness.

        Patched at common.models, not common.health: health.py imports the
        model inside the function (deliberately, to keep module import order
        free of an ORM dependency), so it is resolved at call time.
        """
        with patch("common.models.MaintenanceRun") as broken:
            broken.objects.filter.side_effect = RuntimeError("boom")
            status, body = payload()
        assert status == 200
        assert body["status"] == "ok"
        assert body["maintenance"]["last_run_age_seconds"] is None

    def test_database_failure_still_returns_503(self):
        """The one condition that must keep failing."""
        with patch("common.health.connection") as conn:
            conn.cursor.side_effect = RuntimeError("db down")
            response = Client().get(reverse("healthz"))
        assert response.status_code == 503
        assert json.loads(response.content)["database"] == "unreachable"


@pytest.mark.django_db
def test_admission_counters_increment_on_a_real_request():
    from common.middleware import admission_stats

    before = admission_stats()["admitted"]
    Client().get(reverse("dashboard-bootstrap"))   # 401, but still admitted
    assert admission_stats()["admitted"] > before


@pytest.mark.django_db
class TestHotPathStaysCheap:
    """
    /healthz is the most-polled endpoint in the system: Render's health check
    plus the warm-keeper every 5 minutes for 45 minutes per run. The
    maintenance heartbeat needs a database read and changes once a day, so
    doubling the query count on every poll to carry it would be permanent
    waste that nobody would ever notice or remove.
    """

    def test_the_default_path_makes_exactly_one_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client = Client()
        client.get(reverse("healthz"))          # warm
        with CaptureQueriesContext(connection) as queries:
            client.get(reverse("healthz"))

        assert len(queries) == 1, (
            f"/healthz ran {len(queries)} queries; the readiness probe must "
            f"stay at one. Move new fields behind ?ops=1."
        )

    def test_the_default_path_omits_maintenance(self):
        MaintenanceRun.record("run_maintenance", True)
        _, body = payload(ops=False)
        assert "maintenance" not in body
        assert "admission" in body, "in-memory counters are free and stay"

    def test_ops_opts_in(self):
        MaintenanceRun.record("run_maintenance", True)
        _, body = payload(ops=True)
        assert "maintenance" in body
