"""
Why did Judge0 refuse? (Phase 1 M10)

PURE. Classification over an HTTP status and a response body — no requests, no
ORM, no I/O. The caller does the network; this decides what the answer means.

── Why this exists ─────────────────────────────────────────────────────────

Every Judge0 failure reached the platform as one string:

    {"error": "Judge0 request failed: 403 Client Error: Forbidden for url: …"}

which becomes `GradingUnavailable`, a 503, and an operator with nothing to act
on. Four milestones reported "Judge0 is down (429/403)" and inferred quota
exhaustion from the status code alone. The M10 diagnostic showed that was
wrong: RapidAPI answers a read with

    403 {"message":"You are not subscribed to this API."}

and a write with 429 "Too many requests" — the 429 being a CONSEQUENCE of
unsubscribed access, not an independent quota story. Quota and subscription
have completely different remedies (wait vs. pay), and a status code cannot
tell them apart. The provider's own words can.

── The categories ──────────────────────────────────────────────────────────

Each names the ACTION it implies, because that is the only reason to
distinguish them:

    AUTHENTICATION      the key is missing or rejected -> fix credentials
    NOT_SUBSCRIBED      the key is valid, the plan is not active -> subscribe
    QUOTA               the plan's allowance is spent -> wait or upgrade
    RATE_LIMITED        too fast right now -> back off
    MALFORMED_REQUEST   we sent something invalid -> a bug HERE, not there
    PROVIDER_UNAVAILABLE  their side is broken -> retry later
    EXECUTION_FAILURE   Judge0 ran it and it failed -> the learner's problem
    UNKNOWN             unclassified; the raw detail is carried through

`UNKNOWN` is deliberate. A classifier that forces every response into a known
bucket is how "not subscribed" spent four milestones being called "quota".
"""

AUTHENTICATION = "authentication"
NOT_SUBSCRIBED = "not_subscribed"
QUOTA = "quota"
RATE_LIMITED = "rate_limited"
MALFORMED_REQUEST = "malformed_request"
PROVIDER_UNAVAILABLE = "provider_unavailable"
EXECUTION_FAILURE = "execution_failure"
UNKNOWN = "unknown"

#: What an operator should do about each category. Carried with the failure so
#: the remedy travels with the diagnosis instead of living in a report.
REMEDY = {
    AUTHENTICATION: "check JUDGE0_API_KEY — the provider rejected it",
    NOT_SUBSCRIBED: ("the key is accepted but the account has no active "
                     "subscription to this Judge0 product; subscribe on "
                     "RapidAPI, or point JUDGE0_URL at a self-hosted Judge0"),
    QUOTA: "the plan's allowance is spent; wait for the reset or upgrade",
    RATE_LIMITED: "requests are arriving too fast; back off and retry later",
    MALFORMED_REQUEST: ("the request this platform sent was rejected as "
                        "invalid — a defect here, not at the provider"),
    PROVIDER_UNAVAILABLE: "the provider is failing; retry later",
    EXECUTION_FAILURE: "Judge0 ran the submission and it failed",
    UNKNOWN: "unclassified; see the raw detail",
}

#: Phrases the provider actually uses. Matched case-insensitively against the
#: response body, and checked BEFORE the status code, because the body is the
#: only thing that can separate a subscription problem from a quota problem —
#: RapidAPI returns 403 for both.
_BODY_SIGNALS = (
    ("not subscribed", NOT_SUBSCRIBED),
    ("invalid api key", AUTHENTICATION),
    ("missing api key", AUTHENTICATION),
    ("not authorized", AUTHENTICATION),
    ("exceeded the monthly limit", QUOTA),
    ("exceeded the rate limit", RATE_LIMITED),
    ("quota", QUOTA),
    ("too many requests", RATE_LIMITED),
)

_STATUS_FALLBACK = {
    400: MALFORMED_REQUEST,
    401: AUTHENTICATION,
    403: AUTHENTICATION,
    422: MALFORMED_REQUEST,
    429: RATE_LIMITED,
    500: PROVIDER_UNAVAILABLE,
    502: PROVIDER_UNAVAILABLE,
    503: PROVIDER_UNAVAILABLE,
    504: PROVIDER_UNAVAILABLE,
}


def classify(status_code=None, body="", transport_error=None):
    """
    (category, detail) for one Judge0 response.

    `transport_error` is for the case where no HTTP response arrived at all —
    a connection reset or a timeout is the provider being unavailable, which
    is a different fact from the provider refusing us.
    """
    if transport_error is not None:
        return PROVIDER_UNAVAILABLE, str(transport_error)[:300]

    text = (body or "")
    lowered = text.lower()

    # Body first. A 403 alone cannot distinguish "not subscribed" from
    # "quota", and those have opposite remedies.
    for phrase, category in _BODY_SIGNALS:
        if phrase in lowered:
            return category, text[:300]

    if status_code in _STATUS_FALLBACK:
        return _STATUS_FALLBACK[status_code], text[:300] or f"HTTP {status_code}"

    if status_code is not None and 200 <= status_code < 300:
        return EXECUTION_FAILURE, text[:300]

    return UNKNOWN, (text[:300] or f"HTTP {status_code}")


def describe(category, detail=""):
    """One operator-readable line: what happened, and what to do about it."""
    remedy = REMEDY.get(category, REMEDY[UNKNOWN])
    suffix = f" — {detail}" if detail else ""
    return f"Judge0 {category}: {remedy}{suffix}"


def is_retryable(category):
    """
    Whether waiting could plausibly help.

    A subscription does not appear by waiting, and neither does a fix for a
    malformed request. Saying so lets a caller avoid a retry that can only
    burn the allowance it does not have.
    """
    return category in (RATE_LIMITED, PROVIDER_UNAVAILABLE, QUOTA)
