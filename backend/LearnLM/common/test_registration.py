"""
M7/M9 registration hardening: email must be present, well-formed, and
unique (case-insensitive); username must be unique (case-insensitive);
password must satisfy Django's configured validators — including
complexity — none of which were actually enforced before
UserSerializer.validate_password existed (AUTH_PASSWORD_VALIDATORS was
configured but nothing invoked it).
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


@pytest.mark.django_db
def test_registration_rejects_duplicate_username_case_insensitively():
    get_user_model().objects.create_user(
        username="TakenName", password="pw-not-relevant", email="a@test.com"
    )
    response = _register(
        {"username": "takenname", "password": "GoodPass123!", "email": "b@test.com"}
    )
    assert response.status_code == 400
    assert "username" in response.json()


@pytest.mark.django_db
@pytest.mark.parametrize("password,missing", [
    ("alllowercase123!", "uppercase"),
    ("NoDigitsHere!", "number"),
    ("NoSymbols123", "symbol"),
    ("Short1!", "length"),  # under Django's configured min_length=8
])
def test_registration_rejects_weak_passwords(password, missing):
    response = _register(
        {"username": f"weak_{missing}", "password": password, "email": f"{missing}@test.com"}
    )
    assert response.status_code == 400
    assert "password" in response.json()


@pytest.mark.django_db
def test_registration_accepts_a_password_meeting_every_rule():
    response = _register(
        {"username": "strongpassuser", "password": "Str0ng!Pass", "email": "strong@test.com"}
    )
    assert response.status_code in (200, 201)
