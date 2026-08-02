"""
common.auth_views — throttled JWT endpoints.

First resident of the v2 `common` module (frozen architecture §9: new code
lands in v2 modules; `groups` is frozen legacy).

The token obtain/refresh endpoints previously rode the general anonymous
rate (30/min), which is comfortable for credential stuffing. The dedicated
'auth' scope (10/min, configured in settings.REST_FRAMEWORK) is keyed by IP
for anonymous callers and acts as the brute-force brake required by the
frozen architecture §7/§13.7 without affecting any authenticated flow.
"""

import logging

from django.db.models import F
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from common.throttling import ClientIPScopedRateThrottle
from common.tokens import VersionedTokenObtainPairSerializer

logger = logging.getLogger(__name__)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'auth'
    # Stamps each token with the user's current token_version (M4 Phase A).
    # Everything else about this view is unchanged.
    serializer_class = VersionedTokenObtainPairSerializer


class ThrottledTokenRefreshView(TokenRefreshView):
    # Separate bucket from login: refresh presents an existing token, so it
    # is not a credential-guessing surface, and behind shared-IP NATs the
    # hourly refresh traffic of many users must never starve sign-ins.
    #
    # No serializer override needed: refresh mints an access token from the
    # presented refresh token, and SimpleJWT copies every claim except
    # token_type/exp/iat/jti (RefreshToken.access_token, verified against
    # 5.5.1). The version claim therefore rides along automatically, which
    # is why the claim is stamped on the refresh token rather than the
    # access token — see common/tokens.py.
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'auth-refresh'


class LogoutAllView(APIView):
    """
    POST /api/auth/logout-all/ — revoke every token issued to the caller.

    The trigger the revocation primitive was missing. `token_version` and
    the authentication check are useless on their own: something has to be
    able to bump the number, or the column is decoration. This is that
    something, plus the admin action in groups/admin.py for the case where
    the account holder cannot act (compromise, offboarding).

    Uses `F('token_version') + 1` rather than a read-modify-write so two
    concurrent revocations cannot both read the same value and collapse into
    one increment. The exact resulting number is irrelevant — only that it
    differs from what every outstanding token carries — but an atomic
    increment is the same amount of code as a racy one.

    Requires authentication, which means the caller must present a token
    that is still valid. That is deliberate: this endpoint ends *your own*
    sessions. Ending someone else's is an admin action, not an API call.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        User = type(request.user)
        User.objects.filter(pk=request.user.pk).update(
            token_version=F("token_version") + 1
        )
        logger.info("All tokens revoked for user=%s", request.user.pk)
        # 200 rather than 204: the client should know the call did something,
        # and the next request with the old token will 401 — which would
        # otherwise look like a bug rather than the intended effect.
        return Response({"detail": "All sessions signed out."})
