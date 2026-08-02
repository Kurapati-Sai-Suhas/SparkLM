"""
common.google_auth_views — "Sign in with Google" (M9, first slice).

Frontend (Google Identity Services) obtains a Google-signed ID token
identifying the user and POSTs it here. This view verifies the token's
signature and audience directly against Google's public keys — the
frontend is never trusted to assert who the user is — then get-or-creates
a local account and issues our own JWT pair, so the rest of the app
(including every existing IsAuthenticated view) needs no Google-awareness
at all.

Accounts created this way get an unusable password (Django's
set_unusable_password): they authenticate only through Google, so there
is no local secret to guess, leak, or brute-force. If a Google email
matches an EXISTING locally-registered account, that account is reused
as-is (email is the join key) — the user can thereafter sign in either
way.
"""

import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from common.throttling import ClientIPScopedRateThrottle
from common.tokens import issue_token_pair
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

User = get_user_model()

# Usernames must satisfy Django's UnicodeUsernameValidator (letters,
# digits, and @/./+/-/_ only); an email local-part can contain other
# characters (e.g. quoted strings, rare punctuation) that would fail it.
_USERNAME_SANITIZE_RE = re.compile(r"[^\w.@+-]")


def _unique_username_from_email(email: str) -> str:
    base = _USERNAME_SANITIZE_RE.sub("", email.split("@")[0]).lower() or "user"
    base = base[:140]  # leave room for a numeric suffix under the 150-char field limit
    candidate = base
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


class GoogleAuthView(APIView):
    """
    POST /api/auth/google/  { "credential": "<Google ID token (JWT)>" }
    -> { "access": ..., "refresh": ..., "created": bool }

    Same throttle scope as password login: this endpoint is just another
    way to obtain a token pair, so it must not become an unthrottled
    side-door around the brute-force brake.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ClientIPScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        credential = request.data.get("credential")
        if not credential:
            return Response({"error": "credential is required"}, status=400)

        if not settings.GOOGLE_CLIENT_ID:
            # Fails loudly rather than silently accepting tokens with no
            # audience check — misconfiguration must not look like success.
            logger.error("GoogleAuthView called but GOOGLE_CLIENT_ID is not configured")
            return Response({"error": "Google sign-in is not configured on this server"}, status=503)

        try:
            payload = google_id_token.verify_oauth2_token(
                credential, google_requests.Request(), settings.GOOGLE_CLIENT_ID
            )
        except ValueError as exc:
            # Covers: bad signature, expired token, audience/issuer mismatch.
            logger.info("Google ID token verification failed: %s", exc)
            return Response({"error": "Invalid Google credential"}, status=401)

        email = payload.get("email")
        if not email:
            return Response({"error": "Google account has no email"}, status=400)
        if not payload.get("email_verified", False):
            return Response({"error": "Google email is not verified"}, status=401)

        with transaction.atomic():
            user = User.objects.filter(email__iexact=email).first()
            created = False
            if user is None:
                user = User.objects.create(
                    username=_unique_username_from_email(email),
                    email=email.lower(),
                )
                # No local password exists for a Google-only account —
                # set_unusable_password ensures the password-login path
                # (and any password check) correctly rejects it rather
                # than comparing against an empty/default hash.
                user.set_unusable_password()
                user.save(update_fields=["password"])
                created = True

        # Routed through common.tokens so SSO tokens carry the same
        # token_version claim as password logins (M4 Phase A). If this ever
        # goes back to RefreshToken.for_user directly, SSO accounts silently
        # become unrevocable — pinned by
        # test_google_login_tokens_are_revocable.
        refresh = issue_token_pair(user)
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "created": created,
        })
