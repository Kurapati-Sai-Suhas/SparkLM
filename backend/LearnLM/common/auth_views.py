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

from django.contrib.auth import get_user_model
from django.db.models import F
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from common.auth_cookies import (
    REFRESH_COOKIE_NAME,
    auth_v2_enabled,
    clear_refresh_cookie,
    has_csrf_sentinel,
    read_refresh_token,
    set_refresh_cookie,
)
from common.throttling import ClientIPScopedRateThrottle
from common.tokens import (
    TOKEN_VERSION_CLAIM,
    VersionedTokenObtainPairSerializer,
    issue_token_pair,
)

logger = logging.getLogger(__name__)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'auth'
    # Stamps each token with the user's current token_version (M4 Phase A).
    # Everything else about this view is unchanged.
    serializer_class = VersionedTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        """
        Login. With AUTH_V2_COOKIES off this is byte-identical to before —
        same body, same headers, no cookie.

        With it on, the refresh token moves into an httpOnly cookie and is
        REMOVED from the response body. Leaving it in both places would be
        the worst of both: script could still read it, so the cookie would
        buy nothing.

        This is why the frontend must ship before the flag flips. The new
        client reads the access token from the body and never touches
        `refresh`, so it works under both settings; the old client reads
        `refresh` from the body and would break. Rollout order is in the
        deployment notes, and the body fallback in read_refresh_token()
        makes the reverse direction safe too.
        """
        response = super().post(request, *args, **kwargs)

        if auth_v2_enabled() and response.status_code == 200:
            # Mutating .data is sufficient and correct: DRF renders the body
            # in finalize_response(), which runs AFTER this method returns.
            # An earlier draft called response.render() here defensively and
            # crashed every login with "accepted_renderer not set" — the
            # renderer is not attached yet at this point.
            refresh = response.data.pop("refresh", None)
            if refresh:
                set_refresh_cookie(response, refresh)

        return response


class CookieTokenRefreshMixin:
    """
    Rotating refresh with replay detection (M5 Phase 4).

    Rotation without replay detection is theatre: the old token stays valid
    for its full remaining lifetime, so a stolen one is just as useful after
    rotation as before. Detection needs somewhere to record "this token has
    been spent", which is what SimpleJWT's token_blacklist app provides.

    ⚠ ROTATE_REFRESH_TOKENS is deliberately NOT set in settings. Setting it
    globally would rotate on the legacy path too — and the existing frontend
    stores `data.access` on refresh but never updates its stored refresh
    token, so every old client would break permanently after one refresh.
    Rotation is bound to the flag, in this view, for that reason alone.

    Three checks, in this order:

    1. Signature/expiry, and blacklist membership — `RefreshToken(raw)`
       raises TokenError for all three.
    2. token_version. NOT redundant: TokenRefreshSerializer never checks it,
       so without this a revoked refresh token would mint a brand-new pair
       stamped with the CURRENT version — silently un-revoking the session
       that `logout-all` had just ended. Pinned by
       test_a_revoked_refresh_token_cannot_mint_a_new_session.
    3. Single-use. `blacklist()` is get_or_create; if the row already
       existed, someone else spent this token first, so this is a replay
       and is refused. The DB unique constraint makes that atomic, which is
       what gives concurrent refreshes exactly one winner.
    """

    def _rotate(self, request):
        raw = read_refresh_token(request)
        if not raw:
            return self._reject("No refresh token provided.")

        try:
            token = RefreshToken(raw)
        except TokenError:
            # Covers expired, tampered, and already-blacklisted.
            return self._reject("Token is invalid or expired.")

        user = self._user_for(token)
        if user is None:
            return self._reject("Token is invalid or expired.")

        claimed = token.get(TOKEN_VERSION_CLAIM)
        if claimed is not None and claimed != user.token_version:
            logger.info("Refresh refused: stale token_version for user=%s", user.pk)
            return self._reject("Token is invalid or expired.")

        if not self._spend(token):
            # Reuse of an already-rotated token. Either a race between two
            # tabs or a stolen token being replayed; we cannot tell them
            # apart, and refusing is correct for both.
            logger.warning("Refresh token reuse detected for user=%s", user.pk)
            return self._reject("Token is invalid or expired.")

        new_refresh = issue_token_pair(user)
        response = Response({"access": str(new_refresh.access_token)})
        set_refresh_cookie(response, str(new_refresh))
        return response

    @staticmethod
    def _user_for(token):
        User = get_user_model()
        return User.objects.filter(pk=token.get("user_id")).first()

    @staticmethod
    def _spend(token):
        """
        Mark the token spent. True if THIS caller spent it.

        Returns False when the blacklist row already existed — the atomic
        signal that another request rotated the same token first.
        """
        try:
            _, created = token.blacklist()
        except AttributeError:
            # token_blacklist app not installed. Rotation still happens but
            # replay cannot be detected; refuse to pretend otherwise.
            logger.error(
                "token_blacklist app is not installed — refresh rotation "
                "cannot detect replay. Add it to INSTALLED_APPS."
            )
            return True
        return created

    @staticmethod
    def _reject(detail):
        response = Response({"detail": detail}, status=401)
        return clear_refresh_cookie(response)


class ThrottledTokenRefreshView(CookieTokenRefreshMixin, TokenRefreshView):
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

    def post(self, request, *args, **kwargs):
        if not auth_v2_enabled():
            # Legacy path. No rotation, no blacklist — exactly the behaviour
            # every currently-deployed client expects.
            #
            # One addition, and only for a case that cannot occur before the
            # flag has been on: a browser that logged in under v2 holds ONLY
            # a cookie, so after a rollback its empty-bodied refresh would
            # 400 and strand the user. Reading the cookie in that case is
            # what makes the rollback claim true rather than aspirational.
            # A genuine legacy client sends a body token and never reaches
            # this branch, so its behaviour is unchanged.
            if not request.data.get("refresh"):
                cookie = request.COOKIES.get(REFRESH_COOKIE_NAME)
                if cookie:
                    serializer = self.get_serializer(data={"refresh": cookie})
                    serializer.is_valid(raise_exception=True)
                    return Response(serializer.validated_data)
            return super().post(request, *args, **kwargs)

        if not has_csrf_sentinel(request):
            # SameSite=None means the browser attaches this cookie to
            # cross-site requests. Requiring a custom header forces a CORS
            # preflight that our origin allowlist answers for, so a POST
            # from an attacker's page never reaches the rotation logic.
            return Response({"detail": "Missing client header."}, status=403)

        return self._rotate(request)


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
        response = Response({"detail": "All sessions signed out."})
        # Also drop this browser's cookie (M5 Phase 4). The version bump
        # already makes it useless, but leaving it set means the next
        # refresh sends a dead credential and gets a 401 that looks like a
        # fault rather than a completed logout.
        return clear_refresh_cookie(response)


class LogoutView(APIView):
    """
    POST /api/auth/logout/ — end THIS session (M5 Phase 4).

    Required by the architecture rather than added as a feature: an
    httpOnly cookie cannot be removed by client script, so without a
    server endpoint "log out" would leave a live refresh token in the
    browser. The pre-v2 client cleared localStorage itself; that option
    disappears the moment the token stops being readable.

    Distinct from logout-all, which bumps token_version and ends every
    session on every device. This one spends a single refresh token, so
    signing out of a library computer does not sign you out of your phone.

    AllowAny deliberately: logging out must work even when the access
    token has already expired, which is precisely when a user reaches for
    it. Authorization comes from possessing the refresh token, and the
    endpoint reveals nothing — it answers 200 either way.
    """

    permission_classes = [AllowAny]
    # No authenticator. AllowAny alone is NOT enough: the default
    # VersionedJWTAuthentication still runs and raises AuthenticationFailed
    # on a malformed or expired Bearer token BEFORE permissions are
    # consulted, so logout returned 401 exactly when it was most needed.
    # Authorization here comes from possessing the refresh token, not from
    # a live access token. Pinned by
    # test_logout_works_without_a_valid_access_token.
    authentication_classes = []
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'auth-refresh'

    def post(self, request):
        raw = read_refresh_token(request)
        if raw:
            try:
                token = RefreshToken(raw)
                token.blacklist()
            except (TokenError, AttributeError):
                # Already expired, already spent, or the blacklist app is
                # absent. Nothing to do, and nothing worth telling the
                # caller — the cookie is cleared regardless.
                pass

        # Always 200, always clear. A logout that reports failure invites
        # the client to leave the credential in place and retry.
        response = Response({"detail": "Signed out."})
        return clear_refresh_cookie(response)
