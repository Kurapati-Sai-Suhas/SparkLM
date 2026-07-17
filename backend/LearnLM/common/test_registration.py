"""
M7 registration hardening: email must be present, well-formed, and
unique (case-insensitive). Previously all three were unchecked.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient


def _register(payload):
    return APIClient().post(reverse("register"), payload, format="json")


@pytest.mark.django_db
def test_registration_rejects_missing_and_malformed_email():
    assert _register({"username": "u1", "password": "GoodPass123!"}).status_code == 400
    assert _register(
        {"username": "u2", "password": "GoodPass123!", "email": "not-an-email"}
    ).status_code == 400


@pytest.mark.django_db
def test_registration_rejects_duplicate_email_case_insensitively():
    get_user_model().objects.create_user(
        username="existing", password="pw-not-relevant", email="taken@test.com"
    )
    response = _register(
        {"username": "someoneelse", "password": "GoodPass123!", "email": "TAKEN@test.com"}
    )
    assert response.status_code == 400
    assert "email" in response.json()


@pytest.mark.django_db
def test_registration_accepts_a_valid_email_and_normalizes_it():
    response = _register(
        {"username": "newuser", "password": "GoodPass123!", "email": "New.User@Test.com"}
    )
    assert response.status_code in (200, 201)
    user = get_user_model().objects.get(username="newuser")
    assert user.email == "new.user@test.com"
