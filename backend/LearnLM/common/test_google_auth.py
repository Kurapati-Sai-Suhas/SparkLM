"""
GoogleAuthView (M9): verifies a Google ID token server-side, then
get-or-creates a local account and issues our own JWT pair. Google's own
verification call is mocked throughout — these tests are about OUR
account-linking and error-handling logic, not Google's library.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture(autouse=True)
def _isolate_throttle_state():
    """
    GoogleAuthView shares the 'auth' throttle bucket with password login, and
    every test here posts from the same (absent) client IP — so without this,
    throttle counters accumulate ACROSS tests in this module.

    That latent coupling was invisible while 'auth' allowed 10/minute: the
    module's requests happened to fit. When Phase B lowered the limit to
    5/minute, the later tests started receiving 429 instead of the status
    they assert. The tests were wrong, not the limit — a test must not depend
    on how many requests its neighbours happened to make.
    """
    cache.clear()
    yield
    cache.clear()


def _post_google(credential="fake-credential"):
    return APIClient().post(reverse("auth-google"), {"credential": credential}, format="json")


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_new_google_user_is_created_with_unusable_password():
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "fresh@gmail.com", "email_verified": True, "name": "Fresh User",
        }
        response = _post_google()

    assert response.status_code == 200
    data = response.json()
    assert data["created"] is True
    assert "access" in data and "refresh" in data

    user = User.objects.get(email="fresh@gmail.com")
    assert user.has_usable_password() is False
    assert user.username  # derived from the email local-part, non-empty


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_existing_account_is_reused_by_email_not_duplicated():
    existing = User.objects.create_user(
        username="already_here", password="RealPass123!", email="known@gmail.com"
    )
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {
            "email": "KNOWN@gmail.com",  # case-insensitive match
            "email_verified": True,
        }
        response = _post_google()

    assert response.status_code == 200
    assert response.json()["created"] is False
    assert User.objects.filter(email__iexact="known@gmail.com").count() == 1
    # The pre-existing password-based account is untouched.
    existing.refresh_from_db()
    assert existing.has_usable_password() is True


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_username_collision_is_disambiguated():
    User.objects.create_user(username="sam", password="pw-not-relevant", email="sam@other.com")
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "sam@gmail.com", "email_verified": True}
        response = _post_google()

    assert response.status_code == 200
    new_user = User.objects.get(email="sam@gmail.com")
    assert new_user.username != "sam"
    assert new_user.username.startswith("sam")


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_invalid_token_is_rejected():
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.side_effect = ValueError("Token used too late")
        response = _post_google()

    assert response.status_code == 401
    assert User.objects.count() == 0


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_unverified_email_is_rejected():
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.return_value = {"email": "unverified@gmail.com", "email_verified": False}
        response = _post_google()

    assert response.status_code == 401
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_missing_credential_is_rejected():
    response = APIClient().post(reverse("auth-google"), {}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID=None)
def test_unconfigured_server_fails_loudly_not_silently():
    # Misconfiguration (no audience to check tokens against) must never
    # look like a successful login.
    response = _post_google()
    assert response.status_code == 503
    assert User.objects.count() == 0


@pytest.mark.django_db
@override_settings(GOOGLE_CLIENT_ID="test-client-id")
def test_google_login_is_rate_limited_same_scope_as_password_login():
    from django.conf import settings
    allowed = int(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"].split("/")[0]
    )
    with patch("common.google_auth_views.google_id_token.verify_oauth2_token") as mock_verify:
        mock_verify.side_effect = ValueError("bad token")
        codes = [_post_google().status_code for _ in range(allowed + 2)]

    # Assert the BOUNDARY, not just that a 429 shows up eventually: SSO must
    # consume the same bucket as password login, or it becomes a way around
    # the credential-stuffing brake.
    assert 429 not in codes[:allowed], (
        f"the first {allowed} attempts must pass the throttle, got {codes}"
    )
    assert codes[allowed] == 429, f"attempt {allowed + 1} must be throttled, got {codes}"
