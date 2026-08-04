"""
The CORS contract between the SPA and the API (production incident, M5).

Auth v2 added `X-SparkLM-Client: web` to the SHARED axios instance, because
the refresh and logout endpoints require it as their CSRF control —
SameSite=None means the browser attaches the refresh cookie to cross-site
requests, so a custom header is what forces a preflight an attacker's page
cannot satisfy.

Being on the shared instance means it rides on EVERY request. It was never
added to django-cors-headers' allow-list, whose defaults are:

    accept, authorization, content-type, user-agent, x-csrftoken,
    x-requested-with

The preflight answered 200 while omitting the header from
Access-Control-Allow-Headers, so the browser blocked every real cross-origin
request. Login broke. The login endpoint itself was healthy throughout,
which is exactly why it presented as an auth bug rather than a CORS bug —
the failure is in the browser, and the server logs show nothing wrong.

No unit test could have caught it: the Django test client does not enforce
CORS, and every backend test passed. This file closes that gap by asserting
the contract itself — that any header the frontend is configured to send is
one the backend is configured to allow.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

REPO = Path(__file__).resolve().parents[3]
API_CLIENT = REPO / "studysphere-ai-11" / "src" / "services" / "api.js"

# The header Auth v2's CSRF control depends on.
CLIENT_HEADER = "x-sparklm-client"


def test_the_client_header_is_allowed_by_cors():
    """
    The exact regression. Without this the browser blocks every request from
    the deployed SPA, and the API looks perfectly healthy from curl.
    """
    allowed = {h.lower() for h in settings.CORS_ALLOW_HEADERS}

    assert CLIENT_HEADER in allowed, (
        f"{CLIENT_HEADER!r} is missing from CORS_ALLOW_HEADERS. The SPA sends "
        f"it on every request, so the browser will block ALL cross-origin "
        f"calls — including login."
    )


def test_the_defaults_are_extended_not_replaced():
    """
    `authorization` and `content-type` are load-bearing. Replacing the
    default tuple instead of extending it would break every authenticated
    request while leaving the custom header working.
    """
    allowed = {h.lower() for h in settings.CORS_ALLOW_HEADERS}

    for required in ("authorization", "content-type", "accept"):
        assert required in allowed, f"{required!r} was dropped from CORS_ALLOW_HEADERS"


def test_credentials_are_enabled_for_the_refresh_cookie():
    """httpOnly refresh cookies are not sent cross-origin without this."""
    assert settings.CORS_ALLOW_CREDENTIALS is True


def test_every_custom_header_the_frontend_sends_is_allowed():
    """
    Guards the class, not the instance. Scans the shared API client for
    custom `X-*` headers and asserts each is in the allow-list, so the next
    header added to axios cannot silently break the browser contract.
    """
    if not API_CLIENT.exists():
        pytest.skip("frontend api.js not present")

    source = API_CLIENT.read_text(encoding="utf-8")
    # Header keys as written in the axios `headers` object literal.
    declared = {
        m.group(1).lower()
        for m in re.finditer(r'["\'](X-[A-Za-z0-9-]+)["\']\s*:', source)
    }
    allowed = {h.lower() for h in settings.CORS_ALLOW_HEADERS}

    missing = sorted(declared - allowed)
    assert not missing, (
        f"The SPA sends {missing} but CORS_ALLOW_HEADERS does not permit "
        f"them. The browser will block every cross-origin request carrying "
        f"one of these."
    )


@pytest.mark.django_db
def test_a_preflight_carrying_the_client_header_is_answered(settings):
    """
    End-to-end through the middleware, mirroring what the browser actually
    asks: an OPTIONS preflight naming the custom header must come back with
    that header echoed in Access-Control-Allow-Headers.
    """
    origin = "https://spark-lm-3y3e.vercel.app"
    settings.CORS_ALLOWED_ORIGINS = [origin]

    response = Client().options(
        "/api/token/",
        HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=f"content-type,{CLIENT_HEADER}",
    )

    echoed = response.headers.get("Access-Control-Allow-Headers", "").lower()
    assert CLIENT_HEADER in echoed, (
        f"preflight did not permit {CLIENT_HEADER!r}; browser would block "
        f"the request. Got: {echoed!r}"
    )
    assert response.headers.get("Access-Control-Allow-Origin") == origin
