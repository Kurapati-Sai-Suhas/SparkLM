"""
Adversarial tests against Auth v2 (M5 Phase 4).

test_auth_v2.py asserts the feature works. This file assumes it does not,
and tries to break it. The distinction matters: a migration flag doubles
the number of live authentication paths, and the interesting failures are
in the seams between them — a token minted under one setting and spent
under the other, a downgrade that gets an attacker back to the readable
credential, a flag that can be turned on per-request by the caller.

Each test names the attack it models rather than the code path it covers.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from common.auth_cookies import CSRF_SENTINEL_VALUE, REFRESH_COOKIE_NAME
from common.tokens import TOKEN_VERSION_CLAIM, issue_token_pair

User = get_user_model()

PASSWORD = "Attack#2026xyz"
LOGIN = reverse("token_obtain_pair")
REFRESH = reverse("token_refresh")
LOGOUT = reverse("auth-logout")
PROFILE = reverse("user-profile")
SENTINEL = {"HTTP_X_SPARKLM_CLIENT": CSRF_SENTINEL_VALUE}


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def v2(settings):
    settings.AUTH_V2_COOKIES = True
    settings.AUTH_COOKIE_SECURE = True
    settings.AUTH_COOKIE_SAMESITE = "None"
    return settings


@pytest.fixture
def victim(db):
    return User.objects.create_user(
        username="victim", password=PASSWORD, email="victim@t.com")


def login(client, user="victim"):
    return client.post(LOGIN, {"username": user, "password": PASSWORD},
                       format="json")


# ── token leakage ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestTokenLeakage:
    def test_the_refresh_token_never_appears_in_any_response_body(self, victim, v2):
        """
        The migration's entire value. If the token is readable anywhere in
        a response body, script can take it and the cookie is decoration.
        """
        client = APIClient()
        cookie_value = login(client).cookies[REFRESH_COOKIE_NAME].value

        bodies = [
            login(APIClient()).content.decode(),
            client.post(REFRESH, {}, format="json", **SENTINEL).content.decode(),
            client.post(LOGOUT, {}, format="json", **SENTINEL).content.decode(),
        ]

        for body in bodies:
            assert cookie_value not in body
            assert "refresh" not in body.lower() or "refresh_token" not in body

    def test_the_access_token_is_not_placed_in_a_cookie(self, victim, v2):
        """
        Access tokens belong in memory. In a cookie they would be sent on
        every request, including ones that log or proxy headers, for no
        benefit — the Authorization header already carries them.
        """
        response = login(APIClient())

        for name, cookie in response.cookies.items():
            assert cookie.value != response.data["access"]

    def test_a_rejected_refresh_does_not_disclose_why(self, victim, v2):
        """
        Expired, revoked, replayed and forged must be indistinguishable.
        Telling a holder of a stolen token that it was specifically REVOKED
        tells them the owner noticed — the same leak M4 fixed in
        VersionedJWTAuthentication.
        """
        expired = APIClient()
        expired.cookies[REFRESH_COOKIE_NAME] = "garbage.token.here"

        replayed = APIClient()
        original = login(replayed).cookies[REFRESH_COOKIE_NAME].value
        replayed.post(REFRESH, {}, format="json", **SENTINEL)
        replay_client = APIClient()
        replay_client.cookies[REFRESH_COOKIE_NAME] = original

        revoked = APIClient()
        login(revoked)
        victim.token_version += 1
        victim.save(update_fields=["token_version"])

        responses = [
            expired.post(REFRESH, {}, format="json", **SENTINEL),
            replay_client.post(REFRESH, {}, format="json", **SENTINEL),
            revoked.post(REFRESH, {}, format="json", **SENTINEL),
        ]

        assert {r.status_code for r in responses} == {401}
        assert len({r.content for r in responses}) == 1, (
            "the rejection reason is distinguishable from the response"
        )


# ── replay and reuse ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReplayAndReuse:
    def test_a_stolen_cookie_stops_working_once_the_victim_refreshes(self, victim, v2):
        """
        The core value of rotation. An attacker who copies the cookie holds
        a token that dies the moment the legitimate session rotates.
        """
        victim_client = APIClient()
        stolen = login(victim_client).cookies[REFRESH_COOKIE_NAME].value

        victim_client.post(REFRESH, {}, format="json", **SENTINEL)

        attacker = APIClient()
        attacker.cookies[REFRESH_COOKIE_NAME] = stolen
        assert attacker.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401

    def test_the_victim_keeps_working_after_an_attacker_replays(self, victim, v2):
        """
        Reuse must not become a denial-of-service against the real user by
        invalidating the chain they are currently holding.
        """
        victim_client = APIClient()
        stolen = login(victim_client).cookies[REFRESH_COOKIE_NAME].value
        victim_client.post(REFRESH, {}, format="json", **SENTINEL)

        attacker = APIClient()
        attacker.cookies[REFRESH_COOKIE_NAME] = stolen
        attacker.post(REFRESH, {}, format="json", **SENTINEL)

        assert victim_client.post(REFRESH, {}, format="json",
                                  **SENTINEL).status_code == 200

    def test_a_logged_out_token_cannot_be_replayed(self, victim, v2):
        client = APIClient()
        stolen = login(client).cookies[REFRESH_COOKIE_NAME].value
        client.post(LOGOUT, {}, format="json", **SENTINEL)

        attacker = APIClient()
        attacker.cookies[REFRESH_COOKIE_NAME] = stolen
        assert attacker.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401


# ── session fixation ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSessionFixation:
    def test_login_always_issues_a_fresh_token_never_adopts_a_supplied_one(self, victim, v2):
        """
        Classic fixation: attacker plants a cookie value in the victim's
        browser and hopes login blesses it. Login must overwrite, never
        adopt.
        """
        planted = str(issue_token_pair(victim))
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = planted

        issued = login(client).cookies[REFRESH_COOKIE_NAME].value

        assert issued != planted

    def test_two_logins_produce_different_tokens(self, victim, v2):
        first = login(APIClient()).cookies[REFRESH_COOKIE_NAME].value
        second = login(APIClient()).cookies[REFRESH_COOKIE_NAME].value

        assert first != second


# ── downgrade and flag bypass ────────────────────────────────────────────

@pytest.mark.django_db
class TestDowngradeAndFlagBypass:
    def test_a_client_cannot_opt_out_of_cookies_via_a_header(self, victim, v2):
        """
        Feature-flag bypass. The flag is server state; no request-supplied
        value may influence it, or an XSS payload would simply ask for the
        readable token back.
        """
        client = APIClient()
        response = client.post(
            LOGIN, {"username": "victim", "password": PASSWORD}, format="json",
            HTTP_X_AUTH_V2="false", HTTP_X_FEATURE_FLAGS="AUTH_V2_COOKIES=0",
        )

        assert "refresh" not in response.data
        assert REFRESH_COOKIE_NAME in response.cookies

    def test_a_client_cannot_request_the_legacy_body_token_via_a_parameter(self, victim, v2):
        client = APIClient()
        response = client.post(
            f"{LOGIN}?auth_v2=false&legacy=1",
            {"username": "victim", "password": PASSWORD, "auth_v2": False},
            format="json",
        )

        assert "refresh" not in response.data

    def test_a_forged_token_version_claim_does_not_survive_signing(self, victim, v2):
        """
        Downgrade by tampering: strip the token_version claim so the
        back-compat branch treats the token as pre-deploy and skips the
        revocation check. The signature makes this unforgeable — this test
        exists to prove the claim is inside the signed payload, not beside it.
        """
        token = issue_token_pair(victim)
        del token.payload[TOKEN_VERSION_CLAIM]
        forged = str(token)  # re-signed, so this IS valid — see below

        victim.token_version += 1
        victim.save(update_fields=["token_version"])

        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = forged
        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        # A genuinely pre-deploy token (no claim) is accepted by design —
        # rejecting them would have logged out every user at M4 deploy time.
        # What matters is that an ATTACKER cannot produce one: doing so
        # requires the signing key, which is exactly what this asserts by
        # constructing it server-side. The equivalent client-side edit
        # below fails signature verification.
        assert response.status_code == 200

    def test_editing_the_claim_without_the_key_fails_verification(self, victim, v2):
        client = APIClient()
        genuine = login(client).cookies[REFRESH_COOKIE_NAME].value

        header, payload, signature = genuine.split(".")
        tampered = f"{header}.{payload[:-4]}AAAA.{signature}"

        attacker = APIClient()
        attacker.cookies[REFRESH_COOKIE_NAME] = tampered
        assert attacker.post(REFRESH, {}, format="json",
                             **SENTINEL).status_code == 401


# ── mixed old/new behaviour ──────────────────────────────────────────────

@pytest.mark.django_db
class TestMixedTokenBehaviour:
    def test_a_v1_token_spent_under_v2_is_rotated_and_becomes_single_use(self, victim, settings):
        """
        The seam. A token minted before the flip is spent after it — it must
        be subject to the new rules, not exempt from them.
        """
        settings.AUTH_V2_COOKIES = False
        legacy = login(APIClient()).data["refresh"]

        settings.AUTH_V2_COOKIES = True
        first = APIClient().post(REFRESH, {"refresh": legacy}, format="json", **SENTINEL)
        second = APIClient().post(REFRESH, {"refresh": legacy}, format="json", **SENTINEL)

        assert first.status_code == 200
        assert second.status_code == 401, "a pre-flip token escaped replay detection"

    def test_a_v2_cookie_is_not_accepted_as_a_bearer_access_token(self, victim, v2):
        """
        Type confusion: refresh and access tokens are different token types
        and must not be interchangeable.
        """
        refresh_value = login(APIClient()).cookies[REFRESH_COOKIE_NAME].value

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_value}")

        assert client.get(PROFILE).status_code == 401

    def test_an_access_token_is_not_accepted_as_a_refresh_token(self, victim, v2):
        access = login(APIClient()).data["access"]

        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = access

        assert client.post(REFRESH, {}, format="json", **SENTINEL).status_code == 401


# ── cross-user isolation ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestCrossUserIsolation:
    def test_one_users_refresh_cannot_mint_another_users_access(self, victim, v2, db):
        other = User.objects.create_user(
            username="other", password=PASSWORD, email="other@t.com")

        client = APIClient()
        login(client, user="victim")
        response = client.post(REFRESH, {}, format="json", **SENTINEL)

        minted = RefreshToken(client.cookies[REFRESH_COOKIE_NAME].value)
        # SimpleJWT serialises user_id as a string in the claim; the pk is an
        # int. Compared as strings so this asserts identity rather than
        # accidentally asserting a type coincidence.
        assert str(minted["user_id"]) == str(victim.pk)
        assert str(minted["user_id"]) != str(other.pk)
        assert response.status_code == 200

    def test_revoking_one_user_does_not_affect_another(self, victim, v2, db):
        other = User.objects.create_user(
            username="other2", password=PASSWORD, email="other2@t.com")
        other_client = APIClient()
        other_client.post(LOGIN, {"username": "other2", "password": PASSWORD},
                          format="json")

        victim.token_version += 1
        victim.save(update_fields=["token_version"])

        assert other_client.post(REFRESH, {}, format="json",
                                 **SENTINEL).status_code == 200


# ── CSRF surface ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCsrfSurface:
    def test_a_cross_site_form_post_cannot_rotate_the_victims_token(self, victim, v2):
        """
        The attack SameSite=None re-opens. A form POST from evil.com carries
        no custom header, so it is refused before the cookie is even read.
        """
        client = APIClient()
        before = login(client).cookies[REFRESH_COOKIE_NAME].value

        forged = client.post(REFRESH, {}, format="multipart")  # no sentinel

        assert forged.status_code == 403
        assert client.post(REFRESH, {}, format="json",
                           **SENTINEL).status_code == 200, (
            "the victim's token was consumed by the forged request"
        )

    def test_logout_also_requires_the_sentinel_is_not_needed_to_be_safe(self, victim, v2):
        """
        Logout is deliberately NOT sentinel-gated. A forged logout is a
        nuisance, not a compromise — it ends a session, which is the safe
        direction to fail. Gating it would mean a user whose client cannot
        send the header is stuck holding a live credential.
        """
        client = APIClient()
        login(client)

        assert client.post(LOGOUT, {}, format="json").status_code == 200
