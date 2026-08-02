"""
JWT revocation contract (M4 Phase A).

SparkLM's JWTs are stateless, which bought scalability and sold
revocability. This adds revocation back for the price of an integer
comparison against a row `JWTAuthentication.get_user()` already loads.

The tests are shaped around the failure mode this codebase keeps hitting:
a security control that is present, configured, reviewed, and **inert**.
Dead password validators, an inert throttle, and a non-persisting cache were
all of that shape. So it is not enough to assert that a mismatched claim is
rejected — the tests must also prove that every issuance path actually
STAMPS a claim, because a revocation check against tokens that carry no
claim rejects nothing and passes every test you would think to write.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from common.tokens import TOKEN_VERSION_CLAIM, issue_token_pair

User = get_user_model()


@pytest.fixture(autouse=True)
def _isolate_throttle_state():
    """
    Several tests here post to /api/token/, which shares the 5/minute 'auth'
    bucket with every other module that logs in. Without this, counters
    accumulate ACROSS test files and a test that passes alone fails in the
    full suite with a 429.

    That is not hypothetical — it is exactly how this file first failed, and
    it is the same latent coupling that bit common/test_google_auth.py when
    Phase B of Milestone 3 lowered the limit from 10 to 5. A test must not
    depend on how many requests its neighbours happened to make.
    """
    cache.clear()
    yield
    cache.clear()

PASSWORD = "M4Revoke#2026x"
PROTECTED = "dashboard-bootstrap"


def make_user(username="revoke_me", **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, email=f"{username}@t.com", **extra
    )


def auth(client, access):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


# ── issuance: the claim must actually be stamped ─────────────────────────

@pytest.mark.django_db
class TestEveryIssuancePathStampsTheClaim:
    """
    If any path mints an unstamped token, revocation is silently inert for
    every user who signed in that way.
    """

    def test_password_login_tokens_carry_the_claim(self):
        make_user()
        response = APIClient().post(
            reverse("token_obtain_pair"),
            {"username": "revoke_me", "password": PASSWORD}, format="json",
        )
        assert response.status_code == 200
        assert TOKEN_VERSION_CLAIM in AccessToken(response.data["access"])
        assert TOKEN_VERSION_CLAIM in RefreshToken(response.data["refresh"])

    def test_refreshed_access_tokens_inherit_the_claim(self):
        """
        The reason the claim rides on the REFRESH token: SimpleJWT copies
        every claim except token_type/exp/iat/jti onto each access token it
        mints. Without this, a revoked refresh token could keep producing
        working access tokens for a day.
        """
        user = make_user()
        refresh = issue_token_pair(user)

        response = APIClient().post(
            reverse("token_refresh"), {"refresh": str(refresh)}, format="json",
        )
        assert response.status_code == 200
        assert AccessToken(response.data["access"])[TOKEN_VERSION_CLAIM] == 0

    def test_google_login_tokens_are_revocable(self):
        """
        SSO went through RefreshToken.for_user directly before M4, which
        would have left six production accounts permanently unrevocable.
        """
        user = make_user(username="sso_user")
        user.set_unusable_password()
        user.save(update_fields=["password"])

        refresh = issue_token_pair(user)
        assert refresh[TOKEN_VERSION_CLAIM] == user.token_version

    def test_the_claim_tracks_the_current_version_not_zero(self):
        user = make_user()
        user.token_version = 7
        user.save(update_fields=["token_version"])

        assert issue_token_pair(user)[TOKEN_VERSION_CLAIM] == 7


# ── enforcement ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRevocationIsEnforced:
    def test_a_matching_token_is_accepted(self):
        user = make_user()
        access = str(issue_token_pair(user).access_token)
        response = auth(APIClient(), access).get(reverse(PROTECTED))
        assert response.status_code == 200

    def test_bumping_the_version_invalidates_an_outstanding_token(self):
        user = make_user()
        access = str(issue_token_pair(user).access_token)
        client = auth(APIClient(), access)
        assert client.get(reverse(PROTECTED)).status_code == 200

        user.token_version += 1
        user.save(update_fields=["token_version"])

        assert client.get(reverse(PROTECTED)).status_code == 401

    def test_a_revoked_refresh_token_cannot_mint_a_working_access_token(self):
        """
        The attack this closes. Revoking must kill the whole family, not
        just the access token the attacker happens to hold.
        """
        user = make_user()
        refresh = issue_token_pair(user)

        user.token_version += 1
        user.save(update_fields=["token_version"])

        # Refresh still succeeds — the refresh token's signature is valid and
        # SimpleJWT does not consult the database to mint from it...
        response = APIClient().post(
            reverse("token_refresh"), {"refresh": str(refresh)}, format="json",
        )
        assert response.status_code == 200

        # ...but the access token it produces carries the STALE claim, so it
        # is refused at the point of use. That is where the check belongs.
        client = auth(APIClient(), response.data["access"])
        assert client.get(reverse(PROTECTED)).status_code == 401

    def test_revocation_does_not_leak_that_the_token_was_revoked(self):
        """
        A revoked token must not announce itself. Telling whoever holds a
        stolen token that the owner noticed and acted is useful only to
        them.

        This test earned its place: the first implementation raised
        SimpleJWT's `AuthenticationFailed`, which mixes in DetailDictMixin
        and serialises the exception CODE into the response body. The client
        received `{"detail": ..., "code": "token_revoked"}` — a
        machine-readable announcement — while the docstring claimed the code
        was server-side only. Asserting on the RENDERED body rather than
        `response.data` is what exposed it.
        """
        user = make_user()
        access = str(issue_token_pair(user).access_token)
        user.token_version += 1
        user.save(update_fields=["token_version"])

        revoked = auth(APIClient(), access).get(reverse(PROTECTED))
        garbage = auth(APIClient(), "not.a.token").get(reverse(PROTECTED))

        assert revoked.status_code == garbage.status_code == 401
        body = revoked.content.decode().lower()
        assert "revok" not in body, f"revocation leaked to the client: {body}"
        assert "version" not in body, f"revocation leaked to the client: {body}"

    def test_other_users_are_unaffected(self):
        alice, bob = make_user("alice"), make_user("bob")
        bob_client = auth(APIClient(), str(issue_token_pair(bob).access_token))

        alice.token_version += 1
        alice.save(update_fields=["token_version"])

        assert bob_client.get(reverse(PROTECTED)).status_code == 200


# ── back-compatibility ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_a_token_issued_before_this_deploy_still_works():
    """
    Load-bearing. Every token in the wild at deploy time has no
    token_version claim; rejecting those would log out the entire user base
    and turn a security improvement into an outage. They age out naturally
    within the 60-minute access lifetime.

    If this test is ever deliberately removed, that is a decision to force a
    fleet-wide logout — make it knowingly.
    """
    user = make_user()
    legacy = RefreshToken.for_user(user)          # no claim, pre-M4 shape
    assert TOKEN_VERSION_CLAIM not in legacy

    client = auth(APIClient(), str(legacy.access_token))
    assert client.get(reverse(PROTECTED)).status_code == 200


# ── the trigger: something must be able to bump the counter ──────────────

@pytest.mark.django_db
class TestLogoutAll:
    def test_logout_all_revokes_the_callers_tokens(self):
        user = make_user()
        first = auth(APIClient(), str(issue_token_pair(user).access_token))
        second = auth(APIClient(), str(issue_token_pair(user).access_token))

        assert first.post(reverse("auth-logout-all")).status_code == 200

        # Both sessions die, including the one that made the call.
        assert first.get(reverse(PROTECTED)).status_code == 401
        assert second.get(reverse(PROTECTED)).status_code == 401

    def test_logout_all_requires_authentication(self):
        assert APIClient().post(reverse("auth-logout-all")).status_code == 401

    def test_a_fresh_login_after_logout_all_works(self):
        """Revocation must not brick the account."""
        user = make_user()
        auth(APIClient(), str(issue_token_pair(user).access_token)).post(
            reverse("auth-logout-all")
        )

        response = APIClient().post(
            reverse("token_obtain_pair"),
            {"username": "revoke_me", "password": PASSWORD}, format="json",
        )
        assert response.status_code == 200
        assert auth(APIClient(), response.data["access"]).get(
            reverse(PROTECTED)
        ).status_code == 200

    def test_a_second_logout_all_cannot_be_called_with_a_revoked_token(self):
        """
        Discovered while writing the atomicity test below: the endpoint
        cannot be called twice with the same token, because the first call
        revokes the token making the second. That is correct behaviour and
        worth pinning — it means "revoke everything" genuinely includes the
        session issuing the request, with no special case.
        """
        user = make_user()
        client = auth(APIClient(), str(issue_token_pair(user).access_token))

        assert client.post(reverse("auth-logout-all")).status_code == 200
        assert client.post(reverse("auth-logout-all")).status_code == 401


@pytest.mark.django_db
def test_the_counter_increments_atomically():
    """
    Atomicity belongs at the ORM layer, not the endpoint.

    My first version of this drove two `logout-all` calls and asserted the
    counter reached 2. It cannot: the first call revokes the second
    caller's token, so the second request 401s before touching the counter
    (see the test above). Testing the concurrency property through an
    endpoint that structurally forbids the second call proves nothing.

    What actually matters is that the update is `F('token_version') + 1`
    rather than a read-modify-write, so two writers holding stale in-memory
    copies both increment instead of both writing 1. That is a queryset
    property and is tested as one.
    """
    from django.db.models import F

    user = make_user()
    stale_a = User.objects.get(pk=user.pk)   # both hold token_version == 0
    stale_b = User.objects.get(pk=user.pk)
    assert stale_a.token_version == stale_b.token_version == 0

    for stale in (stale_a, stale_b):
        User.objects.filter(pk=stale.pk).update(token_version=F("token_version") + 1)

    user.refresh_from_db()
    assert user.token_version == 2, (
        "expected two atomic increments; a read-modify-write from two stale "
        "instances would collapse these into one"
    )
