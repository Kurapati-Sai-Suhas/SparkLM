"""
common.authentication — JWT authentication that can be revoked (M4 Phase A).

See common/tokens.py for why the claim exists and why it is free.

⚠ Back-compatibility is deliberate and load-bearing. A token issued BEFORE
this deploy has no `token_version` claim at all. Rejecting those would log
out every signed-in user at deploy time — turning a security improvement
into an outage. A missing claim is therefore treated as valid, and those
tokens age out naturally within the 60-minute access lifetime (one day for
refresh). Once the window has passed this branch is dead weight and can be
removed; until then, removing it is a self-inflicted mass logout.
"""

import logging

# ⚠ DRF's AuthenticationFailed, NOT the SimpleJWT subclass of the same name.
# SimpleJWT's version mixes in DetailDictMixin, which builds
# {"detail": ..., "code": ...} and puts the code IN THE RESPONSE BODY. Using
# it here shipped `"code": "token_revoked"` to the client — announcing to
# whoever holds a stolen token that the account owner had noticed and acted.
# Caught by test_revocation_does_not_leak_that_the_token_was_revoked.
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from common.tokens import TOKEN_VERSION_CLAIM

logger = logging.getLogger(__name__)


class VersionedJWTAuthentication(JWTAuthentication):
    """
    JWTAuthentication plus a token-version check against the user row that
    the parent class has already fetched.

    A mismatch raises `AuthenticationFailed`, which DRF renders as a plain
    401 with a generic body. The reason is recorded at INFO **server-side
    only**: a caller has no use for "this token was specifically revoked",
    while an attacker holding stolen tokens learns from it that the owner
    noticed and acted.

    (Returning None here would NOT work: SimpleJWT's `authenticate()` calls
    `get_user()` and hands the result straight back, so a None would surface
    as an authenticated request with `request.user = None` rather than a
    401. Raising is the correct signal at this layer.)
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        claimed = validated_token.get(TOKEN_VERSION_CLAIM)
        if claimed is None:
            # Pre-deploy token. See the module docstring before "fixing" this.
            return user

        if claimed != user.token_version:
            logger.info(
                "Rejected revoked token for user=%s (claim=%s, current=%s)",
                user.pk, claimed, user.token_version,
            )
            # Message deliberately matches nothing specific. The diagnostic
            # detail is in the log line above, where only we can read it.
            raise AuthenticationFailed("Given token not valid for any token type")

        return user
