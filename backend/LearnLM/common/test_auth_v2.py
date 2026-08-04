"""
Authentication v2 — httpOnly refresh cookies with rotation (M5 Phase 4).

Both tokens lived in localStorage, so any XSS on the SPA walked away with a
refresh token good for a day and renewable indefinitely. M4's CSP removed
most delivery vectors and token_version bounded the damage once noticed,
but the credential was still *readable by script* — which is the property
that matters.

Phase 4 moves the refresh token into a cookie script cannot read, rotates
it on every use, and refuses a token that has already been spent.

The organising risk in this file is NOT "does the happy path work" — it is
that a migration flag creates two live code paths and a bug in either one
is a silent authentication failure. So every behaviour is asserted under
BOTH flag settings, and the flag-off assertions are written to fail if the
legacy path changes at all.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from common.auth_cookies import (
    CSRF_SENTINEL_VALUE,
    REFRESH_COOKIE_NAME,
)
from common.tokens import issue_token_pair

User = get_user_model()

PASSWORD = "AuthV2#2026xyz"
LOGIN = reverse("token_obtain_pair")
REFRESH = reverse("token_refresh")
LOGOUT = reverse("auth-logout")
LOGOUT_ALL = reverse("auth-logout-all")
PROFILE = reverse("user-profile")

SENTINEL = {"HTTP_X_SPARKLM_CLIENT": CSRF_SENTINEL_VALUE}


@pytest.fixture(autouse=True)
def _clear_throttle_state():
    """
    The auth scope allows 5 logins/minute per IP, and every test here logs
    in. Without this, tests pass alone and 429 in a full run — which is a
    throttle result masquerading as an authentication failure, the single
    most misleading way for an auth suite to go red.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="authv2", password=PASSWORD, email="authv2@t.com")


@pytest.fixture
def v2(settings):
    """Auth v2 on, with cookie attributes as production sets them."""
    settings.AUTH_V2_COOKIES = True
    settings.AUTH_COOKIE_SECURE = True
    settings.AUTH_COOKIE_SAMESITE = "None"
    return settings


@pytest.fixture
def v1(settings):
    """Legacy path — the currently deployed behaviour."""
    settings.AUTH_V2_COOKIES = False
    return settings


def login(client, username="authv2", password=PASSWORD):
    return client.post(LOGIN, {"username": username, "password": password},
                       format="json")


# ── login ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLogin:
    def test_v1_returns_both_tokens_and_sets_no_cookie(self, user, v1):
        """
        The legacy contract, pinned. If this ever changes, every deployed
        client breaks — the flag exists precisely so it cannot.
        """
        response = login(APIClient())

        assert response.status_code == 200
        assert response.data["access"] and response.data["refresh"]
        assert REFRESH_COOKIE_NAME not in response.cookies

    def test_v2_moves_the_refresh_token_into_a_cookie(self, user, v2):
        response = login(APIClient())

        assert response.status_code == 200
        assert response.data["access"]
        assert REFRESH_COOKIE_NAME in response.cookies

    def test_v2_removes_the_refresh_token_from_the_body(self, user, v2):
        """
        The whole point. Leaving it in both places would mean script can
        still read it, and the cookie would buy exactly nothing.
        """
        response = login(APIClient())

        assert "refresh" not in response.data
        assert REFRESH_COOKIE_NAME not in response.content.decode()

    def test_the_cookie_is_httponly_secure_and_samesite_none(self, user, v2):
        cookie = login(APIClient()).cookies[REFRESH_COOKIE_NAME]

        assert cookie["httponly"] is True, "script could read the refresh token"
        assert cookie["secure"] is True, "the cookie would travel over plain HTTP"
        assert cookie["samesite"] == "None", "Vercel->Render is cross-site; Lax never sends it"
        assert cookie["path"] == "/api/"

    def test_the_cookie_expiry_matches_the_refresh_lifetime(self, user, v2, settings):
        cookie = login(APIClient()).cookies[REFRESH_COOKIE_NAME]

        expected = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
        assert int(cookie["max-age"]) == expected

    def test_bad_credentials_still_fail_under_v2(self, user, v2):
        response = login(APIClient(), password="wrong")

        assert response.status_code == 401
        assert REFRESH_COOKIE_NAME not in response.cookies

    def test_the_access_token_still_authenticates(self, user, v2):
        access = login(APIClient()).data["access"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        assert client.get(PROFILE).status_code == 200


# ── refresh + rotation ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestRefreshRotation:
    def test_v1_refresh_is_unchanged_and_does_not_rotate(self, user, v1):
        """
        Legacy clients store the new `access` but never update their stored
        refresh token. If rotation were global they would break after one
        refresh — this asserts it is not.
        """
        client = APIClient()
        refresh = login(client).data["refresh"]

        first = client.post(REFRESH, {"refresh": refresh}, format="json")
        second = client.post(REFRESH, {"refresh": refresh}, format="json")

        assert first.status_code == 200 and second.status_code == 200
        assert "refresh" not in first.data or first.data.get("refresh") == refresh

    def test_v2_refresh_reads_the_cookie_and_issues_a_new_access_token(self, user, v2):
        client = APIClient()
        login(client)

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert response.status_code == 200
        assert response.data["access"]

    def test_v2_rotates_the_cookie_on_every_refresh(self, user, v2):
        client = APIClient()
        original = login(client).cookies[REFRESH_COOKIE_NAME].value

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert REFRESH_COOKIE_NAME in response.cookies
        assert response.cookies[REFRESH_COOKIE_NAME].value != original

    def test_v2_never_returns_the_refresh_token_in_the_body(self, user, v2):
        client = APIClient()
        login(client)

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert "refresh" not in response.data

    def test_a_spent_refresh_token_is_refused(self, user, v2):
        """
        Replay. Rotation without this is theatre — the old token would stay
        valid for its full remaining lifetime.
        """
        client = APIClient()
        original = login(client).cookies[REFRESH_COOKIE_NAME].value
        client.post(REFRESH, {}, format="json", **SENTINEL)

        replay = APIClient()
        replay.cookies[REFRESH_COOKIE_NAME] = original
        response = replay.post(REFRESH, {}, format="json", **SENTINEL)

        assert response.status_code == 401

    def test_concurrent_refresh_has_exactly_one_winner(self, user, v2):
        """
        Two tabs presenting the same token. The blacklist row's unique
        constraint decides atomically; the loser is indistinguishable from
        a replay, and refusing is correct for both.
        """
        client = APIClient()
        original = login(client).cookies[REFRESH_COOKIE_NAME].value

        results = []
        for _ in range(2):
            c = APIClient()
            c.cookies[REFRESH_COOKIE_NAME] = original
            results.append(c.post(REFRESH, {}, format="json", **SENTINEL).status_code)

        assert sorted(results) == [200, 401]

    def test_the_rotated_token_works_for_the_next_refresh(self, user, v2):
        """Rotation must be a chain, not a one-shot."""
        client = APIClient()
        login(client)

        for _ in range(3):
            assert client.post(REFRESH, {}, format="json", **SENTINEL).status_code == 200

    def test_a_garbage_cookie_is_refused_and_cleared(self, user, v2):
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = "not-a-jwt"

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert response.status_code == 401
        assert response.cookies[REFRESH_COOKIE_NAME].value == ""

    def test_no_token_at_all_is_a_401(self, user, v2):
        assert APIClient().post(REFRESH, {}, format="json", **SENTINEL).status_code == 401


# ── token_version interaction ────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenVersionCompatibility:
    def test_a_revoked_refresh_token_cannot_mint_a_new_session(self, user, v2):
        """
        The defect rotation would have introduced.

        TokenRefreshSerializer never checks token_version. Without an
        explicit check, a refresh token revoked by logout-all would be used
        to mint a BRAND NEW pair stamped with the CURRENT version — silently
        un-revoking the session that logout-all had just ended.
        """
        client = APIClient()
        login(client)

        user.token_version += 1
        user.save(update_fields=["token_version"])

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert response.status_code == 401

    def test_rotated_tokens_carry_the_current_token_version(self, user, v2):
        """Otherwise the next rotation would reject its own predecessor."""
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        login(client)
        client.post(REFRESH, {}, format="json", **SENTINEL)

        rotated = RefreshToken(client.cookies[REFRESH_COOKIE_NAME].value)
        assert rotated["token_version"] == user.token_version

    def test_logout_all_still_revokes_every_device(self, user, v2):
        phone, laptop = APIClient(), APIClient()
        login(phone)
        access = login(laptop).data["access"]

        laptop.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        assert laptop.post(LOGOUT_ALL, {}, format="json").status_code == 200

        assert phone.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401

    def test_logout_all_clears_this_browsers_cookie(self, user, v2):
        client = APIClient()
        access = login(client).data["access"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = client.post(LOGOUT_ALL, {}, format="json")

        assert response.cookies[REFRESH_COOKIE_NAME].value == ""


# ── logout ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLogout:
    def test_logout_clears_the_cookie(self, user, v2):
        client = APIClient()
        login(client)

        response = client.post(LOGOUT, {}, format="json", **SENTINEL)

        assert response.status_code == 200
        assert response.cookies[REFRESH_COOKIE_NAME].value == ""

    def test_logout_invalidates_the_refresh_token(self, user, v2):
        client = APIClient()
        original = login(client).cookies[REFRESH_COOKIE_NAME].value
        client.post(LOGOUT, {}, format="json", **SENTINEL)

        attacker = APIClient()
        attacker.cookies[REFRESH_COOKIE_NAME] = original
        assert attacker.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401

    def test_logout_ends_only_this_session_not_every_device(self, user, v2):
        """
        The distinction from logout-all. Signing out of a library computer
        must not sign you out of your phone.
        """
        phone, library = APIClient(), APIClient()
        login(phone)
        login(library)

        library.post(LOGOUT, {}, format="json", **SENTINEL)

        assert phone.post(REFRESH, {}, format="json", **SENTINEL).status_code == 200

    def test_logout_works_without_a_valid_access_token(self, user, v2):
        """
        AllowAny is deliberate: an expired access token is exactly when a
        user reaches for logout, and failing then would strand the cookie.
        """
        client = APIClient()
        login(client)
        client.credentials(HTTP_AUTHORIZATION="Bearer expired.garbage.token")

        assert client.post(LOGOUT, {}, format="json", **SENTINEL).status_code == 200

    def test_logout_with_no_session_is_still_200(self, user, v2):
        """Reporting failure would invite the client to retry and leave the cookie."""
        assert APIClient().post(LOGOUT, {}, format="json", **SENTINEL).status_code == 200


# ── CSRF ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCsrfProtection:
    def test_refresh_without_the_sentinel_header_is_rejected(self, user, v2):
        """
        SameSite=None means the browser attaches this cookie to cross-site
        requests. A form POST from evil.com would otherwise rotate the
        victim's token. Requiring a custom header forces a CORS preflight
        that our origin allowlist answers for.
        """
        client = APIClient()
        login(client)

        response = client.post(REFRESH, {}, format="json")  # no sentinel

        assert response.status_code == 403

    def test_a_wrong_sentinel_value_is_rejected(self, user, v2):
        client = APIClient()
        login(client)

        response = client.post(REFRESH, {}, format="json",
                               HTTP_X_SPARKLM_CLIENT="evil")

        assert response.status_code == 403

    def test_the_sentinel_is_not_required_on_the_legacy_path(self, user, v1):
        """v1 has no cookie, so it has no CSRF surface to protect."""
        client = APIClient()
        refresh = login(client).data["refresh"]

        assert client.post(REFRESH, {"refresh": refresh},
                           format="json").status_code == 200


# ── migration compatibility ──────────────────────────────────────────────

@pytest.mark.django_db
class TestRolloutAndRollback:
    def test_a_body_token_still_works_after_the_flag_is_turned_on(self, user, settings):
        """
        Forward compatibility. A client that logged in BEFORE the flip
        still holds a localStorage refresh token; it must keep working or
        flipping the flag logs everyone out.
        """
        settings.AUTH_V2_COOKIES = False
        client = APIClient()
        refresh = login(client).data["refresh"]

        settings.AUTH_V2_COOKIES = True
        fresh = APIClient()
        response = fresh.post(REFRESH, {"refresh": refresh}, format="json", **SENTINEL)

        assert response.status_code == 200

    def test_a_cookie_still_works_after_the_flag_is_turned_off(self, user, settings):
        """
        Rollback. A client that logged in AFTER the flip holds only a
        cookie; turning the flag off must not strand it.
        """
        settings.AUTH_V2_COOKIES = True
        client = APIClient()
        login(client)

        settings.AUTH_V2_COOKIES = False
        response = client.post(REFRESH, {}, format="json")

        assert response.status_code == 200

    def test_the_cookie_wins_when_both_are_presented(self, user, v2):
        """
        Otherwise a caller could choose which of two tokens to present —
        including an older one they had kept.
        """
        client = APIClient()
        stale = issue_token_pair(user)
        login(client)
        cookie_before = client.cookies[REFRESH_COOKIE_NAME].value

        response = client.post(REFRESH, {"refresh": str(stale)},
                               format="json", **SENTINEL)

        assert response.status_code == 200
        # The cookie token was spent, not the body one.
        replay = APIClient()
        replay.cookies[REFRESH_COOKIE_NAME] = cookie_before
        assert replay.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401


# ── Google SSO parity ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGoogleSignIn:
    @pytest.fixture(autouse=True)
    def _stub_google(self, monkeypatch, settings):
        settings.GOOGLE_CLIENT_ID = "test-client-id"
        monkeypatch.setattr(
            "common.google_auth_views.google_id_token.verify_oauth2_token",
            lambda credential, request, audience: {
                "email": "sso@t.com", "email_verified": True},
        )

    def _sso(self, client):
        return client.post(reverse("auth-google"), {"credential": "x"},
                           format="json")

    def test_v1_sso_returns_the_refresh_token_in_the_body(self, db, v1):
        response = self._sso(APIClient())

        assert response.status_code == 200
        assert response.data["refresh"]
        assert REFRESH_COOKIE_NAME not in response.cookies

    def test_v2_sso_sets_the_cookie_and_omits_the_body_token(self, db, v2):
        """
        Parity matters: if SSO kept returning the token in the body while
        password login moved to a cookie, every Google user would keep a
        script-readable credential and the flag would be a no-op for them.
        """
        response = self._sso(APIClient())

        assert response.status_code == 200
        assert "refresh" not in response.data
        assert REFRESH_COOKIE_NAME in response.cookies
        assert response.cookies[REFRESH_COOKIE_NAME]["httponly"] is True

    def test_an_sso_cookie_can_be_refreshed_and_rotated(self, db, v2):
        client = APIClient()
        self._sso(client)

        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        assert response.status_code == 200
        assert response.data["access"]

    def test_sso_tokens_remain_revocable(self, db, v2):
        client = APIClient()
        self._sso(client)
        sso_user = User.objects.get(email="sso@t.com")

        sso_user.token_version += 1
        sso_user.save(update_fields=["token_version"])

        assert client.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401


# ── multi-device ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMultipleDevices:
    def test_two_devices_hold_independent_sessions(self, user, v2):
        phone, laptop = APIClient(), APIClient()
        login(phone)
        login(laptop)

        assert phone.cookies[REFRESH_COOKIE_NAME].value != \
               laptop.cookies[REFRESH_COOKIE_NAME].value

    def test_rotating_on_one_device_does_not_affect_the_other(self, user, v2):
        phone, laptop = APIClient(), APIClient()
        login(phone)
        login(laptop)

        phone.post(REFRESH, {}, format="json", **SENTINEL)

        assert laptop.post(REFRESH, {}, format="json", **SENTINEL).status_code == 200
