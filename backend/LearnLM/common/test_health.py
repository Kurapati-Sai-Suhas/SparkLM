"""Health endpoint contract (M6): Render's checker gets a cheap, honest probe."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_healthz_is_public_and_reports_ok():
    response = APIClient().get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
