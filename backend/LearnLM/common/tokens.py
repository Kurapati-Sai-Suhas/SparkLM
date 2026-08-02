"""
common.tokens — JWT issuance with a revocation claim (M4 Phase A).

SparkLM's JWTs are stateless by design, which bought horizontal scalability
and sold revocability: a stolen access token stays valid for its full
60-minute lifetime and nothing could stop it.

The usual objection to adding revocation is that it costs a database lookup
per request. **That objection does not apply here.** SimpleJWT's
`JWTAuthentication.get_user()` already does `SELECT user WHERE id = ...` on
every authenticated request — it has to, so that deactivating an account
takes effect immediately. Comparing one integer against a row that is
already in memory is free.

So every token carries a `token_version` claim, and `common.authentication`
rejects a token whose claim does not match the user's current value.
Bumping `User.token_version` invalidates every token ever issued to that
user, in one write.

Why the claim goes on the REFRESH token rather than the access token:
`RefreshToken.access_token` copies every claim except `token_type`, `exp`,
`iat` and `jti` (verified against simplejwt 5.5.1). Setting it once on the
refresh token therefore covers the initial access token AND every later one
minted by /api/token/refresh/, so a revoked refresh token cannot mint a
working access token.
"""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

# Claim name. Spelled out rather than abbreviated: these tokens are small,
# and a claim nobody can decode from its name is a claim nobody maintains.
TOKEN_VERSION_CLAIM = "token_version"


def issue_token_pair(user):
    """
    Mint a refresh/access pair carrying the user's current token version.

    The single place a token is stamped. Both issuance paths — password
    login via the serializer below, and Google SSO in
    common/google_auth_views.py — route through here, so they cannot drift
    into a state where one mints revocable tokens and the other does not.
    """
    refresh = RefreshToken.for_user(user)
    refresh[TOKEN_VERSION_CLAIM] = user.token_version
    return refresh


class VersionedTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    The password-login path, stamped.

    `TokenObtainPairSerializer.validate()` calls `self.get_token(user)` and
    builds the response from it, so overriding this one classmethod covers
    login without touching the view, the response shape, or the throttle
    scope attached to it.

    Without this, revocation would be silently inert for every
    password-authenticated user: the column would exist, the check would
    run, and no token would ever carry a claim to check. That failure mode
    — a security control present but never exercised — is the one this
    codebase has hit three times (dead password validators, the inert
    throttle, the non-persisting cache), so it is covered by an explicit
    test rather than trusted.
    """

    @classmethod
    def get_token(cls, user):
        return issue_token_pair(user)
