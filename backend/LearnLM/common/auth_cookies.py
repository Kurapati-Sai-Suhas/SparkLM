"""
common.auth_cookies — refresh-token transport for Auth v2 (M5 Phase 4).

The problem
-----------
Both tokens live in `localStorage`. Any XSS on the SPA reads them and walks
away with a refresh token good for a full day, renewable indefinitely.
M4's CSP removed most delivery vectors for that XSS and `token_version`
bounded the damage once noticed — but the token is still *readable by
script*, which is the property that matters.

The fix is transport, not cryptography: move the refresh token into a
cookie the page's JavaScript cannot read, and keep the access token in a
module-scoped variable that dies with the tab. An XSS can still call the
API as the user while it runs; it can no longer steal a durable credential
and keep using it after the page closes.

Why SameSite=None
-----------------
Not a weakening — a requirement. The SPA is on Vercel and the API is on
Render, so every request is cross-site by the browser's definition and
`SameSite=Lax` would simply never send the cookie. `None` is the only value
that works for this topology, which is exactly why the CSRF sentinel below
is mandatory rather than optional.

Everything here is inert unless AUTH_V2_COOKIES is on. With the flag off,
not a single response header changes.
"""

from django.conf import settings

REFRESH_COOKIE_NAME = "sparklm_refresh"

# Any header a browser will not attach to a cross-site form/img/script
# request. Requiring one forces a CORS preflight, which our origin
# allowlist then answers for — so a POST from evil.com never reaches the
# handler even though the browser would happily attach a SameSite=None
# cookie to it. This is the CSRF control for the refresh and logout
# endpoints; see test_csrf_style_request_without_the_sentinel_is_rejected.
CSRF_SENTINEL_HEADER = "HTTP_X_SPARKLM_CLIENT"
CSRF_SENTINEL_VALUE = "web"


def auth_v2_enabled():
    """
    The single switch. Read at call time, never cached, so the flag can be
    flipped by settings override in tests and by env var in production
    without a code path that remembers the old value.
    """
    return bool(getattr(settings, "AUTH_V2_COOKIES", False))


def refresh_cookie_kwargs():
    """
    Cookie attributes, in one place so the login, SSO and refresh paths
    cannot set them differently.

    `secure` follows the flag rather than being hardcoded True only so the
    test client and local HTTP development work; it is True in every
    deployed configuration because AUTH_COOKIE_SECURE defaults to
    `not DEBUG`.
    """
    return {
        "httponly": True,          # the whole point: script cannot read it
        "secure": bool(getattr(settings, "AUTH_COOKIE_SECURE", not settings.DEBUG)),
        "samesite": getattr(settings, "AUTH_COOKIE_SAMESITE", "None"),
        # Scoped to the API prefix rather than "/". The cookie is only ever
        # needed by /api/token/refresh/ and the logout endpoints; a narrower
        # path is not possible because those live under different segments.
        "path": getattr(settings, "AUTH_COOKIE_PATH", "/api/"),
    }


def set_refresh_cookie(response, refresh_token):
    """Attach the refresh token as an httpOnly cookie."""
    lifetime = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        str(refresh_token),
        max_age=int(lifetime.total_seconds()),
        **refresh_cookie_kwargs(),
    )
    return response


def clear_refresh_cookie(response):
    """
    Remove the cookie.

    The attributes must match those it was set with or the browser treats
    it as a different cookie and leaves the original in place — a logout
    that appears to work and doesn't. `delete_cookie` needs path and
    samesite explicitly for the same reason.
    """
    kwargs = refresh_cookie_kwargs()
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=kwargs["path"],
        samesite=kwargs["samesite"],
    )
    return response


def read_refresh_token(request):
    """
    The presented refresh token: cookie first, request body second.

    The body fallback is what makes the rollout survivable in both
    directions. A client that logged in before the flag was flipped still
    holds a localStorage refresh token and keeps working; a client that
    logged in after the flag is flipped back off still has its cookie and
    keeps working. Neither direction logs anyone out.

    Cookie takes precedence: once a browser has one, that is the
    authoritative credential, and a body value alongside it would let a
    caller choose which of two tokens to present.
    """
    cookie = request.COOKIES.get(REFRESH_COOKIE_NAME)
    if cookie:
        return cookie
    data = getattr(request, "data", None) or {}
    return data.get("refresh")


def has_csrf_sentinel(request):
    """
    True when the request carries the custom header that proves it came
    from our own fetch/XHR rather than from a cross-site form or image.
    """
    return request.META.get(CSRF_SENTINEL_HEADER) == CSRF_SENTINEL_VALUE
